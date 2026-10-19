import "./styles.css";

import { api, forgetWeeks } from "./api";
import { lessonByRef } from "./lessons";
import { redraw, refresh, renderUpcoming } from "./schedule";
import { state } from "./state";
import type { Lesson, Me } from "./types";
import { $, addDays, DAYS_LONG, mondayOf, MONTHS_GEN, store } from "./util";

type View = "calendar" | "login" | "notes" | "about" | "links" | "guestbook" | "mail" | "reports";

function show(view: View): void {
  for (const v of document.querySelectorAll(".view")) v.classList.add("hidden");
  $(`#view-${view}`).classList.remove("hidden");
  $("#weeknav").style.visibility = view === "calendar" ? "visible" : "hidden";
}

async function showLogin(message = ""): Promise<void> {
  show("login");
  $("#loginErr").textContent = message;
  const { initLogin } = await import("./login");
  await initLogin(openCalendar, startDemo);
}

function openCalendar(): void {
  show("calendar");
  void refresh(showLogin);
  void renderUpcoming();
}

function startDemo(): void {
  state.demo = true;
  store.set("demo", true);
  state.me = { name: "Демо Пользователь", class: "демо" };
  openCalendar();
}

async function logout(): Promise<void> {
  if (!state.demo) await api("/api/logout", { method: "POST" }).catch(() => {});
  state.demo = false;
  state.me = null;
  store.remove("demo");
  forgetWeeks();
  await showLogin();
}

// ---------- окно урока (модуль грузится при первом клике) ----------
async function openLesson(lesson: Lesson | undefined): Promise<void> {
  if (!lesson) return;
  const { openLesson: open } = await import("./lesson-window");
  await open(lesson);
}

function closeLesson(): void {
  $("#lessonWin").classList.add("hidden");
}

document.addEventListener("click", (e) => {
  const target = e.target as HTMLElement;
  const ref = target.closest<HTMLElement>("[data-ref]");
  if (ref) {
    e.preventDefault();
    void openLesson(lessonByRef(ref.dataset.ref));
    return;
  }
  if (target.id === "lessonWin" || target.closest("[data-close]")) closeLesson();
});

// ---------- режим фото: оценки, учителя и домашка под звёздочками ----------
// Без кнопки: включается щелчком по планете в шапке (или клавишей P).
function setPhoto(on: boolean): void {
  document.body.classList.toggle("photo", on);
  store.set("photo", on);
}
setPhoto(store.get("photo", false));
$(".globe").addEventListener("click", () => setPhoto(!document.body.classList.contains("photo")));

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeLesson();
  const typing = (e.target as HTMLElement).closest?.("input, textarea");
  if (!typing && !e.metaKey && !e.ctrlKey && e.code === "KeyP")
    setPhoto(!document.body.classList.contains("photo"));
  const cell = (e.target as HTMLElement).closest?.<HTMLElement>(".c.lesson[data-ref]");
  if (cell && (e.key === "Enter" || e.key === " ")) {
    e.preventDefault();
    void openLesson(lessonByRef(cell.dataset.ref));
  }
});

// ---------- навигация ----------
for (const link of document.querySelectorAll<HTMLElement>("[data-view]")) {
  link.addEventListener("click", async (e) => {
    e.preventDefault();
    const view = link.dataset.view as View;
    if (view === "calendar") return state.me ? openCalendar() : showLogin();
    if (view === "mail" || view === "reports") {
      if (!state.me) return showLogin();
      const school = await import("./school");
      show(view);
      return view === "mail" ? school.openMail() : school.openReports();
    }
    const pages = await import("./pages");
    pages.initPages(logout);
    if (view === "about") pages.renderAbout();
    if (view === "guestbook") pages.renderGuestbook();
    show(view);
  });
}

const goWeek = (start: Date) => {
  state.weekStart = start;
  void refresh(showLogin);
};
$("#prev").addEventListener("click", () => goWeek(addDays(state.weekStart, -7)));
$("#next").addEventListener("click", () => goWeek(addDays(state.weekStart, 7)));
$("#range").addEventListener("click", (e) => {
  e.preventDefault();
  goWeek(mondayOf(new Date()));
});

function setMode(mode: "week" | "periods"): void {
  state.mode = mode;
  $("#modeWeek").classList.toggle("on", mode === "week");
  $("#modePeriods").classList.toggle("on", mode === "periods");
  redraw();
}
$("#modeWeek").addEventListener("click", (e) => {
  e.preventDefault();
  setMode("week");
});
$("#modePeriods").addEventListener("click", (e) => {
  e.preventDefault();
  setMode("periods");
});

// ---------- часы в меню ----------
function tickClock(): void {
  const n = new Date();
  $("#sideDate").innerHTML = `${DAYS_LONG[n.getDay()]},<br>${n.getDate()} ${MONTHS_GEN[n.getMonth()]}`;
  $("#sideTime").textContent = `${n.getHours()}:${String(n.getMinutes()).padStart(2, "0")}`;
}
tickClock();
setInterval(tickClock, 15_000);

// ---------- запуск ----------
async function start(): Promise<void> {
  if (state.demo) return startDemo();
  try {
    state.me = await api<Me>("/api/me");
  } catch {
    return showLogin();
  }
  openCalendar();
  void updateMailBadge();
}

/** Число непрочитанных писем рядом с «Почтой». */
async function updateMailBadge(): Promise<void> {
  if (state.demo) return;
  const { count } = await api<{ count: number }>("/api/mail/unread").catch(() => ({ count: 0 }));
  $("#mailBadge").textContent = String(count);
  $("#mailBadge").classList.toggle("hidden", !count);
}
void start();
