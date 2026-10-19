// «Заметки», «Обо мне», «Гостевая книга». Грузятся лениво — при первом переходе.
import { api, ApiError } from "./api";
import { state } from "./state";
import type { GuestEntry, Guestbook } from "./types";
import { $, esc, store } from "./util";

let ready = false;

export function initPages(onLogout: () => Promise<void>): void {
  if (ready) return;
  ready = true;

  const notes = $<HTMLTextAreaElement>("#notes");
  notes.value = store.get("notes", "");
  notes.addEventListener("input", () => store.set("notes", notes.value));

  initGuestbook();

  $("#logoutBtn").addEventListener("click", () => void onLogout());
}

export function renderAbout(): void {
  const me = state.me;
  const source = me?.mode === "mobile" ? "мобильный API" : "веб-API";
  $("#aboutText").innerHTML = me
    ? `Меня зовут <b>${esc(me.name)}</b>${me.class ? `, класс <b>${esc(me.class)}</b>` : ""}. Люблю котиков и учиться!` +
      (state.demo ? "" : `<br><small>Данные идут через ${source} «Сетевого города».</small>`)
    : "Вы не вошли.";
}

// ---------- гостевая книга (общая, на сервере) ----------
let token = "";
let replyTo: GuestEntry | null = null;

function when(at: number): string {
  return new Date(at * 1000).toLocaleString("ru", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function entryHtml(e: GuestEntry, canPost: boolean): string {
  const to = e.reply_to ? `<span class="gb-to">→ ${esc(e.reply_to)}</span> ` : "";
  const actions = [
    canPost ? `<a href="#" data-gb-reply="${e.id}">ответить</a>` : "",
    e.mine ? `<a href="#" data-gb-delete="${e.id}">удалить</a>` : "",
  ].filter(Boolean);
  return `<div class="gb-entry"><b>${esc(e.name)}</b> <small>${esc(when(e.at))}</small>
    <div class="gb-text">${to}${esc(e.text)}</div>
    ${actions.length ? `<div class="gb-links">${actions.join(" · ")}</div>` : ""}</div>`;
}

let entries: GuestEntry[] = [];

async function loadGuestbook(): Promise<void> {
  const msg = $("#gbMsg");
  try {
    const book = await api<Guestbook>("/api/guestbook");
    token = book.token;
    entries = book.entries;
    $("#gbForm").classList.toggle("hidden", !book.can_post);
    $("#gbLogin").classList.toggle("hidden", book.can_post);
    const roots = entries.filter((e) => !e.parent).reverse(); // новые ветки сверху
    $("#gbList").innerHTML =
      roots
        .map((root) => {
          const replies = entries.filter((e) => e.parent === root.id);
          return `<li>${entryHtml(root, book.can_post)}${
            replies.length
              ? `<ul class="gb-replies">${replies.map((r) => `<li>${entryHtml(r, book.can_post)}</li>`).join("")}</ul>`
              : ""
          }</li>`;
        })
        .join("") || "<li>Пока пусто. Будь первым!</li>";
  } catch (error) {
    msg.textContent = error instanceof Error ? error.message : "Не удалось загрузить гостевую книгу";
  }
}

function setReply(entry: GuestEntry | null): void {
  replyTo = entry;
  $("#gbReply").classList.toggle("hidden", !entry);
  $("#gbReplyName").textContent = entry?.name ?? "";
  if (entry) $<HTMLTextAreaElement>("#gbText").focus();
}

function initGuestbook(): void {
  const name = $<HTMLInputElement>("#gbName");
  const text = $<HTMLTextAreaElement>("#gbText");
  const msg = $("#gbMsg");
  name.value = store.get("gbName", "");
  text.addEventListener("input", () => ($("#gbCount").textContent = `${text.value.length}/500`));

  $("#gbList").addEventListener("click", (e) => {
    const link = (e.target as HTMLElement).closest<HTMLElement>("[data-gb-reply], [data-gb-delete]");
    if (!link) return;
    e.preventDefault();
    if (link.dataset.gbReply) {
      setReply(entries.find((x) => x.id === link.dataset.gbReply) ?? null);
      $("#gbForm").scrollIntoView({ block: "nearest", behavior: "smooth" });
    } else if (link.dataset.gbDelete && confirm("Удалить сообщение? Ответы на него тоже пропадут.")) {
      api(`/api/guestbook/${link.dataset.gbDelete}`, { method: "DELETE" })
        .then(loadGuestbook)
        .catch((error: Error) => (msg.textContent = error.message));
    }
  });
  $("#gbReplyCancel").addEventListener("click", (e) => {
    e.preventDefault();
    setReply(null);
  });

  $<HTMLFormElement>("#gbForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const send = $<HTMLButtonElement>("#gbSend");
    send.disabled = true;
    msg.textContent = "";
    store.set("gbName", name.value);
    api("/api/guestbook", {
      method: "POST",
      body: JSON.stringify({
        name: name.value,
        text: text.value,
        parent: replyTo?.id ?? null,
        token,
        website: $<HTMLInputElement>("#gbWebsite").value,
      }),
    })
      .then(() => {
        text.value = "";
        $("#gbCount").textContent = "0/500";
        setReply(null);
      })
      .catch((error: unknown) => {
        msg.textContent = error instanceof ApiError ? error.message : "Не получилось отправить";
      })
      .finally(() => {
        send.disabled = false;
        void loadGuestbook(); // свежий одноразовый токен формы
      });
  });
}

export function renderGuestbook(): void {
  void loadGuestbook();
}
