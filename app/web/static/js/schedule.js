// «Расписание»: неделя карточками дней — время занятий, число уроков,
// раскрывающийся список и переход на день.
'use strict';

import {
  $, addDays, dayMonth, errorCard, esc, isoToday, mondayOf, skeleton, state,
  WEEKDAYS_FULL,
} from './core.js';
import { dayData, ensureWeek, goToDay } from './day.js';
import { select, tap } from './haptics.js';

const expanded = new Set();

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

export async function loadSchedule(force = false) {
  const monday = addDays(mondayOf(isoToday()), state.scheduleWeek * 7);
  $('schedLabel').textContent = weekTitle(state.scheduleWeek);
  $('schedSub').textContent = `${dayMonth(monday)} – ${dayMonth(addDays(monday, 6))}`;

  const container = $('scheduleContent');
  if (force || !dayData(monday)) container.innerHTML = skeleton();
  try {
    await ensureWeek(monday, force);
  } catch (error) {
    container.innerHTML = errorCard(error, 'schedRetry');
    $('schedRetry')?.addEventListener('click', () => loadSchedule(true));
    return;
  }
  render(monday);
}

function render(monday) {
  const today = isoToday();
  const cards = [];
  for (let index = 0; index < 7; index += 1) {
    const iso = addDays(monday, index);
    const lessons = dayData(iso)?.lessons || [];
    // Воскресенье без уроков не показываем — в приложении его тоже нет.
    if (!lessons.length && index === 6) continue;

    const first = lessons[0];
    const last = lessons[lessons.length - 1];
    const range = first && last && first.start && last.end ? `${first.start} — ${last.end}` : '';
    const open = expanded.has(iso);
    cards.push(`<div class="card sched-card ${iso === today ? 'current' : ''}">
        <div class="sched-top">
          <div><div class="sched-day">${WEEKDAYS_FULL[index]}</div><div class="muted small">${esc(dayMonth(iso))}</div></div>
          <div class="sched-range">${esc(range)}</div>
        </div>
        ${lessons.length
          ? `<button class="sched-toggle" data-toggle="${iso}">${lessons.length} ${lessonsWord(lessons.length)} ${open ? '▴' : '▾'}</button>`
          : '<div class="muted small sched-toggle">Уроков нет</div>'}
        ${open ? `<div class="sched-list">
            ${lessons.map((l) => `<div class="sched-lesson">${esc(l.number ?? '')}. ${esc(l.subject)}</div>`).join('')}
            <button class="btn outline" data-goto="${iso}">Перейти ко дню</button>
          </div>` : ''}
      </div>`);
  }

  const container = $('scheduleContent');
  container.innerHTML = cards.join('');
  container.querySelectorAll('[data-toggle]').forEach((button) =>
    button.addEventListener('click', () => {
      select();
      const iso = button.dataset.toggle;
      if (expanded.has(iso)) expanded.delete(iso);
      else expanded.add(iso);
      render(monday);
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

export function initSchedule() {
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
    select();
    state.scheduleWeek = 0;
    loadSchedule();
  });
}
