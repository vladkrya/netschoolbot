import type { Color, Lesson, Task } from "./types";
import { esc, icon } from "./util";

/** Типы работ, которые красим красным, как лабораторные на макете. */
export const CONTROL = /контрол|проверочн|тест|зач[её]т|экзамен|диктант|самостоят|лаб/i;

export function lessonKind(l: Lesson): string {
  if (l.kind) return l.kind;
  if (l.distance) return "Онлайн";
  if (l.elective) return "Внеурочка";
  const control = l.assignments.find((a) => CONTROL.test(a.kind));
  return control ? control.kind.replace(/ работа$/, "") : "Урок";
}

export function lessonColor(l: Lesson): Color {
  if (l.color) return l.color;
  if (l.elective) return "violet";
  return l.assignments.some((a) => CONTROL.test(a.kind)) ? "red" : "yellow";
}

export const lessonIcon = (l: Lesson) => icon(lessonColor(l) === "red" ? "flask.svg" : "book.png");

export function lessonMarks(l: Lesson): string[] {
  return [...l.assignments.map((a) => a.mark).filter((m): m is string => !!m), ...(l.marks ?? [])];
}

export function lessonText(l: Lesson): string {
  return (
    l.assignments
      .map((a) => a.content)
      .filter(Boolean)
      .join("; ") ||
    l.topic ||
    ""
  );
}

export function markHtml(mark: string): string {
  if (mark === ".") return `<span class="mark debt" title="Долг: задание не сдано">•</span>`;
  const n = Number.parseInt(mark, 10);
  return `<span class="mark m${n >= 2 && n <= 5 ? n : ""}">${esc(mark)}</span>`;
}

export const absenceHtml = (l: Lesson) =>
  l.absence ? `<span class="mark absence" title="Посещаемость">${esc(l.absence)}</span>` : "";

export function taskHtml(task: Task): string {
  const result = task.result || task.mark;
  return `<li class="task${CONTROL.test(task.kind || "") ? " control" : ""}">
    <div class="task-head"><span class="task-kind">${esc(task.kind || "Задание")}</span>${result ? markHtml(result) : ""}</div>
    <div class="task-text">${esc(task.content || "—")}</div>
    ${task.id ? `<div class="task-more" data-assignment="${task.id}"></div>` : ""}
  </li>`;
}

// Уроки, на которые можно нажать: data-ref -> объект урока. Ключ стабильный,
// поэтому перерисовка сетки не ломает ссылки из карточек и наоборот.
const refs = new Map<string, Lesson>();
export function refOf(l: Lesson): string {
  const key = `${l.day}|${l.start}|${l.number}|${l.subject}`;
  refs.set(key, l);
  return esc(key);
}
export const lessonByRef = (ref: string | undefined) => (ref === undefined ? undefined : refs.get(ref));
