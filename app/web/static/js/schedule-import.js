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
    throw new Error('Фото слишком большое. Обрежьте его до нужного класса');
  } finally { bitmap.close(); }
}

export async function openScheduleImport(date = isoToday()) {
  if (refuseReadOnly()) return;
  const body = openSheet('Своё расписание', `
    <div class="note-form">
      <p class="muted small">Фото из школьного чата прочитает Gemini. Обрежьте его до нужного класса: перед добавлением мы покажем, как ИИ поняла расписание, и дадим всё исправить. Расписание заменит уроки только на выбранные даты; задания и оценки останутся из дневника.</p>
      <label class="field-label">Дата / день нужной недели<input class="field import-date" type="date" value="${esc(date)}"></label>
      <label class="field-label">Период<select class="field import-mode"><option value="day">На один день</option><option value="week">На выбранную неделю</option></select></label>
      <label class="field-label">Фото расписания<input class="field import-photo" type="file" accept="image/jpeg,image/png,image/webp"></label>
      <button class="btn import-read">Распознать фото</button>
      <div class="muted small import-status" role="status"></div>
      <div class="import-existing"></div>
    </div>`);
  const status = body.querySelector('.import-status');
  const button = body.querySelector('.import-read');
  button.addEventListener('click', async () => {
    button.disabled = true;
    status.textContent = 'Читаем расписание…';
    try {
      const image = await photoData(body.querySelector('.import-photo').files[0]);
      const { data } = await api('/api/schedule/recognize', { method: 'POST', body: JSON.stringify({ image,
        date: body.querySelector('.import-date').value, mode: body.querySelector('.import-mode').value }) });
      preview(data.days);
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
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
  return `<div class="import-lesson">
    <label class="field-label">№<input class="field" data-key="number" type="number" min="0" max="30" value="${esc(item.number ?? '')}"></label>
    <label class="field-label">Предмет<input class="field" data-key="subject" maxlength="120" value="${esc(item.subject || '')}"></label>
    <div class="edit-times">
      <label class="field-label">Начало<input class="field" data-key="start" type="time" value="${esc(item.start || '')}"></label>
      <label class="field-label">Конец<input class="field" data-key="end" type="time" value="${esc(item.end || '')}"></label>
    </div>
    <label class="field-label">Кабинет<input class="field" data-key="room" maxlength="40" value="${esc(item.room || '')}"></label>
    <button class="link" data-remove-lesson>Убрать урок</button>
  </div>`;
}

function preview(days, saved = false) {
  const body = openSheet('Так ИИ поняла расписание', `<div class="note-form import-preview">
    <p class="muted small">Всё верно? Исправьте предметы, порядок, время и кабинеты перед добавлением. День без уроков означает отмену всех занятий. Время можно оставить пустым.</p>
    ${days.map(day => `<section data-import-day>
      <h3>${esc(fullDate(day.date))}</h3>
      <label class="field-label">Дата<input class="field" data-day-date type="date" value="${esc(day.date)}"></label>
      <div data-import-lessons>${day.lessons.map(lessonRow).join('')}</div>
      <button class="btn outline" data-add-import-lesson>+ Урок</button>
      <button class="link" data-remove-day>Не заменять этот день</button>
    </section>`).join('')}
    <div class="muted small import-status" role="status"></div>
    <button class="btn" data-import-save>Да, добавить в моё расписание</button>
    <button class="btn outline" data-import-share>Предложить одноклассникам</button>
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
