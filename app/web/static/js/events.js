// Олимпиады и конкурсы ученика и заказ справок в школе — маршруты мобильного
// API (приложение «Сетевой город» 2.3.1). Открываются из настроек.
'use strict';

import { api, banner, esc, parseIso, MONTHS, showFailure, skeleton } from './core.js';
import { notify, tap } from './haptics.js';
import { pushScreen, reopenOnRefresh } from './screens.js';

function dateText(iso) {
  if (!iso) return '';
  const date = parseIso(iso);
  return `${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
}

function period(event) {
  const start = dateText(event.start);
  const end = dateText(event.end);
  return start && end && start !== end ? `${start} — ${end}` : start || end;
}

export async function openEvents() {
  const body = pushScreen('Олимпиады и конкурсы');
  reopenOnRefresh(body, openEvents);
  body.innerHTML = skeleton(3);
  try {
    const { data } = await api('/api/events');
    if (!data.available) {
      body.innerHTML = '<div class="empty">Мероприятия есть только в мобильном режиме API</div>';
      return;
    }
    body.innerHTML = data.items.length ? data.items.map((event) => `
      <div class="card event-card">
        <div class="sched-day">${esc(event.name)}</div>
        <div class="event-meta muted small">
          ${event.status ? `<span class="event-status">${esc(event.status)}</span>` : ''}
          ${[event.kind, event.level, period(event)].filter(Boolean).map((text) => `<span>${esc(text)}</span>`).join('')}
        </div>
        ${event.subject && event.subject !== event.name ? `<div class="detail-text">${esc(event.subject)}</div>` : ''}
        ${event.description ? `<div class="detail-text">${esc(event.description)}</div>` : ''}
        ${[event.organization, event.location].filter(Boolean).map((text) => `<div class="muted small">${esc(text)}</div>`).join('')}
      </div>`).join('') : '<div class="empty">Мероприятий нет</div>';
  } catch (error) {
    showFailure(body, error, `<div class="empty">${esc(error.message)}</div>`);
  }
}

export async function openReferences() {
  const body = pushScreen('Заказать справку');
  reopenOnRefresh(body, openReferences);
  body.innerHTML = skeleton(2);
  let data;
  try {
    ({ data } = await api('/api/references/types'));
  } catch (error) {
    showFailure(body, error, `<div class="empty">${esc(error.message)}</div>`);
    return;
  }
  if (!data.available) {
    body.innerHTML = `<div class="empty">${esc(data.message || 'Заказ справок есть только в мобильном режиме API')}</div>`;
    return;
  }
  if (!data.items.length) {
    body.innerHTML = '<div class="empty">Школа не предлагает справок для заказа</div>';
    return;
  }
  body.innerHTML = `<div class="muted small">Заказ уходит в школу. Когда справка будет готова, школа сообщит.</div>
    ${data.items.map((item) => `<div class="card reference-row">
      <span>${esc(item.name)}</span>
      <button class="btn" data-reference="${esc(item.id)}">Заказать</button></div>`).join('')}`;
  body.querySelectorAll('[data-reference]').forEach((button) => button.addEventListener('click', async () => {
    const name = button.previousElementSibling.textContent;
    if (!window.confirm(`Заказать «${name}»? Заявка уйдёт в школу.`)) return;
    tap();
    button.disabled = true;
    try {
      await api('/api/references', { method: 'POST', body: JSON.stringify({ type_id: Number(button.dataset.reference) }) });
      notify('success');
      button.textContent = 'Заказано';
    } catch (error) {
      notify('error');
      banner(error.message, true);
      button.disabled = false;
    }
  }));
}
