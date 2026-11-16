// The board reads the diary's lesson objects and keeps their original indexes
// so its stops can focus the real cards, including after a quiet refresh.
'use strict';

import { esc, isoOf, stillMotion } from './core.js';

const labels = {
  current: 'Идёт урок',
  upcoming: 'Предстоит',
  finished: 'Урок окончен',
  cancelled: 'Отменён',
};

function bellTime(date, value, shift) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value || '');
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  const bell = new Date(`${date}T00:00:00`);
  const time = bell.setHours(hours, minutes, shift, 0);
  return Number.isFinite(time) ? time : null;
}

export function routeBoardModel(lessons, date, now, shift = 0) {
  const today = isoOf(now);
  const rows = lessons.map((lesson, index) => {
    const start = bellTime(date, lesson.start, shift);
    const end = bellTime(date, lesson.end, shift);
    let status = 'upcoming';
    if (lesson.moderation_cancelled) status = 'cancelled';
    else if (date < today || (date === today && end !== null && now >= end)) status = 'finished';
    else if (date === today && start !== null && end !== null && end > start && now >= start) status = 'current';
    return {
      index,
      number: lesson.place ?? lesson.number ?? index + 1,
      name: lesson.subject,
      time: lesson.time || [lesson.start, lesson.end].filter(Boolean).join('–'),
      hasHomework: (lesson.homework || []).some((task) => !task.classwork),
      start,
      status,
    };
  });
  const scheduled = rows.filter(({ status }) => status !== 'cancelled');
  const finished = scheduled.filter(({ status }) => status === 'finished').length;
  const upcoming = scheduled.filter(({ status }) => status === 'upcoming');
  // Custom lessons can appear out of order. Prefer a known, earliest start.
  const next = upcoming.reduce((best, row) => {
    if (!best || (row.start ?? Infinity) < (best.start ?? Infinity)) return row;
    return best;
  }, null);
  const featured = scheduled.find(({ status }) => status === 'current') || next;
  const homework = rows.filter(({ hasHomework }) => hasHomework).length;
  return { rows, total: scheduled.length, finished, featured, homework };
}

function routeFeature({ featured, total, finished }) {
  if (!featured) {
    return `<div class="route-board__heading">
      <span class="route-board__eyebrow">${total && finished === total ? 'Расписание завершено' : 'Расписание'}</span>
      <span class="route-board__subject">${total ? 'Все уроки закончились' : 'Нет запланированных уроков'}</span>
    </div>`;
  }
  const current = featured.status === 'current';
  const title = current ? 'Сейчас' : 'Далее';
  return `<button type="button" class="route-board__heading route-board__jump" data-route-stop="${featured.index}">
    <span class="route-board__eyebrow">${title}</span>
    <span class="route-board__subject">${esc(featured.name)}</span>
    ${featured.time ? `<span class="route-board__time">${esc(featured.time)}</span>` : ''}
    <span class="route-board__arrow" aria-hidden="true">↓</span>
  </button>`;
}

function routeStops({ rows, featured }) {
  return rows.map((row) => {
    const title = [row.number, row.name, row.time, labels[row.status]].filter(Boolean).join(' · ');
    const selected = row.index === featured?.index;
    return `<button type="button" class="route-board__stop" data-route-stop="${row.index}" data-stop-state="${row.status}"
      ${selected ? 'aria-current="step"' : ''} aria-label="${esc(title)}" title="${esc(title)}">${esc(row.number)}</button>`;
  }).join('');
}

export function routeBoardHtml(model, summary) {
  const { total, finished, homework } = model;
  return `<section class="route-board" aria-label="Маршрут уроков">
    ${routeFeature(model)}
    ${total ? `<div class="route-board__progress">
      <progress value="${finished}" max="${total}" aria-label="Завершено уроков"></progress>
      <span>${finished} из ${total}</span>
    </div>` : ''}
    <div class="route-board__stops" role="group" aria-label="Перейти к уроку">
      ${routeStops(model)}
    </div>
    <div class="route-board__summary">${summary}</div>
    ${homework ? `<div class="route-board__filters" role="group" aria-label="Фильтр уроков">
      <button type="button" data-route-filter="all" aria-pressed="true">Все уроки</button>
      <button type="button" data-route-filter="homework" aria-pressed="false">С Д/З <span class="route-board__filter-count">${homework}</span></button>
    </div>` : ''}
  </section>`;
}

// Keep the filter through timer redraws, reset it on a different day.
let filterDate = '';
let filter = 'all';

export function bindRouteBoard(container, date, model) {
  const board = container.querySelector('.route-board');
  if (!board) return;
  const cards = [...container.querySelectorAll(':scope > [data-lesson]')];
  if (filterDate !== date || !model.homework) filter = 'all';
  filterDate = date;

  const applyFilter = (next) => {
    filter = next;
    board.querySelectorAll('[data-route-filter]').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.routeFilter === next));
    });
    cards.forEach((card) => {
      card.hidden = next === 'homework' && !model.rows[Number(card.dataset.lesson)]?.hasHomework;
    });
  };

  applyFilter(filter);
  board.addEventListener('click', (event) => {
    const toggle = event.target.closest('[data-route-filter]');
    if (toggle) {
      applyFilter(toggle.dataset.routeFilter);
      return;
    }
    const stop = event.target.closest('[data-route-stop]');
    if (!stop) return;
    const card = cards.find((item) => item.dataset.lesson === stop.dataset.routeStop);
    if (!card) return;
    if (card.hidden) applyFilter('all');
    card.focus({ preventScroll: true });
    card.scrollIntoView({ block: 'start', behavior: stillMotion() ? 'instant' : 'smooth' });
  });
}
