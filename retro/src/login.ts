// Форма входа. Грузится лениво — только когда вход действительно нужен.
// Три способа: логин и пароль «Сетевого города», Госуслуги (с кодом из SMS
// или «Макс») и QR-код Госуслуг. Школа выбирается своим списком с поиском.
import { api, ApiError, forgetWeeks } from "./api";
import { state } from "./state";
import type { Me } from "./types";
import { $, esc, store } from "./util";

type Method = "password" | "esia" | "qr";

interface School {
  id: number;
  name: string;
  full?: string;
  label?: string;
}

interface Prefs {
  url?: string;
  school?: School | string;
  login?: string;
  esiaLogin?: string;
  method?: Method;
}

/** Событие входа через Госуслуги — что сервер ждёт от нас дальше. */
interface EsiaEvent {
  state: "wait" | "code" | "choose" | "qr" | "scanned" | "done" | "error";
  flow?: string;
  hint?: string;
  length?: number;
  phone?: string;
  options?: string[];
  qr?: string;
  message?: string;
  me?: Me;
}

let ready = false;
let method: Method = "password";
let chosen: School | null = null;
let options: School[] = [];
let active = -1;
let flow = "";
let onDone: () => void = () => {};

const field = (id: string) => $<HTMLInputElement>(`#${id}`);
const url = () => field("url").value.trim().replace(/\/+$/, "");
const error = (text: string) => ($("#loginErr").textContent = text);

// ---------- школа: свой выпадающий список ----------

function recent(): School[] {
  return store.get<Record<string, School[]>>("recentSchools", {})[url()] ?? [];
}

function remember(school: School): void {
  const all = store.get<Record<string, School[]>>("recentSchools", {});
  all[url()] = [school, ...(all[url()] ?? []).filter((s) => s.id !== school.id)].slice(0, 5);
  store.set("recentSchools", all);
}

function drawOptions(list: School[], heading = ""): void {
  options = list;
  active = list.length ? 0 : -1;
  const box = $("#schoolOptions");
  box.innerHTML =
    (heading ? `<li class="combo-head">${esc(heading)}</li>` : "") +
    list
      .map(
        (s, i) => `<li role="option" id="school-${i}" data-index="${i}" class="${i === active ? "on" : ""}">
          <b>${esc(s.name)}</b>${s.label && s.label !== "школа" ? ` <span class="tag">${esc(s.label)}</span>` : ""}
          ${s.full ? `<small>${esc(s.full)}</small>` : ""}</li>`,
      )
      .join("");
  const open = list.length > 0 || !!heading;
  box.classList.toggle("hidden", !open);
  field("school").setAttribute("aria-expanded", String(open));
}

function highlight(index: number): void {
  active = Math.max(0, Math.min(options.length - 1, index));
  for (const li of document.querySelectorAll<HTMLElement>("#schoolOptions [data-index]"))
    li.classList.toggle("on", Number(li.dataset.index) === active);
  document.querySelector(`#school-${active}`)?.scrollIntoView({ block: "nearest" });
  field("school").setAttribute("aria-activedescendant", `school-${active}`);
}

function pick(school: School): void {
  chosen = school;
  field("school").value = school.name;
  $("#schoolNote").textContent = "";
  close();
}

function close(): void {
  $("#schoolOptions").classList.add("hidden");
  field("school").setAttribute("aria-expanded", "false");
}

let searchTimer: ReturnType<typeof setTimeout> | undefined;
let searchSeq = 0;

function search(): void {
  clearTimeout(searchTimer);
  const q = field("school").value.trim();
  if (!q) {
    const last = recent();
    if (last.length) drawOptions(last, "Недавние");
    else close();
    return;
  }
  if (!/^https?:\/\//.test(url())) {
    drawOptions([], "Сначала выбери регион");
    return;
  }
  searchTimer = setTimeout(async () => {
    const seq = ++searchSeq;
    $("#schoolNote").textContent = "ищу…";
    try {
      const list = await api<School[]>(
        `/api/schools?url=${encodeURIComponent(url())}&q=${encodeURIComponent(q)}`,
      );
      if (seq !== searchSeq) return; // пришёл ответ на старый запрос
      $("#schoolNote").textContent = "";
      drawOptions(list, list.length ? "" : "Ничего не нашлось — попробуй часть названия или номер");
    } catch (err) {
      if (seq === searchSeq)
        $("#schoolNote").textContent = err instanceof ApiError ? err.message : "сервер школ не отвечает";
    }
  }, 200);
}

function initSchoolPicker(): void {
  const input = field("school");
  input.addEventListener("input", () => {
    chosen = null;
    search();
  });
  input.addEventListener("focus", search);
  input.addEventListener("blur", () => setTimeout(close, 150));
  input.addEventListener("keydown", (e) => {
    const open = !$("#schoolOptions").classList.contains("hidden");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) search();
      else highlight(active + 1);
    } else if (e.key === "ArrowUp" && open) {
      e.preventDefault();
      highlight(active - 1);
    } else if (e.key === "Enter" && open && options[active]) {
      e.preventDefault();
      pick(options[active]);
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      close();
    }
  });
  // mousedown, а не click: иначе поле успеет потерять фокус и список закроется.
  $("#schoolOptions").addEventListener("mousedown", (e) => {
    const li = (e.target as HTMLElement).closest<HTMLElement>("[data-index]");
    if (!li) return;
    e.preventDefault();
    pick(options[Number(li.dataset.index)]);
  });
}

// ---------- способ входа ----------

function setMethod(next: Method): void {
  method = next;
  for (const tab of document.querySelectorAll<HTMLElement>("#loginTabs [data-method]")) {
    tab.classList.toggle("on", tab.dataset.method === next);
    tab.setAttribute("aria-selected", String(tab.dataset.method === next));
  }
  for (const block of document.querySelectorAll<HTMLElement>("#loginForm [data-for]"))
    block.classList.toggle("hidden", block.dataset.for !== next);
  $("#loginBtn").textContent = next === "qr" ? "Показать QR-код" : "Войти";
  void cancelFlow();
  error("");
}

async function checkMethods(): Promise<void> {
  if (!/^https?:\/\//.test(url())) return;
  const m = await api<{ password: boolean; esia: boolean; esia_main: boolean }>(
    `/api/login/methods?url=${encodeURIComponent(url())}`,
  ).catch(() => null);
  if (!m) return;
  for (const tab of document.querySelectorAll<HTMLButtonElement>("#loginTabs [data-method]")) {
    const off = tab.dataset.method === "password" ? !m.password : !m.esia;
    tab.disabled = off;
    tab.title = off ? "На сервере этого региона такой вход выключен" : "";
  }
  if (m.esia_main && method === "password") setMethod("esia");
  if (!m.esia && method !== "password") setMethod("password");
}

// ---------- вход через Госуслуги: шаги ----------

let answer: ((value: string | number) => void) | null = null;

function step(html: string): void {
  const box = $("#esiaStep");
  box.innerHTML = html;
  box.classList.toggle("hidden", !html);
}

async function drawQr(data: string): Promise<void> {
  const { default: qrcode } = await import("qrcode-generator");
  const qr = qrcode(0, "M");
  qr.addData(data);
  qr.make();
  step(`<div class="qr">${qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true })}</div>
    <p class="hint">Отсканируй камерой в приложении «Госуслуги». Жду…</p>
    <a class="btn" href="${esc(data)}">Открыть Госуслуги</a>`);
}

function waitAnswer(): Promise<string | number> {
  return new Promise((resolve) => {
    answer = (value) => {
      if (value === "") return; // пустой код не отправляем
      answer = null;
      resolve(value);
    };
  });
}

async function runFlow(first: EsiaEvent): Promise<void> {
  let event = first;
  let shownQr = "";
  for (;;) {
    if (!event.flow && event.state !== "done" && event.state !== "error") return;
    if (event.flow) flow = event.flow;
    let reply: string | number | undefined;
    switch (event.state) {
      case "done":
        flow = "";
        step("");
        if (event.me) finish(event.me);
        return;
      case "error":
        flow = "";
        step("");
        error(event.message || "Не получилось войти");
        return;
      case "code":
        step(`<label>${esc(event.hint || "Код подтверждения")}${event.phone ? ` (${esc(event.phone)})` : ""}
            <input id="esiaCode" inputmode="numeric" autocomplete="one-time-code" maxlength="${event.length || 8}" /></label>
          <button class="btn" type="button" id="esiaCodeBtn">Подтвердить</button>`);
        field("esiaCode").focus();
        reply = await waitAnswer();
        step(`<p class="hint">Проверяю код…</p>`);
        break;
      case "choose":
        step(`<p><b>К Госуслугам привязано несколько организаций — выбери:</b></p>
          <div class="choose">${(event.options ?? [])
            .map((o, i) => `<button class="btn" type="button" data-choose="${i}">${esc(o)}</button>`)
            .join("")}</div>`);
        reply = await waitAnswer();
        step(`<p class="hint">Вхожу…</p>`);
        break;
      case "qr":
        if (event.qr && event.qr !== shownQr) {
          shownQr = event.qr;
          await drawQr(event.qr);
        }
        break;
      case "scanned":
        step(`<p class="hint">QR-код отсканирован — подтверди вход в приложении «Госуслуги».</p>`);
        break;
      default:
        if (!shownQr)
          step(`<p class="hint">Жду ответа Госуслуг… Если пришёл пуш в «Госключ» — подтверди его.</p>`);
    }
    if (!flow) return; // вход отменили, пока ждали
    event = await api<EsiaEvent>("/api/login/step", {
      method: "POST",
      body: JSON.stringify({ flow, answer: reply ?? null }),
    }).catch((err: unknown) => ({
      state: "error" as const,
      message: err instanceof Error ? err.message : "",
    }));
  }
}

async function cancelFlow(): Promise<void> {
  if (!flow) return;
  const old = flow;
  flow = "";
  answer = null;
  step("");
  await api("/api/login/cancel", { method: "POST", body: JSON.stringify({ flow: old }) }).catch(() => {});
}

// ---------- общее ----------

function finish(me: Me): void {
  state.me = me;
  store.set("loginPrefs", {
    url: url(),
    school: chosen ?? undefined,
    login: field("login").value.trim() || undefined,
    esiaLogin: field("esiaLogin").value.trim() || undefined,
    method,
  } satisfies Prefs);
  if (chosen) remember(chosen);
  store.remove("demo");
  state.demo = false;
  forgetWeeks();
  field("password").value = "";
  field("esiaPassword").value = "";
  error("");
  onDone();
}

async function submit(): Promise<void> {
  if (!/^https?:\/\//.test(url())) return void error("Выбери регион или укажи адрес сервера");
  if (method === "password" && !chosen) {
    field("school").focus();
    search();
    return void error("Выбери школу из списка");
  }
  const button = $<HTMLButtonElement>("#loginBtn");
  button.disabled = true;
  await cancelFlow();
  try {
    if (method === "password") {
      const login = field("login").value.trim();
      const password = field("password").value;
      if (!login || !password) return void error("Нужны логин и пароль");
      error("Входим…");
      const me = await api<Me>("/api/login", {
        method: "POST",
        body: JSON.stringify({ url: url(), school: String(chosen?.id), login, password }),
      });
      finish(me);
    } else {
      const body = {
        url: url(),
        school: chosen?.name ?? field("school").value.trim(),
        login: field("esiaLogin").value.trim(),
        password: field("esiaPassword").value,
      };
      if (method === "esia" && (!body.login || !body.password))
        return void error("Нужны логин и пароль от Госуслуг");
      error("");
      step(`<p class="hint">Связываюсь с Госуслугами…</p>`);
      const first = await api<EsiaEvent>(method === "esia" ? "/api/login/esia" : "/api/login/qr", {
        method: "POST",
        body: JSON.stringify(body),
      });
      void runFlow(first);
    }
  } catch (err) {
    step("");
    error((err as Error).message);
  } finally {
    button.disabled = false;
  }
}

export async function initLogin(onLoggedIn: () => void, onDemo: () => void): Promise<void> {
  onDone = onLoggedIn;
  if (ready) return;
  ready = true;

  const select = $<HTMLSelectElement>("#region");
  const regions = await api<Record<string, string>>("/api/regions").catch(() => ({}));
  select.innerHTML =
    `<option value="">— свой адрес —</option>` +
    Object.entries(regions)
      .map(([name, address]) => `<option value="${esc(address)}">${esc(name)}</option>`)
      .join("");

  const prefs = store.get<Prefs>("loginPrefs", {});
  if (prefs.url) {
    field("url").value = prefs.url;
    select.value = prefs.url;
  }
  if (prefs.school && typeof prefs.school === "object") pick(prefs.school);
  if (prefs.login) field("login").value = prefs.login;
  if (prefs.esiaLogin) field("esiaLogin").value = prefs.esiaLogin;

  initSchoolPicker();
  const changeServer = () => {
    chosen = null;
    field("school").value = "";
    void checkMethods();
  };
  select.addEventListener("change", () => {
    if (select.value) field("url").value = select.value;
    changeServer();
  });
  field("url").addEventListener("change", changeServer);

  $("#loginTabs").addEventListener("click", (e) => {
    const tab = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-method]");
    if (tab && !tab.disabled) setMethod(tab.dataset.method as Method);
  });
  $("#esiaStep").addEventListener("click", (e) => {
    const target = e.target as HTMLElement;
    if (target.id === "esiaCodeBtn") answer?.(field("esiaCode").value.trim());
    else if (target.dataset.choose) answer?.(Number(target.dataset.choose));
  });
  $("#esiaStep").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.target as HTMLElement).id === "esiaCode") {
      e.preventDefault();
      answer?.(field("esiaCode").value.trim());
    }
  });
  $<HTMLFormElement>("#loginForm").addEventListener("submit", (e) => {
    e.preventDefault();
    void submit();
  });
  $("#demoBtn").addEventListener("click", onDemo);

  setMethod(prefs.method ?? "password");
  void checkMethods();
}
