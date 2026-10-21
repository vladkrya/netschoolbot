// Вторая вкладка: расписание звонков (по умолчанию) или неделя карточками
// дней — время занятий, число уроков, раскрывающийся список и переход на день.
// Звонки берём у школы (/api/bells, веб-API schedule/times). Когда их нет —
// вход через Госуслуги в мобильном режиме или сбой, — собираем звонки из
// номеров и времени уроков недели, как раньше.
'use strict';

import {
  $, addDays, api, dayMonth, errorCard, esc, isoToday, lessonNo, mondayOf, plural, showFailure, showLoading, skeleton, state,
  WEEKDAYS, WEEKDAYS_FULL, weekOffsetOf, stillMotion,
} from './core.js';
import { dayData, ensureWeek, goToDay, isHiddenWeekend, openCalendar } from './day.js';
import { isNewDesign, showBells } from './appearance.js';
import { select, tap } from './haptics.js';

const expanded = new Set();
const expansionAnimations = new WeakMap();

function lessonsWord(count) {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return 'уроков';
  if (count % 10 === 1) return 'урок';
  if ([2, 3, 4].includes(count % 10)) return 'урока';
  return 'уроков';
}

function weekTitle(offset) {
  if (offset === 0) return 'Текущая';
  if (offset === 1) return 'Следующая';
  if (offset === -1) return 'Прошлая';
  return offset > 0 ? `Через ${offset} нед.` : `${-offset} нед. назад`;
}

/** Новый дизайн, режим звонков: звонки одни на все недели, листать недели
 *  незачем — полосы со стрелками нет, показываем текущую неделю. */
function bellsOnly() {
  return isNewDesign() && showBells();
}

/* Звонки школы: null — ещё не знаем, [] — школа их не дала. */
let schoolBells = null;
let bellsRequest = null;

export function loadSchoolBells() {
  bellsRequest ||= api('/api/bells', {
    onFresh: ({ data }) => { schoolBells = usedBells(data.items); rerenderBells(); },
  })
    .then(({ data }) => { schoolBells = usedBells(data.items); })
    .catch(() => { schoolBells = []; bellsRequest = null; });
  return bellsRequest;
}

function usedBells(items) {
  return (items || []).filter((bell) => bell.used !== false
    && minutesOf(bell.start) != null && minutesOf(bell.end) > minutesOf(bell.start));
}

function hasSchoolBells() {
  return Boolean(schoolBells?.length);
}

/** Пометка над звонками: только когда они собраны из уроков. */
export function bellsNoteHtml() {
  return hasSchoolBells() ? ''
    : '<div class="bells-note">Звонки собраны из расписания уроков и могут быть неточными.</div>';
}

function rerenderBells() {
  if (state.tab === 'schedule' && showBells()) loadSchedule();
  document.dispatchEvent(new CustomEvent('netschool:bells'));
}

export async function loadSchedule(force = false) {
  const plain = bellsOnly();
  $('schedPrev').closest('.date-bar').hidden = plain;
  if (showBells()) await loadSchoolBells();
  $('bellsNote').hidden = !plain || hasSchoolBells();
  if (plain) state.scheduleWeek = 0;
  let monday = addDays(mondayOf(isoToday()), state.scheduleWeek * 7);
  $('schedLabel').textContent = weekTitle(state.scheduleWeek);
  $('schedSub').textContent = `${dayMonth(monday)} – ${dayMonth(addDays(monday, 6))}`;

  const container = $('scheduleContent');
  if (force || !dayData(monday)) showLoading(container, skeleton());
  try {
    await ensureWeek(monday, force);
  } catch (error) {
    showFailure(container, error, errorCard(error, 'schedRetry'));
    $('schedRetry')?.addEventListener('click', () => loadSchedule(true));
    return;
  }
  // Каникулы: на этой неделе уроков нет — звонки берём со следующей.
  if (plain && !hasSchoolBells() && !bellGroups(monday).length) {
    const next = addDays(monday, 7);
    try {
      await ensureWeek(next, force);
      if (bellGroups(next).length) monday = next;
    } catch {
      /* останется «уроков нет» */
    }
  }
  render(monday);
}

function render(monday) {
  if (showBells()) {
    renderBells(monday);
    return;
  }
  const today = isoToday();
  const cards = [];
  for (let index = 0; index < 7; index += 1) {
    const iso = addDays(monday, index);
    const lessons = dayData(iso)?.lessons || [];
    // Субботу и воскресенье без уроков не показываем, если не включено
    // «Показывать выходные» в меню.
    if (isHiddenWeekend(iso)) continue;

    const first = lessons[0];
    const last = lessons[lessons.length - 1];
    const range = first && last && first.start && last.end ? `${first.start} — ${last.end}` : '';
    if (isNewDesign()) {
      cards.push(weekCard(iso, index, lessons, range, iso === today));
      continue;
    }
    const open = expanded.has(iso);
    cards.push(`<div class="card sched-card ${iso === today ? 'current' : ''}">
        <div class="sched-top">
          <div><div class="sched-day">${WEEKDAYS_FULL[index]}</div><div class="muted small">${esc(dayMonth(iso))}</div></div>
          <div class="sched-range">${esc(range)}</div>
        </div>
        ${lessons.length
          ? `<button class="sched-toggle" data-toggle="${iso}" aria-expanded="${open}" aria-controls="sched-${iso}">${lessons.length} ${lessonsWord(lessons.length)} <span class="sched-chevron" aria-hidden="true">▾</span></button>`
          : '<div class="muted small sched-toggle">Уроков нет</div>'}
        ${lessons.length ? `<div id="sched-${iso}" class="sched-collapse${open ? ' open' : ''}"${open ? '' : ' inert'}><div class="sched-collapse-inner"><div class="sched-list">
            ${lessons.map((l) => `<div class="sched-lesson"><span class="lc-num">${esc(lessonNo(l))}</span><span>${esc(l.subject)}${l.is_elective ? '<span class="elective-badge">Факультатив</span>' : ''}</span></div>`).join('')}
            <button class="btn outline" data-goto="${iso}">Перейти ко дню</button>
          </div></div></div>` : ''}
      </div>`);
  }

  const container = $('scheduleContent');
  container.innerHTML = cards.join('');
  container.querySelectorAll('[data-toggle]').forEach((button) =>
    button.addEventListener('click', () => {
      select();
      const iso = button.dataset.toggle;
      const open = !expanded.has(iso);
      if (open) expanded.add(iso);
      else expanded.delete(iso);
      button.setAttribute('aria-expanded', String(open));
      const panel = document.getElementById(button.getAttribute('aria-controls'));
      const from = panel.getBoundingClientRect().height;
      expansionAnimations.get(panel)?.cancel();
      panel.inert = !open;
      panel.classList.toggle('open', open);
      panel.style.height = open ? 'auto' : '0px';
      if (!stillMotion()) {
        const to = open ? panel.scrollHeight : 0;
        const animation = panel.animate([{ height: `${from}px` }, { height: `${to}px` }], {
          duration: 240, easing: 'cubic-bezier(.2,.8,.3,1)',
        });
        expansionAnimations.set(panel, animation);
      }
    })
  );
  container.querySelectorAll('[data-goto]').forEach((button) =>
    button.addEventListener('click', () => {
      tap();
      window.dispatchEvent(new CustomEvent('netschool:tab', { detail: 'day' }));
      goToDay(button.dataset.goto);
    })
  );
}

/** Неделя карточками дней (новый дизайн) — для шторки «Звонки и неделя» на
 *  «Дне». Нажатие на день (data-goto) открывает его. */
export function weekCardsHtml(monday) {
  const today = isoToday();
  const cards = [];
  for (let index = 0; index < 7; index += 1) {
    const iso = addDays(monday, index);
    if (isHiddenWeekend(iso)) continue;
    const lessons = dayData(iso)?.lessons || [];
    const first = lessons[0];
    const last = lessons[lessons.length - 1];
    const range = first && last && first.start && last.end ? `${first.start} — ${last.end}` : '';
    cards.push(weekCard(iso, index, lessons, range, iso === today));
  }
  return cards.join('');
}

/** Новый дизайн: день недели целиком, без раскрытия — уроки со временем.
 *  Нажатие на день открывает его во вкладке «День». */
function weekCard(iso, index, lessons, range, isToday) {
  return `<button class="card sched-card week-card ${isToday ? 'current' : ''}" data-goto="${iso}">
      <div class="sched-top">
        <div><div class="sched-day">${WEEKDAYS_FULL[index]}</div><div class="muted small">${esc(dayMonth(iso))}${isToday ? ' · сегодня' : ''}</div></div>
        <div class="sched-range">${esc(range.replace(' — ', '–'))}</div>
      </div>
      ${lessons.length ? `<div class="sched-list">${lessons.map((l) => `<div class="sched-lesson">
          <span class="lc-num">${esc(lessonNo(l))}</span>
          <span class="sl-subject">${esc(l.subject)}${l.is_elective ? '<span class="elective-badge">Факультатив</span>' : ''}</span>
          <span class="sl-time">${esc(l.start || '')}</span>
        </div>`).join('')}</div>` : '<div class="muted small sched-empty">Уроков нет</div>'}
    </button>`;
}

/* ─────────────────────────── Звонки ─────────────────────────── */

function minutesOf(text) {
  const [hours, minutes] = String(text || '').split(':').map(Number);
  return Number.isFinite(hours) && Number.isFinite(minutes) ? hours * 60 + minutes : null;
}

/** Уроки дня без повторов: «номер, начало, конец», по времени начала. */
function dayBells(lessons) {
  const rows = new Map();
  for (const lesson of lessons) {
    // Свои правки — не звонки школы.
    if (lesson.addedBy || lesson.editedBy) continue;
    const start = minutesOf(lesson.start);
    const end = minutesOf(lesson.end);
    if (start == null || end == null || end <= start) continue;
    rows.set(`${start}-${end}`, { number: lesson.number || '', start, end, text: `${lesson.start}–${lesson.end}` });
  }
  return [...rows.values()].sort((a, b) => a.start - b.start);
}

/** Дни недели с одинаковыми звонками — в одну карточку. У урока с номером
 *  обычное время — самое частое за неделю; день, где хоть один урок идёт
 *  в другое время (сокращённый, суббота), получает свою карточку. */
function bellGroups(monday) {
  const days = [];
  for (let index = 0; index < 7; index += 1) {
    const iso = addDays(monday, index);
    const rows = dayBells(dayData(iso)?.lessons || []);
    if (rows.length) days.push({ iso, index, rows });
  }
  const votes = new Map();
  for (const { rows } of days) {
    for (const row of rows) {
      if (!row.number) continue;
      const counts = votes.get(row.number) || new Map();
      counts.set(row.text, (counts.get(row.text) || 0) + 1);
      votes.set(row.number, counts);
    }
  }
  const usual = new Map([...votes].map(([number, counts]) =>
    [number, [...counts].sort((a, b) => b[1] - a[1])[0][0]]));

  const groups = new Map();
  // Один урок не в своё время (факультатив после уроков) не уводит день
  // в отдельную карточку: он идёт строкой под общими звонками с пометкой дня.
  const extras = new Map();
  for (const day of days) {
    // Номер, который бывает лишь в один день, — тоже «только Чт»: без него
    // перед ним рисовалась бы «перемена» в пару часов.
    const unique = day.rows.filter((row) => days.length > 1 && row.number
      && [...votes.get(row.number).values()].reduce((sum, count) => sum + count, 0) === 1);
    const conflicts = day.rows.filter((row) => !unique.includes(row)
      && (!row.number || usual.get(row.number) !== row.text));
    const regular = conflicts.length === 0 || (conflicts.length === 1 && day.rows.length >= 3);
    const odd = regular ? [...unique, ...conflicts] : [];
    const key = regular ? 'usual' : day.rows.map((row) => `${row.number}@${row.text}`).join('|');
    const group = groups.get(key) || { days: [], rows: new Map() };
    group.days.push(day);
    for (const row of day.rows) {
      const id = `${row.start}-${row.end}`;
      if (odd.includes(row)) {
        const extra = extras.get(id) || { row, days: [] };
        extra.days.push(day);
        extras.set(id, extra);
      } else {
        group.rows.set(id, row);
      }
    }
    groups.set(key, group);
  }
  return [...groups.entries()].map(([key, group]) => ({
    days: group.days,
    rows: [...group.rows.values()].sort((a, b) => a.start - b.start),
    extras: key === 'usual' ? [...extras.values()].sort((a, b) => a.row.start - b.row.start) : [],
  }));
}

/* Смена, которую человек открыл переключателем, — только на эту неделю и до
 *  перезапуска; потом снова открывается своя. */
let viewedShift = null;

/** Смены школы и своя: её угадываем по урокам недели (у кого больше
 *  совпадений начала урока); не угадали — own = null, открываем первую. */
function shiftsOf(monday) {
  const starts = new Set();
  for (let index = 0; index < 7; index += 1) {
    for (const lesson of dayData(addDays(monday, index))?.lessons || []) {
      if (lesson.addedBy || lesson.editedBy) continue;
      const start = minutesOf(lesson.start);
      if (start != null) starts.add(`${index + 1}@${start}`);
    }
  }
  const shifts = [...new Set(schoolBells.map((bell) => bell.shift))].sort((a, b) => a - b);
  const score = (shift) => schoolBells
    .filter((bell) => bell.shift === shift && starts.has(`${bell.weekday}@${minutesOf(bell.start)}`)).length;
  const best = shifts.map((shift) => [shift, score(shift)]).sort((a, b) => b[1] - a[1])[0];
  const own = best && best[1] > 0 ? best[0] : null;
  const picked = viewedShift?.monday === monday && shifts.includes(viewedShift.shift) ? viewedShift.shift : null;
  return { shifts, own, shown: picked ?? own ?? shifts[0] };
}

/** Звонки школы карточками одной смены: дни с одинаковым временем — вместе. */
function schoolGroups(monday, shift) {
  const groups = new Map();
  for (let weekday = 1; weekday <= 7; weekday += 1) {
    const rows = schoolBells
      .filter((bell) => bell.shift === shift && bell.weekday === weekday)
      .map((bell) => ({
        number: bell.number, start: minutesOf(bell.start), end: minutesOf(bell.end),
        text: `${bell.start}–${bell.end}`,
      }))
      .sort((a, b) => a.start - b.start);
    if (!rows.length) continue;
    const key = rows.map((row) => `${row.number}@${row.text}`).join('|');
    const group = groups.get(key) || { days: [], rows, extras: [] };
    group.days.push({ iso: addDays(monday, weekday - 1), index: weekday - 1 });
    groups.set(key, group);
  }
  return [...groups.values()];
}

/** «1 смена | 2 смена» над звонками, своя — с пометкой «ваша». */
function shiftSwitchHtml(monday, { shifts, own, shown }) {
  if (shifts.length < 2) return '';
  const index = shifts.indexOf(shown);
  return `<div class="seg-glass bells-shift" role="tablist" data-monday="${esc(monday)}" style="--n:${shifts.length};--i:${index}">
      <span class="seg-lens" aria-hidden="true"></span>
      ${shifts.map((shift) => `<button class="${shift === shown ? 'on' : ''}" data-shift="${esc(shift)}" role="tab"
        aria-selected="${shift === shown}">${esc(shift)} смена${shift === own ? ' <small>ваша</small>' : ''}</button>`).join('')}
    </div>`;
}

/** «Пн–Пт», «Пн, Ср, Чт», «Сб». */
function daysTitle(days) {
  const parts = [];
  let run = [];
  const flush = () => {
    if (run.length >= 3) parts.push(`${WEEKDAYS[run[0]]}–${WEEKDAYS[run[run.length - 1]]}`);
    else parts.push(...run.map((index) => WEEKDAYS[index]));
    run = [];
  };
  for (const { index } of days) {
    if (run.length && index !== run[run.length - 1] + 1) flush();
    run.push(index);
  }
  flush();
  return parts.join(', ');
}

function renderBells(monday) {
  $('scheduleContent').innerHTML = bellsHtml(monday);
}

/** Карточки звонков недели — для вкладки и для шторки из «Дня». */
export function bellsHtml(monday) {
  const today = isoToday();
  const now = new Date();
  const minute = now.getHours() * 60 + now.getMinutes();
  const shifts = hasSchoolBells() ? shiftsOf(monday) : null;
  const groups = shifts ? schoolGroups(monday, shifts.shown) : bellGroups(monday);
  if (!groups.length) {
    return `<div class="card bells-card"><div class="muted">На этой неделе уроков нет — звонки появятся вместе с расписанием.</div></div>`;
  }
  return shiftSwitchHtml(monday, shifts || { shifts: [] }) + groups.map((group) => {
    const isToday = group.days.some((day) => day.iso === today);
    const items = [];
    group.rows.forEach((row, index) => {
      const current = isToday && minute >= row.start && minute < row.end;
      items.push(`<div class="bell-row${current ? ' now' : ''}">
          <span class="lc-num">${esc(row.number)}</span>
          <span class="bell-time">${esc(row.text)}</span>
          <span class="bell-len muted small">${row.end - row.start} мин</span>
        </div>`);
      const next = group.rows[index + 1];
      const gap = next ? next.start - row.end : 0;
      if (gap > 0) {
        const breakNow = isToday && minute >= row.end && minute < next.start;
        items.push(`<div class="bell-break${breakNow ? ' now' : ''}">перемена ${gap} ${plural(gap, 'минута', 'минуты', 'минут')}</div>`);
      }
    });
    return `<div class="card bells-card${isToday ? ' current' : ''}">
        <div class="sched-top">
          <div class="sched-day">${esc(daysTitle(group.days))}</div>
          <div class="sched-range">${esc(`${group.rows[0].text.split('–')[0]}–${group.rows[group.rows.length - 1].text.split('–')[1]}`)}</div>
        </div>
        <div class="bells-list">${items.join('')}</div>
        ${group.extras.length ? `<div class="bells-extra">${group.extras.map(({ row, days }) => {
          const current = days.some((day) => day.iso === today) && minute >= row.start && minute < row.end;
          return `<div class="bell-row${current ? ' now' : ''}">
              <span class="lc-num">${esc(row.number)}</span>
              <span class="bell-time">${esc(row.text)}</span>
              <span class="bell-len muted small">только ${esc(daysTitle(days))}</span>
            </div>`;
        }).join('')}</div>` : ''}
      </div>`;
  }).join('');
}

export function initSchedule() {
  // Переключатель смен — и на вкладке, и в шторке из «Дня»: перерисовываем
  // звонки в том же контейнере.
  document.addEventListener('click', (event) => {
    const button = event.target.closest('.bells-shift [data-shift]');
    if (!button || button.classList.contains('on')) return;
    select();
    const control = button.closest('.bells-shift');
    const { monday } = control.dataset;
    viewedShift = { monday, shift: Number(button.dataset.shift) };
    control.parentElement.innerHTML = bellsHtml(monday);
  });
  if (showBells()) loadSchoolBells();
  // Свои правки расписания меняют неделю карточками.
  window.addEventListener('netschool:daysedited', () => { if (state.tab === 'schedule') loadSchedule(); });
  // Подсветка идущего урока или перемены.
  setInterval(() => {
    if (state.tab === 'schedule' && showBells() && state.scheduleWeek === 0) {
      const monday = mondayOf(isoToday());
      if (dayData(monday)) renderBells(monday);
    }
  }, 30000);
  $('schedPrev').addEventListener('click', () => {
    select();
    state.scheduleWeek -= 1;
    loadSchedule();
  });
  $('schedNext').addEventListener('click', () => {
    select();
    state.scheduleWeek += 1;
    loadSchedule();
  });
  $('schedTitle').addEventListener('click', () => {
    const monday = addDays(mondayOf(isoToday()), state.scheduleWeek * 7);
    openCalendar(monday, (iso) => {
      state.scheduleWeek = weekOffsetOf(iso);
      loadSchedule();
    }, { week: true });
  });
}
