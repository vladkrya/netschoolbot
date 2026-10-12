// «Д/З»: домашние задания карточками с поиском, как во вкладке приложения.
//
// Отметка «выполнено» хранится на сервере и общая с /dz в боте.
'use strict';

import {
  $, addDays, api, dayMonth, errorCard, esc, isoToday, observedTime, showFailure, showLoading, skeleton, state,
  WEEKDAYS_FULL, weekdayIndex,
} from './core.js';
import { ICONS } from './icons.js';
import { homeworkTimes, isDoneItem, loadDone, openHomework, saveDone } from './details.js';
import { select } from './haptics.js';

let query = '';
// all — с сегодняшнего дня, todo — только невыполненные, past — прошедшие.
let filter = 'all';

export async function loadHomework(force = false) {
  const container = $('homeworkContent');
  const past = filter === 'past';
  const slot = past ? 'homeworkPast' : 'homework';
  if (!state[slot] || force) {
    showLoading(container, skeleton());
    try {
      const params = [past ? 'past=1' : '', force ? 'refresh=1' : ''].filter(Boolean).join('&');
      const [{ data }] = await Promise.all([
        api(`/api/homework${params ? `?${params}` : ''}`, {
          onFresh: (fresh) => { state[slot] = fresh.data; },
        }),
        loadDone(),
      ]);
      state[slot] = data;
    } catch (error) {
      showFailure(container, error, errorCard(error, 'hwRetry'));
      $('hwRetry')?.addEventListener('click', () => loadHomework(true));
      return;
    }
  } else if (!state.hwDone) {
    await loadDone();
  }
  render();
}

function visibleItems() {
  const today = isoToday();
  const needle = query.trim().toLowerCase();
  const done = state.hwDone || new Set();
  const source = filter === 'past' ? state.homeworkPast : state.homework;
  let items = source.items;
  if (filter === 'past') {
    items = items.filter((item) => item.date < today).slice().reverse();
  } else {
    // Прошедшее здесь не показываем: домашка нужна на «сейчас и вперёд».
    items = items.filter((item) => item.date >= today);
    if (filter === 'todo') items = items.filter((item) => !isDoneItem(item, done));
  }
  return items.filter((item) => !needle || `${item.subject} ${item.text}`.toLowerCase().includes(needle));
}

function render() {
  const items = visibleItems();
  const done = state.hwDone || new Set();
  const titles = { all: 'Домашние задания', todo: 'Не сделано', past: 'Прошедшие задания' };
  $('hwCount').textContent = `${titles[filter]} (${items.length})`;
  $('hwFilters').querySelectorAll('[data-filter]').forEach((chip) =>
    chip.classList.toggle('active', chip.dataset.filter === filter));

  const container = $('homeworkContent');
  if (!items.length) {
    const empty = query.trim() ? 'Ничего не найдено' : filter === 'todo' ? 'Всё сделано 🎉' : 'Заданий нет 🎉';
    container.innerHTML = `<div class="empty big">${empty}</div>`;
    return;
  }

  const perDay = new Map();
  for (const item of items) perDay.set(item.date, (perDay.get(item.date) || 0) + 1);
  container.innerHTML = items
    .map((item, index) => {
      const isDone = isDoneItem(item, done);
      // Заголовок дня перед первым его заданием (виден только в новом дизайне).
      const heading = item.date !== items[index - 1]?.date
        ? `<div class="hw-day"><span>${esc(dayHeading(item.date))}</span><span class="hw-day-count">${perDay.get(item.date)}</span></div>`
        : '';
      return `${heading}<div class="card hw-card ${isDone ? 'done' : ''}" data-hw="${index}">
          <div class="hw-top">
            <div class="hw-subject">${esc(item.subject)}</div>
            ${item.auto_done ? '<span class="hw-check on auto" title="Задание не предусмотрено — отмечено само">✓</span>'
              : item.key ? `<button class="hw-check ${isDone ? 'on' : ''}" data-done="${index}" aria-pressed="${isDone}"
              aria-label="${isDone ? 'Снять отметку' : 'Отметить выполненным'}">${isDone ? '✓' : ''}</button>` : ''}
          </div>
          <div class="hw-text">${esc(item.text) || '—'}</div>
          ${homeworkTimes(item, observedTime).map((line) => `<div class="hw-observed">${esc(line)}</div>`).join('')}
          <div class="hw-foot">
            <span><span class="hw-badge">Д/З</span>${item.attachments.length ? `<span class="hw-badge clip">${ICONS.clip}</span>` : ''}${item.comment ? `<span class="hw-badge clip" title="Есть комментарий к заданию">${ICONS.chat}</span>` : ''}</span>
            <span class="muted small">${esc(dayMonth(item.date))}</span>
          </div>
        </div>`;
    })
    .join('');
  container.querySelectorAll('[data-hw]').forEach((card) =>
    card.addEventListener('click', (event) => {
      const check = event.target.closest('[data-done]');
      if (check) toggleDone(items[Number(check.dataset.done)], check);
      else openHomework(items[Number(card.dataset.hw)]);
    })
  );
}

function dayHeading(iso) {
  const today = isoToday();
  const near = { [today]: 'Сегодня', [addDays(today, 1)]: 'Завтра', [addDays(today, -1)]: 'Вчера' }[iso];
  return `${near || WEEKDAYS_FULL[weekdayIndex(iso)]}, ${dayMonth(iso)}`;
}

/** Галочка на месте, без перерисовки списка: иначе карточки заново «въезжают». */
function paintCard(button, isDone) {
  button.classList.toggle('on', isDone);
  button.textContent = isDone ? '✓' : '';
  button.setAttribute('aria-pressed', String(isDone));
  button.setAttribute('aria-label', isDone ? 'Снять отметку' : 'Отметить выполненным');
  button.closest('.hw-card')?.classList.toggle('done', isDone);
}

function toggleDone(item, button) {
  // Сразу на экране, сервер — следом: галочка не должна ждать сети.
  // В «Не сделано» выполненное уходит из списка — тут перерисовка нужна.
  saveDone(item, !isDoneItem(item), (value) => (filter === 'todo' ? render() : paintCard(button, value)), 'list');
}

export function initHomework() {
  // Отметили на экране урока или задания — галочки в списке должны совпасть.
  window.addEventListener('netschool:hwdone', (event) => {
    const source = filter === 'past' ? state.homeworkPast : state.homework;
    if (event.detail?.source !== 'list' && source) render();
  });
  $('hwSearch').addEventListener('input', (event) => {
    query = event.target.value;
    if (state.homework) render();
  });
  $('hwFilters').addEventListener('click', (event) => {
    const chip = event.target.closest('[data-filter]');
    if (!chip || chip.dataset.filter === filter) return;
    select();
    filter = chip.dataset.filter;
    loadHomework();
  });
}
