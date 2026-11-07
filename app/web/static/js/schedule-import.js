// Распознанное расписание сначала проверяет человек; публикация — отдельное действие.
'use strict';
import { api, esc, isoToday, mondayOf, addDays, fullDate, refuseReadOnly } from './core.js';
import { openSheet, closeSheet } from './sheet.js';

export const importScheduleButton = () => '<button class="btn outline" data-import-schedule>Расписание по фото · расписания класса</button>';
export function bindImportSchedule(container, date = isoToday()) {
  container.querySelector('[data-import-schedule]')?.addEventListener('click', () => openScheduleImport(date));
}
const post = (body) => api('/api/schedule/imports', { method: 'POST', body: JSON.stringify(body) });
function changed() {
  window.dispatchEvent(new Event('netschool:scheduleimported'));
}

function importSteps(current) {
  const labels = ['Фото', 'Проверка', 'Готово'];
  return `<ol class="import-steps" aria-label="Этапы добавления расписания">${labels.map((label, index) =>
    `<li class="${index === current ? 'active' : index < current ? 'done' : ''}"><span>${index < current ? '✓' : index + 1}</span>${label}</li>`).join('')}</ol>`;
}

function fileDescription(files, fallback) {
  if (!files?.length) return fallback;
  return files.length === 1 ? files[0].name : `Выбрано фото: ${files.length}`;
}

// Масштабируем на устройстве: исходная фотография не уходит на сервер.
async function photoData(file) {
  if (!file || file.size > 25 * 1024 * 1024) throw new Error('Выберите фото размером до 25 МБ');
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, 2000 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const context = canvas.getContext('2d');
    context.fillStyle = '#fff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    let url;
    for (const quality of [0.9, 0.75, 0.55, 0.35]) {
      url = canvas.toDataURL('image/jpeg', quality);
      if (url.length < 900000) return url.split(',')[1];
    }
    throw new Error('Фото слишком большое. Обрежьте его до одного дня');
  } finally { bitmap.close(); }
}

export async function openScheduleImport(date = isoToday()) {
  if (refuseReadOnly()) return;
  const body = openSheet('Своё расписание', `
    <div class="note-form import-flow">
      ${importSteps(0)}
      <section class="import-hero">
        <span class="import-hero-icon" aria-hidden="true">✦</span>
        <div><strong>Добавим расписание с фото</strong><p>Сначала покажем результат, затем его можно спокойно поправить перед сохранением.</p></div>
      </section>
      <div class="import-controls">
        <label class="field-label">Дата<input class="field import-date" type="date" value="${esc(date)}"></label>
        <label class="field-label">Период<select class="field import-mode"><option value="day">Один день</option><option value="week">Неделя</option></select></label>
      </div>
      <p class="import-ai-note"><span aria-hidden="true">✦</span> Gemini найдёт строку вашего класса и колонку выбранного дня. Перед сохранением вы сможете всё проверить и исправить.</p>
      <label class="import-file-card primary">
        <input class="import-photo" type="file" accept="image/jpeg,image/png,image/webp" multiple>
        <span class="import-file-icon" aria-hidden="true">▧</span><span><strong>Фото расписания уроков</strong><small class="import-photo-copy">Выберите фото из галереи или камеры</small></span><b>Выбрать</b>
      </label>
      <div class="muted small import-photo-hint">Для дня — одно фото. Для недели — общий снимок или до семи фото по порядку: Пн, Вт, Ср…</div>
      <aside class="import-privacy-note" role="note"><span aria-hidden="true">⌁</span><p><strong>Перед отправкой</strong> обрежьте фото до нужного класса и дня. Не загружайте ФИО, лица, оценки, переписки или другие личные данные.</p></aside>
      <label class="import-file-card">
        <input class="import-bells" type="file" accept="image/jpeg,image/png,image/webp">
        <span class="import-file-icon" aria-hidden="true">◷</span><span><strong>Расписание звонков</strong><small class="import-bells-copy">Необязательно — время возьмём из дневника</small></span><b>Добавить</b>
      </label>
      <div class="import-analysis" hidden aria-live="polite"><span class="import-orbit" aria-hidden="true"></span><div><strong>Анализируем изображение</strong><small>Сверяем предметы, кабинеты и время с дневником…</small></div></div>
      <button class="btn import-read">Продолжить к проверке <span aria-hidden="true">→</span></button>
      <div class="muted small import-status" role="status"></div>
      <div class="import-existing"></div>
    </div>`);
  const status = body.querySelector('.import-status');
  const button = body.querySelector('.import-read');
  const photoInput = body.querySelector('.import-photo');
  const bellInput = body.querySelector('.import-bells');
  photoInput.addEventListener('change', () => {
    body.querySelector('.import-photo-copy').textContent = fileDescription(photoInput.files, 'Выберите фото из галереи или камеры');
  });
  bellInput.addEventListener('change', () => {
    body.querySelector('.import-bells-copy').textContent = fileDescription(bellInput.files, 'Необязательно — время возьмём из дневника');
  });
  button.addEventListener('click', async () => {
    button.disabled = true;
    button.classList.add('loading');
    body.classList.add('analyzing');
    body.querySelector('.import-analysis').hidden = false;
    status.textContent = '';
    try {
      const mode = body.querySelector('.import-mode').value;
      const files = [...body.querySelector('.import-photo').files];
      const bellFile = body.querySelector('.import-bells').files[0];
      if (!files.length && !bellFile) throw new Error('Выберите расписание уроков или звонков');
      if (mode === 'day' && files.length > 1) throw new Error('Для одного дня выберите одно фото расписания');
      if (files.length > 7) throw new Error('Для недели можно выбрать до семи фото');
      const images = await Promise.all(files.map(photoData));
      const bellsImage = bellFile ? await photoData(bellFile) : '';
      const { data } = await api('/api/schedule/recognize', { method: 'POST', body: JSON.stringify({ images, bellsImage,
        date: body.querySelector('.import-date').value, mode }) });
      preview(data.days);
    } catch (error) { status.textContent = error.message; }
    finally {
      button.disabled = false;
      button.classList.remove('loading');
      body.classList.remove('analyzing');
      body.querySelector('.import-analysis').hidden = true;
    }
  });
  try {
    const { data } = await api('/api/schedule/imports');
    if (!body.isConnected || body.querySelector('.import-read') !== button) return;
    if (!data.recognition) { button.disabled = true; status.textContent = 'Распознавание Gemini пока не настроено'; }
    const list = body.querySelector('.import-existing');
    const draw = () => {
      const selected = body.querySelector('.import-date').value;
      const monday = mondayOf(selected || date);
      const personal = data.days.filter(day => day.date >= monday && day.date <= addDays(monday, 6));
      list.innerHTML = `<h3>Мои замены на эту неделю</h3>${personal.length
        ? '<button class="btn outline import-personal">Проверить, поделиться или удалить</button>' : '<p class="muted small">Сохранённых замен нет</p>'}
        <h3>Расписания моего класса</h3>${data.warning ? `<p class="muted small">${esc(data.warning)}</p>` : ''}
        ${data.shared.length ? data.shared.map((item, index) => `<button class="btn outline" data-shared="${index}">${esc(item.days[0].date)} — ${esc(item.days.at(-1).date)} · проверить и применить</button>`).join('')
          : '<p class="muted small">Пока никто не поделился расписанием</p>'}`;
      list.querySelector('.import-personal')?.addEventListener('click', () => preview(personal, true));
      list.querySelectorAll('[data-shared]').forEach(el => el.addEventListener('click', () => preview(data.shared[Number(el.dataset.shared)].days)));
    };
    body.querySelector('.import-date').addEventListener('change', draw);
    draw();
  } catch (error) { status.textContent = error.message; }
}

function lessonRow(item = {}) {
  // Равные время начала и конца — частая ошибка OCR, это не время урока.
  const validTime = item.start && item.end && item.end > item.start;
  const start = validTime ? item.start : '';
  const end = validTime ? item.end : '';
  const time = validTime ? `${start}–${end}` : 'время не указано';
  const place = item.room ? ` · каб. ${item.room}` : '';
  return `<details class="import-lesson">
    <summary><span><b>Урок ${esc(item.number ?? '—')}</b><small>${esc(item.subject || 'Предмет не указан')}</small></span><em>${esc(time)}${esc(place)}</em></summary>
    <div class="import-lesson-edit">
      <div class="import-lesson-head"><strong>Изменить урок</strong><button class="import-remove" type="button" data-remove-lesson aria-label="Убрать урок">×</button></div>
      <div class="import-lesson-basics"><label class="field-label">№<input class="field" data-key="number" type="number" min="0" max="30" value="${esc(item.number ?? '')}"></label>
      <label class="field-label">Предмет<input class="field" data-key="subject" maxlength="120" value="${esc(item.subject || '')}"></label></div>
      <div class="edit-times">
      <label class="field-label">Начало<input class="field" data-key="start" type="time" value="${esc(start)}"></label>
      <label class="field-label">Конец<input class="field" data-key="end" type="time" value="${esc(end)}"></label>
      </div>
      <label class="field-label">Кабинет<input class="field" data-key="room" maxlength="40" value="${esc(item.room || '')}"></label>
    </div>
  </details>`;
}

function preview(days, saved = false) {
  const total = days.reduce((count, day) => count + day.lessons.length, 0);
  const body = openSheet('Проверьте расписание', `<div class="note-form import-preview import-flow">
    ${importSteps(1)}
    <section class="import-hero success"><span class="import-hero-icon" aria-hidden="true">✓</span><div><strong>Готово к проверке</strong><p>Нашли ${total} ${total === 1 ? 'урок' : total < 5 ? 'урока' : 'уроков'}. Проверьте детали — ничего ещё не сохранено.</p></div></section>
    ${days.map(day => `<section data-import-day>
      <div class="import-day-head"><h3>${esc(fullDate(day.date))}</h3><button class="link" type="button" data-remove-day>Не добавлять</button></div>
      <label class="field-label">Дата<input class="field" data-day-date type="date" value="${esc(day.date)}"></label>
      <div data-import-lessons>${day.lessons.map(lessonRow).join('')}</div>
      <button class="btn outline import-add-lesson" data-add-import-lesson>+ Добавить урок</button>
    </section>`).join('')}
    <div class="muted small import-status" role="status"></div>
    <button class="btn" data-import-save>Сохранить в моё расписание</button>
    <button class="btn outline" data-import-share>Поделиться с одноклассниками</button>
    <p class="muted small import-share-note">Его увидят только ученики той же школы и класса. Каждый сам решит, добавлять ли расписание.</p>
    ${saved ? '<button class="btn outline" data-import-delete>Убрать эти замены и вернуть дневник</button>' : ''}
  </div>`);
  const status = body.querySelector('.import-status');
  body.addEventListener('click', event => {
    if (event.target.closest('[data-remove-lesson]')) event.target.closest('.import-lesson').remove();
    if (event.target.closest('[data-remove-day]')) event.target.closest('[data-import-day]').remove();
    const add = event.target.closest('[data-add-import-lesson]');
    if (add) add.closest('section').querySelector('[data-import-lessons]').insertAdjacentHTML('beforeend', lessonRow());
  });
  const read = () => [...body.querySelectorAll('[data-import-day]')].map(section => ({
    date: section.querySelector('[data-day-date]').value,
    lessons: [...section.querySelectorAll('.import-lesson')].map(row => Object.fromEntries(
      [...row.querySelectorAll('[data-key]')].map(input => [input.dataset.key,
        input.dataset.key === 'number' ? (input.value === '' ? null : Number(input.value)) : input.value.trim()]))),
  }));
  const run = async (action) => {
    const buttons = body.querySelectorAll('[data-import-save], [data-import-share], [data-import-delete]');
    buttons.forEach(button => { button.disabled = true; });
    status.textContent = 'Сохраняем…';
    try {
      await post({ action, days: action === 'delete' ? days : read() });
      if (action === 'share') {
        status.textContent = 'Предложено классу: одноклассники смогут проверить и добавить это расписание себе';
      } else { changed(); closeSheet(); }
    } catch (error) { status.textContent = error.message; }
    finally { buttons.forEach(button => { button.disabled = false; }); }
  };
  body.querySelector('[data-import-save]').addEventListener('click', () => run('save'));
  body.querySelector('[data-import-share]').addEventListener('click', () => run('share'));
  body.querySelector('[data-import-delete]')?.addEventListener('click', () => run('delete'));
}
