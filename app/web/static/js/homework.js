// «Д/З»: домашние задания карточками с поиском, как во вкладке приложения.
//
// Отметка «выполнено» хранится на сервере и общая с /dz в боте.
'use strict';

import { $, api, banner, dayMonth, errorCard, esc, isoToday, observedTime, skeleton, state } from './core.js';
import { ICONS } from './icons.js';
import { openHomework } from './details.js';
import { notify, select, tap } from './haptics.js';

let query = '';
// all — с сегодняшнего дня, todo — только невыполненные, past — прошедшие.
let filter = 'all';

async function loadDone() {
  try {
    const { data } = await api('/api/homework/done');
    state.hwDone = new Set(data.keys);
  } catch {
    state.hwDone = state.hwDone || new Set();
  }
}

export async function loadHomework(force = false) {
  const container = $('homeworkContent');
  const past = filter === 'past';
  const slot = past ? 'homeworkPast' : 'homework';
  if (!state[slot] || force) {
    container.innerHTML = skeleton();
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
      container.innerHTML = errorCard(error, 'hwRetry');
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
    if (filter === 'todo') items = items.filter((item) => !done.has(item.key));
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

  container.innerHTML = items
    .map((item, index) => {
      const isDone = done.has(item.key);
      return `<div class="card hw-card ${isDone ? 'done' : ''}" data-hw="${index}">
          <div class="hw-top">
            <div class="hw-subject">${esc(item.subject)}</div>
            ${item.key ? `<button class="hw-check ${isDone ? 'on' : ''}" data-done="${index}" aria-pressed="${isDone}"
              aria-label="${isDone ? 'Снять отметку' : 'Отметить выполненным'}">${isDone ? '✓' : ''}</button>` : ''}
          </div>
          <div class="hw-text">${esc(item.text) || '—'}</div>
          ${item.published_at
            ? `<div class="hw-observed">Опубликовано: ${esc(observedTime(item.published_at))}</div>`
            : item.observed_at ? `<div class="hw-observed">Замечено ботом: ${esc(observedTime(item.observed_at))}</div>` : ''}
          <div class="hw-foot">
            <span><span class="hw-badge">Д/З</span>${item.attachments.length ? `<span class="hw-badge clip">${ICONS.clip}</span>` : ''}</span>
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

/** Галочка на месте, без перерисовки списка: иначе карточки заново «въезжают». */
function paintCard(button, isDone) {
  button.classList.toggle('on', isDone);
  button.textContent = isDone ? '✓' : '';
  button.setAttribute('aria-pressed', String(isDone));
  button.setAttribute('aria-label', isDone ? 'Снять отметку' : 'Отметить выполненным');
  button.closest('.hw-card')?.classList.toggle('done', isDone);
}

async function toggleDone(item, button) {
  const done = state.hwDone || (state.hwDone = new Set());
  const next = !done.has(item.key);
  // Сразу на экране, сервер — следом: галочка не должна ждать сети.
  const apply = (value) => {
    if (value) done.add(item.key);
    else done.delete(item.key);
    // В «Не сделано» выполненное уходит из списка — тут перерисовка нужна.
    if (filter === 'todo') render();
    else paintCard(button, value);
  };
  apply(next);
  next ? notify('success') : tap('soft');
  try {
    await api('/api/homework/done', { method: 'POST', body: JSON.stringify({ key: item.key, done: next }) });
  } catch (error) {
    apply(!next);
    notify('error');
    banner(error.message, true);
  }
}

export function initHomework() {
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
