// Свои задачи ученика («принести циркуль», «повторить параграф») и серии.
//
// Задачи хранятся на сервере (app/web/extras.py), видны в «Заданиях» рядом
// со школьными и попадают в вечернее напоминание бота. Серии — «N дней без
// двоек», «пятёрки подряд», «всё на неделю сделано» — считаются из уже
// загруженных оценок и заданий, отдельных запросов не делают.
'use strict';

import { addDays, api, banner, esc, isoToday, mondayOf, plural, refuseReadOnly, state, weekdayIndex } from './core.js';
import { closeSheet, openSheet } from './sheet.js';
import { notify, select, tap } from './haptics.js';

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
    state.notes = data.items || [];
  } catch {
    state.notes = state.notes || [];
  }
  return state.notes;
}

/** Задачи в виде заданий для общего списка «Заданий». */
export function notesAsItems(notes = state.notes || []) {
  return notes.map((note) => ({
    own: true, id: note.id, date: note.date, subject: note.subject || 'Своя задача',
    text: note.text, done: note.done, attachments: [],
  }));
}

export async function setNoteDone(item, done) {
  if (refuseReadOnly()) return false;
  const note = (state.notes || []).find((n) => n.id === item.id);
  if (note) note.done = done;
  item.done = done;
  done ? notify('success') : tap('soft');
  try {
    await api(`/api/notes/${item.id}`, { method: 'POST', body: JSON.stringify({ done }) });
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
  try {
    await api(`/api/notes/${item.id}`, { method: 'POST', body: JSON.stringify({ delete: true }) });
    state.notes = (state.notes || []).filter((n) => n.id !== item.id);
    notify('success');
    return true;
  } catch (error) {
    notify('error');
    banner(error.message, true);
    return false;
  }
}

/** Шторка «Своя задача»: текст, день и (по желанию) предмет. */
export function openNoteEditor(subjects, onSaved, { date = null, subject = '' } = {}) {
  if (refuseReadOnly()) return;
  tap();
  const today = isoToday();
  let day = date || addDays(today, weekdayIndex(today) >= 4 ? 7 - weekdayIndex(today) : 1);
  const body = openSheet('Своя задача', `
    <div class="note-form">
      <textarea class="field note-text" maxlength="500" rows="3" placeholder="Например: принести циркуль"></textarea>
      <label class="field-label">К какому дню
        <input class="field note-date" type="date" value="${day}" min="${addDays(today, -7)}"></label>
      <label class="field-label">Предмет
        <select class="field note-subject">
          <option value="">Без предмета</option>
          ${subjects.map((name) => `<option ${name === subject ? 'selected' : ''}>${esc(name)}</option>`).join('')}
        </select></label>
      <button class="btn note-save">Добавить</button>
      <div class="muted small">Вечером накануне бот напомнит, если задача не отмечена.</div>
    </div>`);
  const text = body.querySelector('.note-text');
  setTimeout(() => text.focus(), 50);
  body.querySelector('.note-save').addEventListener('click', async (event) => {
    const value = text.value.trim();
    day = body.querySelector('.note-date').value || day;
    if (!value) { text.focus(); notify('error'); return; }
    event.currentTarget.disabled = true;
    try {
      const { data } = await api('/api/notes', {
        method: 'POST',
        body: JSON.stringify({ date: day, text: value, subject: body.querySelector('.note-subject').value }),
      });
      state.notes = [...(state.notes || []), data].sort((a, b) => a.date.localeCompare(b.date) || a.id - b.id);
      notify('success');
      closeSheet();
      onSaved?.(data);
    } catch (error) {
      event.currentTarget.disabled = false;
      notify('error');
      banner(error.message, true);
    }
  });
}

/* ──────────────────────────────── Серии ──────────────────────────────── */

/** Учебные дни (пн–пт) после дня iso и до сегодня включительно. */
function schoolDaysSince(iso, today = isoToday()) {
  let count = 0;
  for (let day = addDays(iso, 1); day <= today; day = addDays(day, 1)) {
    if (weekdayIndex(day) < 5) count += 1;
  }
  return count;
}

/** Серии из оценок года (state.grades) и заданий недели. */
export function streaks(grades, homework, done, notes = []) {
  const result = [];
  const marks = (grades?.subjects || []).flatMap((s) => s.marks || [])
    .filter((m) => Number(m.mark) >= 1 && Number(m.mark) <= 5)
    .sort((a, b) => a.date.localeCompare(b.date) || String(a.marked_at || '').localeCompare(String(b.marked_at || '')));
  if (marks.length) {
    const bad = marks.filter((m) => Number(m.mark) <= 2);
    const since = bad.length ? schoolDaysSince(bad[bad.length - 1].date) : null;
    if (since == null && marks.length >= 5) result.push({ icon: '🛡️', text: 'Ни одной двойки за год' });
    else if (since >= 3) result.push({ icon: '🛡️', text: `${since} ${plural(since, 'учебный день', 'учебных дня', 'учебных дней')} без двоек` });
    let fives = 0;
    for (let i = marks.length - 1; i >= 0 && Number(marks[i].mark) === 5; i -= 1) fives += 1;
    if (fives >= 2) result.push({ icon: '🔥', text: `${fives} ${plural(fives, 'пятёрка', 'пятёрки', 'пятёрок')} подряд` });
  }
  if (homework) {
    const today = isoToday();
    const end = addDays(mondayOf(today), 6);
    const week = homework.filter((item) => item.date >= today && item.date <= end && (item.key || item.auto_done));
    const weekNotes = notes.filter((n) => n.date >= today && n.date <= end);
    const total = week.length + weekNotes.length;
    const left = week.filter((item) => !item.auto_done && !done.has(item.key)).length + weekNotes.filter((n) => !n.done).length;
    if (total && !left) result.push({ icon: '✅', text: 'Всё на эту неделю сделано' });
    else if (total >= 3 && left <= total / 3) result.push({ icon: '💪', text: `Осталось ${left} из ${total} на неделе` });
  }
  return result;
}

export function streaksHtml(items) {
  if (!items.length) return '';
  return `<div class="streaks">${items.map((item) =>
    `<span class="streak"><span class="streak-icon">${item.icon}</span>${esc(item.text)}</span>`).join('')}</div>`;
}

export function bindStreaks(container) {
  container.querySelectorAll('.streak').forEach((chip) => chip.addEventListener('click', () => {
    select();
    chip.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.08)' }, { transform: 'scale(1)' }], { duration: 300 });
  }));
}
