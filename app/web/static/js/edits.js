// Свои правки расписания: другое время урока, скрытый урок, свой урок.
//
// Дневник школы бывает неточным: «Россия — мои горизонты» стоит в 16:55, а
// идёт на перемене после третьего урока. Правка живёт в настройках
// (`lessonEdits`, общие для Telegram и сайта) и накладывается на дни из
// дневника при каждой загрузке — сами данные школы не меняются.
// Правка — на один день (`date`) или каждую неделю в этот день (`weekday`).
'use strict';

import { addDays, esc, fullDate, isoToday, refuseReadOnly, weekdayIndex } from './core.js';
import { getPref, setPref } from './prefs.js';
import { closeSheet, openSheet } from './sheet.js';
import { notify, select, tap } from './haptics.js';

const MAX_EDITS = 100;
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

// «Каждый понедельник», «каждую среду», «каждое воскресенье».
const EVERY = [
  'Каждый понедельник', 'Каждый вторник', 'Каждую среду', 'Каждый четверг',
  'Каждую пятницу', 'Каждую субботу', 'Каждое воскресенье',
];

export function lessonEdits() {
  const value = getPref('lessonEdits');
  return Array.isArray(value) ? value : [];
}

function saveEdits(edits) {
  // Разовые правки прошедших дней больше не нужны — не копим их.
  const old = addDays(isoToday(), -30);
  setPref('lessonEdits', edits.filter((edit) => !edit.date || edit.date >= old).slice(-MAX_EDITS));
  window.dispatchEvent(new CustomEvent('netschool:edits'));
}

function minutes(text) {
  const [hours, mins] = String(text || '').split(':').map(Number);
  return Number.isFinite(hours) && Number.isFinite(mins) ? hours * 60 + mins : null;
}

function newId() {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

/** Урок дня, к которому относится правка: тот же предмет и номер, а если
 *  номер в эту неделю другой — единственный урок с этим предметом. */
function findLesson(lessons, edit) {
  const same = lessons.filter((lesson) => lesson.subject === edit.subject);
  const exact = same.find((lesson) => String(lesson.number ?? '') === String(edit.number ?? ''));
  return exact || (same.length === 1 ? same[0] : null);
}

/** День из дневника с наложенными правками. Исходный объект не меняется. */
export function applyEdits(day, edits = lessonEdits()) {
  if (!day || !edits.length) return day;
  const weekday = weekdayIndex(day.date);
  // Сначала еженедельные, затем разовые: правка на день главнее.
  const relevant = [
    ...edits.filter((edit) => edit.weekday === weekday && !edit.date),
    ...edits.filter((edit) => edit.date === day.date),
  ];
  if (!relevant.length) return day;
  let lessons = (day.lessons || []).map((lesson) => ({ ...lesson }));
  const hidden = [];
  for (const edit of relevant) {
    if (edit.kind === 'add') {
      lessons.push({
        number: '', subject: edit.subject, start: edit.start, end: edit.end, time: `${edit.start}–${edit.end}`,
        room: edit.room || '', teacher: '', topic: '', absence: '', is_elective: false,
        marks: [], homework: [], addedBy: edit.id,
      });
      continue;
    }
    const lesson = findLesson(lessons, edit);
    if (!lesson) continue;
    if (edit.kind === 'hide') {
      lessons = lessons.filter((item) => item !== lesson);
      hidden.push({ subject: lesson.subject, number: lesson.number, edit });
    } else if (edit.kind === 'time') {
      lesson.schoolTime ??= lesson.time || [lesson.start, lesson.end].filter(Boolean).join('–');
      lesson.start = edit.start;
      lesson.end = edit.end;
      lesson.time = `${edit.start}–${edit.end}`;
      lesson.editedBy = edit;
    }
  }
  // По времени начала; урок без времени остаётся на своём месте в конце.
  lessons = lessons
    .map((lesson, index) => ({ lesson, index, start: minutes(lesson.start) ?? 24 * 60 }))
    .sort((a, b) => a.start - b.start || a.index - b.index)
    .map(({ lesson }) => lesson);
  // Свой урок без номера выбивался из списка — в такой день все уроки
  // нумеруются по порядку (lessonNo в core.js).
  if (lessons.some((lesson) => lesson.addedBy)) lessons.forEach((lesson, index) => { lesson.place = index + 1; });
  return { ...day, lessons, hidden };
}

/* ─────────────────────────────── Шторки ─────────────────────────────── */

/** «четверг, 15 октября» — после «только». */
function dayText(iso) {
  const text = fullDate(iso);
  return text.charAt(0).toLowerCase() + text.slice(1);
}

function repeatChips(iso, weekly) {
  return `<div class="chips edit-repeat">
      <button class="chip ${weekly ? 'active' : ''}" data-repeat="week">${EVERY[weekdayIndex(iso)]}</button>
      <button class="chip ${weekly ? '' : 'active'}" data-repeat="day">Только ${esc(dayText(iso))}</button>
    </div>`;
}

function timeFields(start, end) {
  return `<div class="edit-times">
      <label class="field-label">Начало<input class="field edit-start" type="time" value="${esc(start || '')}"></label>
      <label class="field-label">Конец<input class="field edit-end" type="time" value="${esc(end || '')}"></label>
    </div>`;
}

function bindRepeat(body) {
  const chips = body.querySelectorAll('[data-repeat]');
  chips.forEach((chip) => chip.addEventListener('click', () => {
    select();
    chips.forEach((other) => other.classList.toggle('active', other === chip));
  }));
  return () => body.querySelector('[data-repeat].active')?.dataset.repeat === 'week';
}

/** Время из полей или null (с отказом), если оно не годится. */
function readTimes(body) {
  const start = body.querySelector('.edit-start').value;
  const end = body.querySelector('.edit-end').value;
  if (!TIME_RE.test(start) || !TIME_RE.test(end) || minutes(end) <= minutes(start)) {
    notify('error');
    body.querySelector('.edit-error').hidden = false;
    return null;
  }
  return { start, end };
}

function where(iso, weekly) {
  return weekly ? { weekday: weekdayIndex(iso) } : { date: iso };
}

/** Убрать правки этого урока того же рода и повтора — новая их заменит. */
function without(edits, sample) {
  return edits.filter((edit) => !(edit.kind === sample.kind && edit.subject === sample.subject
    && String(edit.number ?? '') === String(sample.number ?? '')
    && (sample.date ? edit.date === sample.date : !edit.date && edit.weekday === sample.weekday)));
}

/** Шторка урока: своё время, скрыть, вернуть как в дневнике. Свой урок —
 *  поменять время или удалить. onDone — после сохранения. */
export function openLessonEditor(lesson, iso, onDone) {
  if (refuseReadOnly()) return;
  tap();
  if (lesson.addedBy) {
    openAddedEditor(lesson, iso, onDone);
    return;
  }
  const current = lesson.editedBy;
  const number = lesson.number === '' || lesson.number == null ? null : Number(lesson.number);
  const body = openSheet('Изменить урок', `
    <div class="note-form lesson-edit">
      <div class="muted small">${esc(lesson.subject)}${lesson.schoolTime ? ` · в дневнике ${esc(lesson.schoolTime)}` : ''}</div>
      ${timeFields(lesson.start, lesson.end)}
      <div class="muted small edit-error" hidden>Конец урока должен быть позже начала</div>
      ${repeatChips(iso, current ? !current.date : true)}
      <button class="btn edit-save">Сохранить время</button>
      ${current ? '<button class="btn outline edit-reset">Вернуть время из дневника</button>' : ''}
      <button class="btn outline edit-hide">Скрыть урок</button>
      <div class="muted small">Правка видна только вам, в дневнике школы ничего не меняется.</div>
    </div>`);
  const weekly = bindRepeat(body);
  const done = () => {
    notify('success');
    closeSheet();
    onDone?.();
  };
  body.querySelector('.edit-save').addEventListener('click', () => {
    const times = readTimes(body);
    if (!times) return;
    const edit = { id: newId(), kind: 'time', subject: lesson.subject, number, ...where(iso, weekly()), ...times };
    let edits = without(lessonEdits(), edit);
    // Сменили повтор — прежняя правка другого повтора больше не нужна.
    if (current) edits = edits.filter((item) => item.id !== current.id);
    saveEdits([...edits, edit]);
    done();
  });
  body.querySelector('.edit-reset')?.addEventListener('click', () => {
    saveEdits(lessonEdits().filter((item) => item.id !== current.id));
    done();
  });
  body.querySelector('.edit-hide').addEventListener('click', () => {
    const edit = { id: newId(), kind: 'hide', subject: lesson.subject, number, ...where(iso, weekly()) };
    saveEdits([...without(lessonEdits(), edit), edit]);
    done();
  });
}

function openAddedEditor(lesson, iso, onDone) {
  const current = lessonEdits().find((edit) => edit.id === lesson.addedBy);
  if (!current) return;
  const body = openSheet('Свой урок', `
    <div class="note-form lesson-edit">
      <div class="muted small">${esc(lesson.subject)} · ${esc(current.date ? `только ${dayText(current.date)}` : EVERY[current.weekday].toLowerCase())}</div>
      ${timeFields(lesson.start, lesson.end)}
      <div class="muted small edit-error" hidden>Конец урока должен быть позже начала</div>
      <button class="btn edit-save">Сохранить</button>
      <button class="btn outline edit-remove">Удалить урок</button>
    </div>`);
  const done = () => {
    notify('success');
    closeSheet();
    onDone?.();
  };
  body.querySelector('.edit-save').addEventListener('click', () => {
    const times = readTimes(body);
    if (!times) return;
    saveEdits(lessonEdits().map((edit) => (edit.id === current.id ? { ...edit, ...times } : edit)));
    done();
  });
  body.querySelector('.edit-remove').addEventListener('click', () => {
    saveEdits(lessonEdits().filter((edit) => edit.id !== current.id));
    done();
  });
}

/** Шторка «Добавить урок» на день iso. subjects — для подсказок. */
export function openAddLesson(iso, subjects = []) {
  if (refuseReadOnly()) return;
  tap();
  const body = openSheet('Добавить урок', `
    <div class="note-form lesson-edit">
      <label class="field-label">Предмет
        <input class="field edit-subject" maxlength="120" list="editSubjects" placeholder="Например: Россия — мои горизонты"></label>
      <datalist id="editSubjects">${subjects.map((name) => `<option value="${esc(name)}">`).join('')}</datalist>
      ${timeFields('', '')}
      <div class="muted small edit-error" hidden>Укажите начало и конец, конец — позже начала</div>
      <label class="field-label">Кабинет (необязательно)
        <input class="field edit-room" maxlength="40"></label>
      ${repeatChips(iso, true)}
      <button class="btn edit-save">Добавить</button>
    </div>`);
  const weekly = bindRepeat(body);
  const subject = body.querySelector('.edit-subject');
  setTimeout(() => subject.focus(), 50);
  body.querySelector('.edit-save').addEventListener('click', () => {
    const name = subject.value.trim();
    if (!name) { subject.focus(); notify('error'); return; }
    const times = readTimes(body);
    if (!times) return;
    const room = body.querySelector('.edit-room').value.trim();
    const edit = { id: newId(), kind: 'add', subject: name, ...where(iso, weekly()), ...times, ...(room ? { room } : {}) };
    saveEdits([...lessonEdits(), edit]);
    notify('success');
    closeSheet();
  });
}

/** Скрытые уроки дня — строкой под уроками, нажатие возвращает урок. */
export function hiddenHtml(day) {
  if (!day?.hidden?.length) return '';
  return `<div class="hidden-lessons">${day.hidden.map((item, index) =>
    `<button class="link" data-unhide="${index}">Скрыт: ${esc(item.subject)} · вернуть</button>`).join('')}</div>`;
}

export function bindHidden(container, day) {
  container.querySelectorAll('[data-unhide]').forEach((button) => button.addEventListener('click', () => {
    tap();
    const { edit } = day.hidden[Number(button.dataset.unhide)];
    saveEdits(lessonEdits().filter((item) => item.id !== edit.id));
  }));
}
