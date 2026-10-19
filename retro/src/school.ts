// «Почта» и «Отчёты». Модуль грузится лениво — при первом переходе на эти страницы.
import { api, ApiError } from "./api";
import { state } from "./state";
import type { MailMessage, MailPage, Performance, Person, PeriodTotal, Totals } from "./types";
import { $, esc, icon } from "./util";

const errorText = (error: unknown) =>
  error instanceof ApiError ? error.message : "Сервер не ответил, попробуй ещё раз";
const DEMO = `<p class="hint">В демо-режиме почты и отчётов нет — войди через «Сетевой город».</p>`;

function when(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleString("ru", {
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Общее окно (то же, что у урока): заголовок, содержимое и свои кнопки. */
function openWindow(title: string, body: string, extra = ""): void {
  $("#lessonTitle").innerHTML = title;
  $("#lessonBody").innerHTML = body;
  $("#winExtra").innerHTML = extra;
  $("#lessonWin").classList.remove("hidden");
  $("#lessonOk").focus();
}

const closeWindow = () => $("#lessonWin").classList.add("hidden");

async function refreshBadge(): Promise<void> {
  const { count } = await api<{ count: number }>("/api/mail/unread").catch(() => ({ count: 0 }));
  $("#mailBadge").textContent = String(count);
  $("#mailBadge").classList.toggle("hidden", !count);
}

// =====================================================================
// Почта
// =====================================================================

let folder = "Inbox";
let page = 1;
let people: Person[] | null = null;
let chosen: Person[] = [];
let ready = false;

function initMail(): void {
  if (ready) return;
  ready = true;

  $("#mailFolders").addEventListener("click", (e) => {
    const link = (e.target as HTMLElement).closest<HTMLElement>("[data-folder]");
    if (!link) return;
    e.preventDefault();
    folder = link.dataset.folder ?? "Inbox";
    page = 1;
    void loadMail();
  });
  $("#mailPager").addEventListener("click", (e) => {
    const link = (e.target as HTMLElement).closest<HTMLElement>("[data-page]");
    if (!link) return;
    e.preventDefault();
    page = Number(link.dataset.page);
    void loadMail();
  });
  $("#mailList").addEventListener("click", (e) => {
    const row = (e.target as HTMLElement).closest<HTMLElement>("[data-mail]");
    if (row) void openMessage(Number(row.dataset.mail), row);
  });
  $("#mailList").addEventListener("keydown", (e) => {
    const row = (e.target as HTMLElement).closest<HTMLElement>("[data-mail]");
    if (row && e.key === "Enter") void openMessage(Number(row.dataset.mail), row);
  });

  $("#mailNew").addEventListener("click", () => void compose());
  $("#mailCancel").addEventListener("click", () => $("#mailForm").classList.add("hidden"));
  $("#mailChips").addEventListener("click", (e) => {
    const chip = (e.target as HTMLElement).closest<HTMLElement>("[data-chip]");
    if (!chip) return;
    chosen = chosen.filter((p) => p.id !== chip.dataset.chip);
    drawChips();
  });
  const toInput = $<HTMLInputElement>("#mailToInput");
  toInput.addEventListener("change", () => {
    const person = people?.find((p) => p.name === toInput.value.trim());
    if (person && !chosen.some((p) => p.id === person.id)) chosen.push(person);
    toInput.value = "";
    drawChips();
  });
  $<HTMLFormElement>("#mailForm").addEventListener("submit", (e) => {
    e.preventDefault();
    void send();
  });

  // Кнопки в окне письма.
  $("#winExtra").addEventListener("click", (e) => {
    const button = (e.target as HTMLElement).closest<HTMLElement>("[data-mail-reply], [data-mail-delete]");
    if (!button) return;
    if (button.dataset.mailReply) void reply(Number(button.dataset.mailReply));
    else if (button.dataset.mailDelete && confirm("Переместить письмо в «Удалённые»?"))
      void remove(Number(button.dataset.mailDelete));
  });
}

export async function openMail(): Promise<void> {
  if (state.demo) {
    $("#mailList").innerHTML = DEMO;
    return;
  }
  initMail();
  await loadMail();
}

async function loadMail(): Promise<void> {
  for (const link of document.querySelectorAll<HTMLElement>("#mailFolders [data-folder]"))
    link.classList.toggle("on", link.dataset.folder === folder);
  const list = $("#mailList");
  list.innerHTML = `<p class="hint">Загружаю письма…</p>`;
  $("#mailPager").innerHTML = "";
  try {
    const data = await api<MailPage>(`/api/mail?folder=${folder}&page=${page}`);
    const sent = folder === "Sent";
    list.innerHTML = data.entries.length
      ? `<table><tr><th>${sent ? "Кому" : "От кого"}</th><th>Тема</th><th>Дата</th></tr>${data.entries
          .map(
            (m) => `<tr data-mail="${m.id}" tabindex="0" class="${m.read ? "" : "unread"}">
              <td>${esc(sent ? m.to : m.author)}</td><td>${esc(m.subject || "(без темы)")}</td><td>${esc(when(m.sent))}</td></tr>`,
          )
          .join("")}</table>`
      : `<p class="hint">Писем нет.</p>`;
    if (data.pages > 1) {
      const prev = data.page > 1 ? `<a href="#" data-page="${data.page - 1}">&lt;&lt;</a>` : "";
      const next = data.page < data.pages ? `<a href="#" data-page="${data.page + 1}">&gt;&gt;</a>` : "";
      $("#mailPager").innerHTML = `${prev} стр. ${data.page} из ${data.pages} ${next}`;
    }
  } catch (error) {
    list.innerHTML = `<p class="gb-msg">${esc(errorText(error))}</p>`;
  }
}

async function openMessage(id: number, row?: HTMLElement): Promise<void> {
  openWindow(`${icon("mail.png")} Письмо`, `<p class="hint">Загружаю…</p>`);
  try {
    const m = await api<MailMessage>(`/api/mail/${id}`);
    const files = m.attachments
      .map(
        (f) =>
          `<li>📎 <a href="/api/mail/attachment/${f.id}?name=${encodeURIComponent(f.name)}" download>${esc(f.name)}</a></li>`,
      )
      .join("");
    $("#lessonTitle").innerHTML = `${icon("mail.png")} ${esc(m.subject || "(без темы)")}`;
    $("#lessonBody").innerHTML = `
      <table class="info">
        <tr><th>От кого</th><td>${esc(m.author)}</td></tr>
        <tr><th>Кому</th><td>${esc(m.to)}</td></tr>
        <tr><th>Дата</th><td>${esc(when(m.sent))}</td></tr>
      </table>
      <div class="mail-text">${esc(m.text)}</div>
      ${files ? `<ul class="files">${files}</ul>` : ""}`;
    $("#winExtra").innerHTML = [
      m.can_reply && m.folder !== "Sent"
        ? `<button class="btn" type="button" data-mail-reply="${m.id}">Ответить</button>`
        : "",
      m.folder !== "Deleted"
        ? `<button class="btn" type="button" data-mail-delete="${m.id}">Удалить</button>`
        : "",
    ].join(" ");
    if (row?.classList.contains("unread")) {
      row.classList.remove("unread");
      void refreshBadge();
    }
  } catch (error) {
    $("#lessonBody").innerHTML = `<p class="gb-msg">${esc(errorText(error))}</p>`;
  }
}

function drawChips(): void {
  $("#mailChips").innerHTML = chosen
    .map((p) => `<span class="chip" data-chip="${esc(p.id)}" title="Убрать">${esc(p.name)} ✕</span>`)
    .join("");
}

async function compose(draft?: { to: Person[]; subject: string; text: string }): Promise<void> {
  chosen = draft?.to ?? [];
  drawChips();
  $<HTMLInputElement>("#mailSubject").value = draft?.subject ?? "";
  $<HTMLTextAreaElement>("#mailText").value = draft?.text ?? "";
  $("#mailMsg").textContent = "";
  $("#mailForm").classList.remove("hidden");
  $(draft ? "#mailText" : "#mailToInput").focus();
  if (!people) {
    try {
      people = await api<Person[]>("/api/mail/recipients");
      $("#mailPeople").innerHTML = people.map((p) => `<option value="${esc(p.name)}"></option>`).join("");
    } catch (error) {
      $("#mailMsg").textContent = errorText(error);
    }
  }
}

async function reply(id: number): Promise<void> {
  try {
    const draft = await api<{ to: Person[]; subject: string; text: string }>(`/api/mail/${id}/reply`);
    closeWindow();
    await compose({ ...draft, text: `\n\n${draft.text}` });
    $<HTMLTextAreaElement>("#mailText").setSelectionRange(0, 0);
  } catch (error) {
    alert(errorText(error));
  }
}

async function send(): Promise<void> {
  const button = $<HTMLButtonElement>("#mailSend");
  const msg = $("#mailMsg");
  if (!chosen.length) {
    msg.textContent = "Выбери получателя из списка";
    return;
  }
  button.disabled = true;
  msg.textContent = "";
  try {
    await api("/api/mail", {
      method: "POST",
      body: JSON.stringify({
        to: chosen.map((p) => p.id),
        subject: $<HTMLInputElement>("#mailSubject").value,
        text: $<HTMLTextAreaElement>("#mailText").value,
      }),
    });
    $("#mailForm").classList.add("hidden");
    folder = "Sent";
    page = 1;
    await loadMail();
  } catch (error) {
    msg.textContent = errorText(error);
  } finally {
    button.disabled = false;
  }
}

async function remove(id: number): Promise<void> {
  try {
    await api(`/api/mail/${id}`, { method: "DELETE" });
    closeWindow();
    await loadMail();
  } catch (error) {
    alert(errorText(error));
  }
}

// =====================================================================
// Отчёты
// =====================================================================

let totals: Totals | null = null;
let reportsReady = false;

const markClass = (avg: number) => (avg >= 4.5 ? "m5" : avg >= 3.5 ? "m4" : avg >= 2.5 ? "m3" : "m2");
const num = (n: number) =>
  n
    .toFixed(2)
    .replace(/\.?0+$/, "")
    .replace(".", ",");

/** Отметка цветом: целые (выставленные) и средние баллы красятся одинаково. */
function markSpan(value: number | string, extra = ""): string {
  const n = typeof value === "number" ? value : Number(String(value).replace(",", "."));
  const cls = Number.isFinite(n) && n > 0 ? markClass(n) : "";
  const text = typeof value === "number" ? num(value) : value;
  return `<span class="mark ${extra} ${cls}">${esc(text)}</span>`;
}

function cellHtml(cell: PeriodTotal | undefined): string {
  if (!cell || (cell.avg == null && cell.mark == null)) return "";
  const mark = cell.mark != null ? markSpan(cell.mark, "final") : "";
  const avg = cell.avg != null ? markSpan(cell.avg, "avg") : "";
  return [mark, avg].filter(Boolean).join(" ");
}

export async function openReports(): Promise<void> {
  const box = $("#reportTable");
  if (state.demo) {
    box.innerHTML = DEMO;
    return;
  }
  if (!reportsReady) {
    reportsReady = true;
    box.addEventListener("click", (e) => {
      const target = (e.target as HTMLElement).closest<HTMLElement>("[data-subject]");
      if (target) void openSubject(Number(target.dataset.subject), Number(target.dataset.term));
    });
  }
  box.innerHTML = `<p class="hint">Загружаю…</p>`;
  try {
    totals = await api<Totals>("/api/reports/totals");
    const t = totals;
    const termOf = (key: string) => (key.startsWith("t") ? key.slice(1) : (t.current?.slice(1) ?? ""));
    box.innerHTML = `<table class="report"><tr><th>Предмет</th>${t.periods
      .map((p) => `<th class="${p.key === t.current ? "cur" : ""}">${esc(p.name)}</th>`)
      .join("")}</tr>${t.subjects
      .map(
        (s) =>
          `<tr><td><a href="#" data-subject="${s.id}" data-term="${termOf(t.current ?? "")}">${esc(s.name)}</a></td>${t.periods
            .map((p) => {
              const html = cellHtml(s.cells[p.key]);
              const term = termOf(p.key);
              return html && p.key.startsWith("t")
                ? `<td class="${p.key === t.current ? "cur" : ""}" data-subject="${s.id}" data-term="${term}">${html}</td>`
                : `<td class="${p.key === t.current ? "cur" : ""}">${html}</td>`;
            })
            .join("")}</tr>`,
      )
      .join("")}</table>`;
  } catch (error) {
    box.innerHTML = `<p class="gb-msg">${esc(errorText(error))}</p>`;
  }
}

async function openSubject(subject: number, term: number): Promise<void> {
  if (!term) return;
  openWindow(`${icon("book.png")} Успеваемость`, `<p class="hint">Загружаю…</p>`);
  try {
    const p = await api<Performance>(`/api/reports/subject?subject=${subject}&term=${term}`);
    $("#lessonTitle").innerHTML = `${icon("book.png")} ${esc(p.subject)} — ${esc(p.term)}`;
    const most = Math.max(1, ...p.stats.map((s) => s.count));
    const bars = [...p.stats]
      .sort((a, b) => b.mark - a.mark)
      .map(
        (s) => `<div class="bar"><span class="mark m${s.mark}">${s.mark}</span>
          <i style="width:${Math.round((s.count / most) * 100)}%"></i><b>${s.count}</b></div>`,
      )
      .join("");
    const rows = p.results
      .map(
        (r) => `<tr><td>${esc(r.date.split("-").reverse().join("."))}</td>
          <td title="${esc(r.kind)}">${esc(r.kind || r.abbr)}${r.comment ? `<br><small>${esc(r.comment)}</small>` : ""}</td>
          <td>${r.weight ?? ""}</td>
          <td>${r.duty ? `<span class="mark debt">•</span>` : r.mark != null ? `<span class="mark m${r.mark}">${r.mark}</span>` : ""}</td></tr>`,
      )
      .join("");
    const avg = (n: number | null) =>
      n != null ? `<span class="mark avg ${markClass(n)}">${num(n)}</span>` : "—";
    $("#lessonBody").innerHTML = `
      <table class="info">
        <tr><th>Мой балл</th><td>${avg(p.avg)}</td></tr>
        <tr><th>По классу</th><td>${avg(p.class_avg)}</td></tr>
        <tr><th>Уроки</th><td>${p.lessons.passed ?? "?"} прошло из ${p.lessons.scheduled ?? "?"}</td></tr>
        ${p.teachers.length ? `<tr><th>Учитель</th><td><span class="teacher">${esc(p.teachers.join(", "))}</span></td></tr>` : ""}
      </table>
      ${bars ? `<fieldset><legend>Сколько каких</legend><div class="bars">${bars}</div></fieldset>` : ""}
      <fieldset><legend>${icon("notes.png")} Все оценки</legend>${
        rows
          ? `<table class="marks-table"><tr><th>Дата</th><th>За что</th><th>Вес</th><th>Оценка</th></tr>${rows}</table>`
          : `<p class="hint">Оценок пока нет.</p>`
      }</fieldset>`;
  } catch (error) {
    $("#lessonBody").innerHTML = `<p class="gb-msg">${esc(errorText(error))}</p>`;
  }
}
