// Ответ ученика на домашнее задание и вход на дистанционный урок —
// маршруты мобильного API (приложение «Сетевой город» 2.3.1). В веб-режиме
// сервер отвечает `available: false`, и блок ответа не показывается.
'use strict';

import { api, banner, esc, escRich, openExternal } from './core.js';
import { notify, tap } from './haptics.js';
import { saveAnswers, showAnswers } from './appearance.js';
import { getPref, setPref } from './prefs.js';

const MAX_FILE_MB = 20;

/** Место под ответ: заполняется после открытия экрана. */
export function answerSlot(item) {
  return item.id ? `<div class="hw-answer" data-answer-for="${esc(item.id)}" hidden></div>` : '';
}

// Подсказку «можно скрыть» показываем на первых трёх экранах с блоком ответа.
const HINT_SHOWS = 3;

function hintShown() {
  return Number(getPref('answerHint')) || 0;
}

/** Подгрузить ответы для только что открытого экрана. */
export function loadAnswers(body, items) {
  if (!showAnswers()) return;
  let hinted = false;
  for (const item of items) {
    const slot = item.id && body.querySelector(`[data-answer-for="${item.id}"]`);
    if (!slot) continue;
    api(`/api/homework/${encodeURIComponent(item.id)}/answer`)
      .then(({ data }) => {
        if (!data.available || !showAnswers()) return;
        paint(slot, item.id, data);
        // Одна подсказка на экран, а не под каждым заданием урока.
        if (!hinted && hintShown() < HINT_SHOWS) {
          hinted = true;
          setPref('answerHint', String(hintShown() + 1));
          addHint(body, slot);
        }
      })
      .catch(() => { /* без блока ответа экран всё равно полезен */ });
  }
}

function addHint(body, slot) {
  const hint = document.createElement('div');
  hint.className = 'answer-hint muted small';
  hint.innerHTML = `Не отвечаете на задания через приложение?
    <button class="link" type="button" data-answer-hide>Скрыть этот блок</button>`;
  slot.after(hint);
  hint.querySelector('[data-answer-hide]').addEventListener('click', () => {
    tap();
    saveAnswers(false);
    setPref('answerHint', String(HINT_SHOWS));
    body.querySelectorAll('.hw-answer, .answer-hint').forEach((el) => el.remove());
    banner('Блок ответа скрыт. Вернуть — в настройках, раздел «Оформление».', false, 5000);
  });
}

function paint(slot, id, answer, editing = false) {
  const files = answer.files || [];
  const hasText = Boolean(answer.text);
  slot.hidden = false;
  slot.innerHTML = `
    <div class="detail-label">Мой ответ</div>
    ${editing || !hasText
      ? `<textarea class="answer-text" rows="3" maxlength="10000" placeholder="Текст ответа — его увидит учитель">${esc(answer.text || '')}</textarea>
         <div class="answer-actions">
           <button class="btn" data-answer-save>Сохранить</button>
           ${hasText ? '<button class="btn outline" data-answer-cancel>Отмена</button>' : ''}
         </div>`
      : `<div class="detail-text answer-shown">${escRich(answer.text)}</div>
         <div class="answer-actions">
           <button class="btn outline" data-answer-edit>Изменить</button>
           <button class="btn outline danger" data-answer-clear>Стереть</button>
         </div>`}
    ${files.length ? `<div class="answer-files">${files.map((file) => `
      <div class="answer-file"><span>${esc(file.name)}</span>
        <button class="link danger" data-answer-delete="${esc(file.id)}" aria-label="Убрать файл">Убрать</button></div>`).join('')}</div>` : ''}
    <label class="btn outline answer-attach">Приложить файл<input type="file" data-answer-file hidden></label>`;

  const busy = (on) => slot.querySelectorAll('button, input, textarea').forEach((el) => { el.disabled = on; });
  const run = async (action) => {
    busy(true);
    try {
      const { data } = await action();
      notify('success');
      paint(slot, id, data);
    } catch (error) {
      notify('error');
      banner(error.message, true);
      busy(false);
    }
  };
  const saveText = (text) => run(() => api(`/api/homework/${encodeURIComponent(id)}/answer/text`, {
    method: 'POST', body: JSON.stringify({ text }),
  }));

  slot.querySelector('[data-answer-save]')?.addEventListener('click', () => {
    const text = slot.querySelector('.answer-text').value.trim();
    if (!text && !hasText) return;
    saveText(text);
  });
  slot.querySelector('[data-answer-cancel]')?.addEventListener('click', () => { tap(); paint(slot, id, answer); });
  slot.querySelector('[data-answer-edit]')?.addEventListener('click', () => { tap(); paint(slot, id, answer, true); });
  slot.querySelector('[data-answer-clear]')?.addEventListener('click', () => {
    if (confirm('Стереть текст ответа? Учитель его больше не увидит.')) saveText('');
  });
  slot.querySelectorAll('[data-answer-delete]').forEach((button) => button.addEventListener('click', () => {
    if (!confirm('Убрать файл из ответа?')) return;
    run(() => api(`/api/homework/${encodeURIComponent(id)}/answer/files/${encodeURIComponent(button.dataset.answerDelete)}`, {
      method: 'DELETE',
    }));
  }));
  slot.querySelector('[data-answer-file]').addEventListener('change', (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > MAX_FILE_MB * 1024 * 1024) {
      banner(`Файл больше ${MAX_FILE_MB} МБ`, true);
      return;
    }
    const form = new FormData();
    form.append('file', file, file.name);
    run(() => api(`/api/homework/${encodeURIComponent(id)}/answer/files`, { method: 'POST', body: form }));
  });
}

/** Кнопка «Подключиться» для дистанционного урока. */
export function distanceButton(lesson) {
  return lesson.distance_meeting_id
    ? `<button class="btn distance-join" data-distance="${esc(lesson.distance_meeting_id)}">Подключиться к уроку</button>`
    : '';
}

export function bindDistance(body) {
  body.querySelector('[data-distance]')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    tap();
    button.disabled = true;
    try {
      const { data } = await api(`/api/distance/${encodeURIComponent(button.dataset.distance)}/join`);
      openExternal(data.url);
    } catch (error) {
      notify('error');
      banner(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
}
