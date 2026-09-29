// «Д/З»: домашние задания карточками с поиском, как во вкладке приложения.
'use strict';

import { $, api, dayMonth, errorCard, esc, isoToday, observedTime, skeleton, state } from './core.js';
import { ICONS } from './icons.js';
import { openHomework } from './details.js';

let query = '';

export async function loadHomework(force = false) {
  const container = $('homeworkContent');
  if (!state.homework || force) {
    container.innerHTML = skeleton();
    try {
      const { data } = await api(`/api/homework${force ? '?refresh=1' : ''}`);
      state.homework = data;
    } catch (error) {
      container.innerHTML = errorCard(error, 'hwRetry');
      $('hwRetry')?.addEventListener('click', () => loadHomework(true));
      return;
    }
  }
  render();
}

function render() {
  const today = isoToday();
  const needle = query.trim().toLowerCase();
  // Прошедшее не показываем: домашка нужна на «сейчас и вперёд».
  const items = state.homework.items
    .filter((item) => item.date >= today)
    .filter((item) => !needle || `${item.subject} ${item.text}`.toLowerCase().includes(needle));

  $('hwCount').textContent = `Домашние задания (${items.length})`;
  const container = $('homeworkContent');
  if (!items.length) {
    container.innerHTML = `<div class="empty big">${needle ? 'Ничего не найдено' : 'Заданий нет 🎉'}</div>`;
    return;
  }

  container.innerHTML = items
    .map(
      (item, index) => `<div class="card hw-card" data-hw="${index}">
          <div class="hw-subject">${esc(item.subject)}</div>
          <div class="hw-text">${esc(item.text) || '—'}</div>
          ${item.published_at
            ? `<div class="hw-observed">Опубликовано: ${esc(observedTime(item.published_at))}</div>`
            : item.observed_at ? `<div class="hw-observed">Замечено ботом: ${esc(observedTime(item.observed_at))}</div>` : ''}
          <div class="hw-foot">
            <span><span class="hw-badge">Д/З</span>${item.attachments.length ? `<span class="hw-badge clip">${ICONS.clip}</span>` : ''}</span>
            <span class="muted small">${esc(dayMonth(item.date))}</span>
          </div>
        </div>`
    )
    .join('');
  container.querySelectorAll('[data-hw]').forEach((card) =>
    card.addEventListener('click', () => openHomework(items[Number(card.dataset.hw)]))
  );
}

export function initHomework() {
  $('hwSearch').addEventListener('input', (event) => {
    query = event.target.value;
    if (state.homework) render();
  });
}
