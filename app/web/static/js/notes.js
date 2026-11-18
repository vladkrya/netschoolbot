// Свои задачи ученика («принести циркуль», «повторить параграф»).
//
// Задачи хранятся на сервере (app/web/extras.py), видны в «Заданиях» рядом
// со школьными и попадают в вечернее напоминание бота.
'use strict';

import { addDays, api, banner, esc, isoToday, refuseReadOnly, state, weekdayIndex } from './core.js';
import { closeSheet, openSheet } from './sheet.js';
import { notify, tap } from './haptics.js';
import { dequeue, pendingOp, pendingOps, realId, send } from './outbox.js';

/* ───────────────────────── Проверочные работы ───────────────────────── */

// Те же правила, что у бота (app/domain/insights.py).
const TEST_WORDS = ['контрольн', 'самостоят', 'проверочн', 'тест', 'зачет', 'зачёт', 'экзамен',
  'диктант', 'срез', 'впр', 'диагностич', 'изложени', 'сочинени', 'аттестац'];

export function isTestWork(item) {
  const kind = String(item?.type || '').toLowerCase().replace(/ё/g, 'е');
  if (TEST_WORDS.some((word) => kind.includes(word.replace(/ё/g, 'е')))) return true;
  return Number(item?.weight) >= 3;
}

/* ───────────────────────────── Свои задачи ───────────────────────────── */

export async function loadNotes(force = false) {
  if (state.notes && !force) return state.notes;
  try {
    const today = isoToday();
    const { data } = await api(`/api/notes?from=${addDays(today, -30)}&to=${addDays(today, 120)}`);
    state.notes = withPending(data.items || []);
  } catch {
    state.notes = state.notes || withPending([]);
  }
  return state.notes;
}

// Без сети задача меняется только здесь, а изменение ждёт в очереди
// (outbox.js) одной записью на задачу: новая — под временным отрицательным
// id и путём создания, уже известная серверу — правкой по её id.
const NOTE_FIELDS = ['date', 'text', 'subject', 'time', 'remind', 'done'];
const opId = (id) => `note:${id}`;
const isTemp = (id) => Number(id) < 0;

/** Задачи с сервера (или его сохранённая копия) плюс ждущие отправки. */
function withPending(notes) {
  let result = [...notes];
  for (const { id, path, body } of pendingOps('note:')) {
    const noteId = Number(id.slice(5));
    if (path === '/api/notes') {
      result = result.filter((n) => n.id !== noteId);
      result.push({ id: noteId, date: body.date, text: body.text, subject: body.subject || '',
        time: body.time || '', remind: body.remind ?? null, done: Boolean(body.done) });
    } else if (body.delete) {
      result = result.filter((n) => n.id !== noteId);
    } else {
      result = result.map((n) => (n.id === noteId ? { ...n, ...pick(body) } : n));
    }
  }
  return sortNotes(result);
}

const pick = (body) => Object.fromEntries(NOTE_FIELDS.filter((key) => key in body).map((key) => [key, body[key]]));

/** Изменение задачи: к ждущему изменению той же задачи — дописать.
 *  was — те же поля до изменения: вернули как было — отправлять нечего. */
function saveNote(id, changes, was = null) {
  const prev = pendingOp(opId(id));
  if (isTemp(id) && prev) return send(opId(id), '/api/notes', { ...prev.body, ...changes });
  const base = prev && !prev.body.delete ? prev.body : {};
  return send(opId(id), `/api/notes/${id}`, { ...base, ...changes }, was);
}

// Созданная без сети задача дошла до сервера — временный id меняем на настоящий.
document.addEventListener('netschool:note-created', ({ detail: { temp, note } }) => {
  if (!state.notes) return;
  state.notes = state.notes.map((n) => (n.id === temp ? { ...n, id: note.id } : n));
});

/** Задачи в виде заданий для общего списка «Заданий». */
export function notesAsItems(notes = state.notes || []) {
  return notes.map((note) => ({
    own: true, id: note.id, date: note.date, subject: note.subject || 'Своя задача',
    text: note.text, done: note.done, attachments: [], time: note.time || '', remind: note.remind ?? null,
  }));
}

export async function setNoteDone(item, done) {
  if (refuseReadOnly()) return false;
  const id = realId(item.id);
  const note = (state.notes || []).find((n) => n.id === id);
  if (note) note.done = done;
  item.done = done;
  done ? notify('success') : tap('soft');
  try {
    await saveNote(id, { done }, { done: !done });
  } catch (error) {
    if (note) note.done = !done;
    item.done = !done;
    notify('error');
    banner(error.message, true);
    return false;
  }
  return true;
}

export async function deleteNote(item) {
  if (refuseReadOnly()) return false;
  const id = realId(item.id);
  try {
    // Ещё не дошла до сервера — достаточно забыть.
    if (isTemp(id) && pendingOp(opId(id))) dequeue(opId(id));
    else await send(opId(id), `/api/notes/${id}`, { delete: true });
    state.notes = (state.notes || []).filter((n) => n.id !== id);
    notify('success');
    return true;
  } catch (error) {
    notify('error');
    banner(error.message, true);
    return false;
  }
}

// За сколько минут до задачи напомнить (те же значения принимает сервер).
// Подпись в списке и короткая — под задачей.
const REMIND_OPTIONS = [[5, 'За 5 минут', 'за 5 мин'], [10, 'За 10 минут', 'за 10 мин'],
  [15, 'За 15 минут', 'за 15 мин'], [30, 'За 30 минут', 'за 30 мин'], [60, 'За час', 'за час'],
  [120, 'За 2 часа', 'за 2 ч'], [1440, 'За сутки', 'за сутки'], [0, 'В момент задачи', 'вовремя']];

/** Подпись под задачей: «16:30 · 🔔 за 15 мин». */
export function noteWhen(note) {
  if (!note.time) return '';
  const remind = REMIND_OPTIONS.find(([minutes]) => minutes === note.remind);
  return remind ? `${note.time} · 🔔 ${remind[2]}` : note.time;
}

const sortNotes = (notes) => notes.sort((a, b) => a.date.localeCompare(b.date)
  || Number(!a.time) - Number(!b.time) || (a.time || '').localeCompare(b.time || '') || a.id - b.id);

/** Шторка «Своя задача»: текст, день, время, напоминание и предмет. С note —
 *  правка уже созданной задачи (и кнопка «Удалить»). */
export function openNoteEditor(subjects, onSaved, { date = null, subject = '', note = null } = {}) {
  if (refuseReadOnly()) return;
  tap();
  const today = isoToday();
  let day = note?.date || date || addDays(today, weekdayIndex(today) >= 4 ? 7 - weekdayIndex(today) : 1);
  const chosen = note ? note.subject : subject;
  const names = chosen && !subjects.includes(chosen) ? [chosen, ...subjects] : subjects;
  const remindChosen = note ? note.remind : 15;
  const body = openSheet(note ? 'Своя задача' : 'Новая задача', `
    <div class="note-form">
      <textarea class="field note-text" maxlength="500" rows="3" placeholder="Например: принести циркуль">${esc(note?.text || '')}</textarea>
      <div class="edit-times">
        <label class="field-label">День
          <input class="field note-date" type="date" value="${day}" min="${addDays(today, -7)}"></label>
        <label class="field-label">Время
          <input class="field note-time" type="time" value="${esc(note?.time || '')}"></label>
      </div>
      <label class="field-label">Напомнить
        <select class="field note-remind">
          <option value="">Не напоминать</option>
          ${REMIND_OPTIONS.map(([minutes, label]) => `<option value="${minutes}" ${minutes === remindChosen ? 'selected' : ''}>${label}</option>`).join('')}
        </select></label>
      <label class="field-label">Предмет
        <select class="field note-subject">
          <option value="">Без предмета</option>
          ${names.map((name) => `<option ${name === chosen ? 'selected' : ''}>${esc(name)}</option>`).join('')}
        </select></label>
      <button class="btn note-save">${note ? 'Сохранить' : 'Добавить'}</button>
      ${note ? '<button class="btn outline note-remove">Удалить задачу</button>' : ''}
      <div class="muted small">Бот напомнит вечером накануне, если задача не отмечена. Со временем — ещё и заранее, в Telegram и push-уведомлением.</div>
    </div>`);
  const text = body.querySelector('.note-text');
  const timeInput = body.querySelector('.note-time');
  const remind = body.querySelector('.note-remind');
  // Без времени напоминать не к чему: выбор гаснет. Не прячется — иначе
  // шторка подпрыгивает, когда вводят время.
  const sync = () => { remind.disabled = !timeInput.value; };
  timeInput.addEventListener('input', sync);
  sync();
  if (!note) setTimeout(() => text.focus(), 50);
  body.querySelector('.note-remove')?.addEventListener('click', async () => {
    tap();
    if (!confirm('Удалить свою задачу?')) return;
    if (await deleteNote(note)) {
      closeSheet();
      onSaved?.(null);
    }
  });
  body.querySelector('.note-save').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const value = text.value.trim();
    day = body.querySelector('.note-date').value || day;
    if (!value) { text.focus(); notify('error'); return; }
    const time = timeInput.value;
    const remindValue = remind.value;
    const payload = {
      date: day, text: value, subject: body.querySelector('.note-subject').value,
      time, remind: time && remindValue !== '' ? Number(remindValue) : null,
    };
    button.disabled = true;
    try {
      const id = note ? realId(note.id) : -Date.now();
      const { queued, data: sent } = note
        ? await saveNote(id, payload, {
          date: note.date, text: note.text, subject: note.subject || '', time: note.time || '', remind: note.remind ?? null,
        })
        : await send(opId(id), '/api/notes', payload);
      // Без сети задача пока только здесь — такой, какой её ввели.
      const data = queued ? { done: false, ...note, ...payload, id } : sent;
      const others = (state.notes || []).filter((n) => n.id !== data.id);
      state.notes = sortNotes([...others, data]);
      notify('success');
      closeSheet();
      onSaved?.(data);
    } catch (error) {
      button.disabled = false;
      notify('error');
      banner(error.message, true);
    }
  });
}

/* ──────────────────────────────── Серии ──────────────────────────────── */
