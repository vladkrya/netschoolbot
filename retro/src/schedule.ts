import { ApiError, cachedWeek, fetchWeek, loadWeek } from "./api";
import {
  absenceHtml,
  lessonColor,
  lessonIcon,
  lessonKind,
  lessonMarks,
  lessonText,
  markHtml,
  refOf,
} from "./lessons";
import { state } from "./state";
import type { Lesson, Week } from "./types";
import {
  $,
  addDays,
  DAYS_LONG,
  DAYS_SHORT,
  esc,
  icon,
  iso,
  minutes,
  mondayOf,
  MONTHS_GEN,
  parseIso,
  plural,
  sameDay,
} from "./util";

const time = (t: string) => t.replace(/^0/, "");

// ---------- сетка недели ----------
function renderGrid(week: Week): void {
  const start = parseIso(week.start);
  const days = Array.from({ length: 6 }, (_, i) => addDays(start, i));
  const today = new Date();

  const byDay = new Map<string, Lesson[]>(days.map((d) => [iso(d), []]));
  for (const l of week.lessons) byDay.get(l.day)?.push(l);

  // Строки — номера уроков: звонки по дням разные (суббота короче), и строка
  // на каждое время дала бы полупустые ряды. Без номеров (демо) — по времени.
  const byNumber = week.lessons.some((l) => l.number > 0);
  const slotOf = (l: Lesson) => (byNumber ? l.number : minutes(l.start));
  const slots = new Map<number, Map<string, number>>(); // слот -> время начала -> сколько раз
  for (const l of week.lessons) {
    const counts = slots.get(slotOf(l)) ?? new Map<string, number>();
    counts.set(l.start, (counts.get(l.start) ?? 0) + 1);
    slots.set(slotOf(l), counts);
  }
  const rows = [...slots.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([slot, counts]) => ({ slot, start: [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0] }));
  if (!rows.length) {
    for (const t of ["8:30", "9:20", "10:20", "11:20", "12:20", "13:20"]) rows.push({ slot: -1, start: t });
  }

  let html = `<div class="dh corner"></div>`;
  days.forEach((d, i) => {
    const ls = byDay.get(iso(d)) ?? [];
    let sub: string;
    if (!ls.length) sub = "нет занятий";
    else if (ls.every((l) => l.distance)) sub = "весь день онлайн";
    else {
      const last = ls.reduce((a, b) => (minutes(a.end) > minutes(b.end) ? a : b));
      sub = `${ls.length} ${plural(ls.length, "урок", "урока", "уроков")}, до ${last.end}`;
    }
    const isToday = sameDay(d, today);
    html += `<div class="dh${isToday ? " today" : ""}">${isToday ? `<span class="clock">${icon("clock.png")}</span>` : ""}<a href="#">${DAYS_SHORT[i]} ${d.getDate()}</a><span class="sub">${esc(sub)}</span></div>`;
  });

  for (const row of rows) {
    html += `<div class="tc">${byNumber ? `<span class="num">${row.slot} урок</span>` : ""}${esc(time(row.start))}</div>`;
    for (const d of days) {
      const l = (byDay.get(iso(d)) ?? []).find((x) => slotOf(x) === row.slot);
      html += l ? cellHtml(l, row.start) : `<div class="c"></div>`;
    }
  }
  $("#grid").innerHTML = html;
}

function cellHtml(l: Lesson, rowStart: string): string {
  const title = [
    `${l.start}–${l.end}`,
    l.subject,
    ...l.assignments.map((a) => `${a.kind || "Задание"}: ${a.content}`),
  ];
  const text = lessonText(l);
  const ownTime =
    l.start !== rowStart ? `<span class="own-time">${esc(time(l.start))}–${esc(time(l.end))}</span>` : "";
  return `<div class="c lesson ${lessonColor(l)}" title="${esc(title.join("\n"))}" data-ref="${refOf(l)}" tabindex="0" role="button">
    ${absenceHtml(l)}${lessonMarks(l).map(markHtml).join("")}
    <div class="kind">${lessonIcon(l)} ${esc(lessonKind(l))}${ownTime}</div>
    <div class="subj">${esc(l.subject)}</div>
    ${text ? `<div class="what">${esc(text)}</div>` : ""}
    ${l.distance ? `<span class="room">${icon("computer.png")} вебинар</span>` : ""}
    ${l.room ? `<span class="room">${icon("pin.svg")}${esc(l.room)}</span>` : ""}
    ${l.teacher ? `<span class="teacher">${esc(l.teacher)}</span>` : ""}
  </div>`;
}

// ---------- «Периоды»: та же неделя таблицей ----------
function renderPeriods(week: Week): void {
  const start = parseIso(week.start);
  let html = `<div class="periods">`;
  for (let i = 0; i < 6; i++) {
    const d = addDays(start, i);
    const ls = week.lessons.filter((l) => l.day === iso(d));
    html += `<h4>${DAYS_LONG[d.getDay()]}, ${d.getDate()} ${MONTHS_GEN[d.getMonth()]}</h4>`;
    if (!ls.length) {
      html += `<p>нет занятий</p>`;
      continue;
    }
    html += `<table>${ls
      .map(
        (l) => `<tr data-ref="${refOf(l)}" class="prow"><td>${l.number || ""}</td><td>${l.start}–${l.end}</td>
          <td><b>${esc(l.subject)}</b></td><td class="what">${esc(lessonText(l))}</td>
          <td>${lessonMarks(l).map(markHtml).join("")}${absenceHtml(l)}</td></tr>`,
      )
      .join("")}</table>`;
  }
  $("#grid").innerHTML = `${html}</div>`;
}

export function redraw(): void {
  if (state.lastWeek) (state.mode === "week" ? renderGrid : renderPeriods)(state.lastWeek);
}

// ---------- главный рендер ----------
function showNote(text: string | null): void {
  const note = $("#emptyNote");
  note.textContent = text ?? "";
  note.classList.toggle("hidden", !text);
}

function emptyReason(week: Week): string | null {
  if (state.demo || week.lessons.length) return null;
  return week.class
    ? "На эту неделю в «Сетевом городе» нет уроков (каникулы или расписание ещё не выставили)."
    : "«Сетевой город» не вернул уроков для этого ученика. Проверь, что при входе выбрана основная школа, а не филиал или «(ДО)».";
}

export async function refresh(onUnauthorized: (msg: string) => void): Promise<void> {
  const s = state.weekStart;
  const e = addDays(s, 5);
  $("#range").textContent =
    s.getMonth() === e.getMonth()
      ? `${s.getDate()} – ${e.getDate()} ${MONTHS_GEN[e.getMonth()]}`
      : `${s.getDate()} ${MONTHS_GEN[s.getMonth()]} – ${e.getDate()} ${MONTHS_GEN[e.getMonth()]}`;

  const draw = (week: Week) => {
    if (iso(state.weekStart) !== week.start) return; // пока грузили, ушли на другую неделю
    state.lastWeek = week;
    redraw();
    showNote(emptyReason(week));
  };

  const hit = cachedWeek(s);
  if (hit) draw(hit.week);
  else {
    $("#grid").innerHTML = `<div class="loading">Загрузка…</div>`;
    showNote(null);
  }
  if (hit?.fresh) return prefetchNext(s);

  try {
    draw(await fetchWeek(s));
    prefetchNext(s);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return onUnauthorized(err.message);
    if (!hit) $("#grid").innerHTML = `<div class="loading error">${esc((err as Error).message)}</div>`;
    else showNote(`Показана сохранённая неделя: ${(err as Error).message}`);
  }
}

/** Следующую неделю подгружаем, когда браузер свободен, — кнопка «>>» сработает мгновенно. */
function prefetchNext(start: Date): void {
  const next = addDays(start, 7);
  if (state.demo || cachedWeek(next)) return;
  const idle = window.requestIdleCallback ?? ((cb: () => void) => setTimeout(cb, 1500));
  idle(() => void fetchWeek(next).catch(() => {}));
}

// ---------- «Сегодня / Завтра / Дальше» ----------
function dayLabel(d: Date): string {
  const t = new Date();
  if (sameDay(d, t)) return "Сегодня";
  if (sameDay(d, addDays(t, 1))) return "Завтра";
  return `${DAYS_LONG[d.getDay()]}, ${d.getDate()}`;
}

function cardHtml(head: string, headRight: string, l: Lesson | undefined): string {
  if (!l) {
    return `<div class="card-head"><a href="#">${esc(head)}</a></div><div class="card-inner card-empty">Пока ничего не запланировано!</div>`;
  }
  const place = l.room || l.distance ? `${icon("pin.svg")} ${esc(l.room || "онлайн")} ` : "";
  return `<div class="card-head"><a href="#">${esc(head)}</a><span>${esc(headRight)}</span></div>
    <div class="card-inner">
      <div class="row"><span>${l.distance ? icon("computer.png") : lessonIcon(l)} ${esc(lessonKind(l))}</span><span>${esc(l.start)} – ${esc(l.end)}</span></div>
      <a class="subj" href="#" data-ref="${refOf(l)}">${esc(l.subject)}</a>
      <div class="row"><span>${place}<span class="teacher">${esc(l.teacher)}</span></span></div>
    </div>`;
}

export async function renderUpcoming(): Promise<void> {
  const now = new Date();
  const today = iso(now);
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const isAhead = (l: Lesson) => l.day > today || (l.day === today && minutes(l.start) > nowMin);
  const thisWeek = mondayOf(now);

  let lessons: Lesson[] = [];
  for (const weekStart of [thisWeek, addDays(thisWeek, 7)]) {
    try {
      lessons = lessons.concat((await loadWeek(weekStart)).lessons);
    } catch {
      break;
    }
    if (lessons.filter(isAhead).length >= 2) break;
  }

  const todays = lessons.filter((l) => l.day === today);
  const busy = todays.some((l) => minutes(l.start) <= nowMin && nowMin < minutes(l.end));
  $("#mood").textContent = busy ? "Учусь!" : todays.some(isAhead) ? "Скоро уроки!" : "Веселюсь!";

  const upcoming = lessons
    .filter(isAhead)
    .sort((a, b) => (a.day + a.start.padStart(5, "0")).localeCompare(b.day + b.start.padStart(5, "0")));
  const [first, second] = upcoming;
  if (first) {
    const sameDayLessons = upcoming.filter((l) => l.day === first.day);
    const n = sameDayLessons.length;
    const last = sameDayLessons[n - 1];
    $("#card1").innerHTML = cardHtml(
      dayLabel(parseIso(first.day)),
      `${n} ${plural(n, "урок", "урока", "уроков")}, до ${last.end}`,
      first,
    );
  } else {
    $("#card1").innerHTML = cardHtml("Завтра", "", undefined);
  }
  $("#card2").innerHTML = cardHtml(
    "Дальше",
    second && first && second.day !== first.day ? dayLabel(parseIso(second.day)) : "",
    second,
  );
}
