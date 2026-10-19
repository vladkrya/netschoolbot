// Окно урока в стиле Win98. Грузится лениво — при первом клике по уроку.
import { api } from "./api";
import { absenceHtml, lessonIcon, lessonMarks, markHtml, taskHtml } from "./lessons";
import { redraw } from "./schedule";
import { state } from "./state";
import type { AssignmentCard, Lesson, LessonDetails } from "./types";
import { $, esc, fullDate, icon } from "./util";

const none = (text: string, cls = "") => `<li class="none ${cls}">${esc(text)}</li>`;

function infoRows(l: Lesson): string {
  const marks = lessonMarks(l);
  const rows: [string, string | false][] = [
    [
      "Когда",
      `${esc(fullDate(l.day))}${l.number ? `, ${l.number} урок` : ""}<br>${esc(l.start)} – ${esc(l.end)}`,
    ],
    ["Учитель", !!l.teacher && `<span class="teacher">${esc(l.teacher)}</span>`],
    ["Кабинет", !!l.room && `${icon("pin.svg")} ${esc(l.room)}`],
    ["Формат", l.distance && `${icon("computer.png")} дистанционно`],
    ["Группа", !!l.group && esc(l.group)],
    ["Тема", !!l.topic && esc(l.topic)],
    ["Оценки", `${marks.length ? marks.map(markHtml).join("") : "—"}${absenceHtml(l)}`],
  ];
  return rows.map(([th, td]) => (td ? `<tr><th>${th}</th><td>${td}</td></tr>` : "")).join("");
}

export async function openLesson(l: Lesson): Promise<void> {
  // Расписание без дневника: задания приходят отчётом по клику.
  const fromReport = l.sg !== undefined && !state.demo;
  $("#winExtra").innerHTML = "";
  $("#lessonTitle").innerHTML = `${lessonIcon(l)} ${esc(l.subject)}`;
  $("#lessonBody").innerHTML = `
    <table class="info">${infoRows(l)}</table>
    <fieldset><legend>${icon("notes.png")} Задания к уроку</legend>
      <ul class="tasks" id="dueTasks">${
        l.assignments.length
          ? l.assignments.map(taskHtml).join("")
          : none(fromReport ? "Загружаю…" : "Ничего не задано")
      }</ul>
    </fieldset>
    ${
      fromReport
        ? `<fieldset><legend>${icon("calendar.png")} <span id="nextTitle">Задано на следующий урок</span></legend>
            <ul class="tasks" id="nextTasks">${none("Загружаю…")}</ul></fieldset>`
        : ""
    }`;
  $("#lessonWin").classList.remove("hidden");
  $("#lessonOk").focus();

  if (fromReport) await loadFromReport(l);
  if (!state.demo) loadCards();
}

async function loadFromReport(l: Lesson): Promise<void> {
  try {
    const d = await api<LessonDetails>(`/api/lesson?sg=${l.sg}&day=${l.day}`);
    $("#dueTasks").innerHTML = d.note
      ? none(d.note)
      : d.due.length
        ? d.due.map(taskHtml).join("")
        : none("Ничего не задано");
    $("#nextTitle").textContent = d.next_day
      ? `Задано на ${fullDate(d.next_day).toLowerCase()}`
      : "Задано на следующий урок";
    $("#nextTasks").innerHTML = d.next.length ? d.next.map(taskHtml).join("") : none("Пока ничего");
    // Задания предмета теперь известны — покажем их и в клетке урока.
    if (d.due.length && !l.assignments.length) {
      l.assignments = d.due.map((a) => ({ ...a, mark: null }));
      redraw();
    }
  } catch (err) {
    $("#dueTasks").innerHTML = none((err as Error).message, "err");
    $("#nextTasks").innerHTML = "";
  }
}

/**
 * Задания из дневника: описание и вложения — из карточки задания. Пока карточка
 * грузится, ничего не показываем; пустую карточку не показываем вовсе.
 * Карточки кэшируются: повторное открытие урока — без запросов.
 */
const cards = new Map<string, Promise<AssignmentCard | null>>();

function card(id: string): Promise<AssignmentCard | null> {
  let pending = cards.get(id);
  if (!pending) {
    pending = api<AssignmentCard>(`/api/assignment/${id}`, { signal: AbortSignal.timeout(8000) }).catch(
      () => {
        cards.delete(id); // ошибку не кэшируем — в следующий раз попробуем снова
        return null;
      },
    );
    cards.set(id, pending);
  }
  return pending;
}

function loadCards(): void {
  for (const el of document.querySelectorAll<HTMLElement>("#lessonBody [data-assignment]")) {
    const id = el.dataset.assignment;
    if (!id) continue;
    void card(id).then((c) => {
      const files = (c?.attachments ?? []).map((f) => `<li>📎 ${esc(f.name)}</li>`).join("");
      const text = c?.description ? `<p>${esc(c.description)}</p>` : "";
      if (text || files) el.innerHTML = `${text}${files ? `<ul class="files">${files}</ul>` : ""}`;
      else el.remove();
    });
  }
}

export function closeLesson(): void {
  $("#lessonWin").classList.add("hidden");
}
