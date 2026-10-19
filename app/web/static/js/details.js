// Детальные экраны: урок, домашнее задание, оценка.
// Открываются из «Дня», «Д/З» и «Итогов» — поэтому живут отдельно.
'use strict';

import {
  absenceText, api, attachmentsHtml, banner, esc, escRich, forDate, fullDate, isAbsence,
  isoToday, lessonNo, markClass, refuseReadOnly, state,
} from './core.js';
import { notify, tap } from './haptics.js';
import { popScreen, pushScreen } from './screens.js';
import { openLessonEditor } from './edits.js';
import { ICONS } from './icons.js';
import { isNewDesign } from './appearance.js';
import { answerSlot, bindDistance, distanceButton, loadAnswers } from './answer.js';
import { pendingOps, send } from './outbox.js';

/* ───────────────────── Отметка «выполнено» ───────────────────── */
// Общая для «Д/З», экрана урока и экрана задания; хранится на сервере и
// видна в /dz бота. Без сети отметка ждёт в очереди (outbox.js).

export async function loadDone() {
  try {
    const { data } = await api('/api/homework/done');
    state.hwDone = new Set(data.keys);
  } catch {
    state.hwDone = state.hwDone || new Set();
  }
  // Сохранённая копия с сервера ещё не знает отметок, сделанных без сети.
  for (const { body } of pendingOps('hw:')) {
    if (body.done) state.hwDone.add(body.key);
    else state.hwDone.delete(body.key);
  }
}

/** Отмечать нечего: учитель написал «Не предусмотрено» («Нет», «нет
 *  задания») или это работа на уроке, который уже прошёл. */
export function isAutoDone(item) {
  return Boolean(item.auto_done) || Boolean(item.classwork && item.date && item.date < isoToday());
}

/** Выполнено: отмечено человеком или отмечать нечего (isAutoDone). */
export function isDoneItem(item, done = state.hwDone || new Set()) {
  return isAutoDone(item) || done.has(item.key);
}

/** Заголовок задания: у работы на уроке — её вид («Диктант»). */
export function homeworkKind(item) {
  return item.classwork ? (item.type || 'Работа на уроке') : 'Домашнее задание';
}

/** Отметить и сохранить. apply(value) рисует состояние — сразу, не дожидаясь
 *  сервера, и ещё раз с прежним значением, если сохранить не вышло.
 *  source — кто отметил: этот экран уже перерисован и событие пропустит. */
export async function saveDone(item, next, apply, source = '') {
  if (refuseReadOnly()) return;
  const done = state.hwDone || (state.hwDone = new Set());
  const set = (value) => {
    if (value) done.add(item.key);
    else done.delete(item.key);
    apply(value);
    // Главная перекрашивает Д/З (подсветка «сделано / не сделано»).
    window.dispatchEvent(new CustomEvent('netschool:hwdone', { detail: { source } }));
  };
  set(next);
  next ? notify('success') : tap('soft');
  try {
    await send(`hw:${item.key}`, '/api/homework/done', { key: item.key, done: next });
  } catch (error) {
    set(!next);
    notify('error');
    banner(error.message, true);
  }
}

/** Кнопка «выполнено» под заданием на экранах урока и задания. */
function doneToggle(item, index) {
  if (item.auto_done) return '<div class="hw-done-toggle on auto">✓ Не предусмотрено — отмечено само</div>';
  if (isAutoDone(item)) return '<div class="hw-done-toggle on auto">✓ Работа на уроке — отмечено само</div>';
  if (!item.key) return '';
  return `<button class="hw-done-toggle" data-done-toggle="${index}" aria-pressed="false">Отметить выполненным</button>`;
}

function paintToggle(button, on) {
  button.classList.toggle('on', on);
  button.setAttribute('aria-pressed', String(on));
  button.textContent = on ? '✓ Выполнено' : 'Отметить выполненным';
}

/** Оживить кнопки «выполнено»; отметки подгружаются, если их ещё нет. */
function bindDoneToggles(body, items) {
  const buttons = [...body.querySelectorAll('[data-done-toggle]')];
  if (!buttons.length) return;
  const paintAll = () => buttons.forEach((button) =>
    paintToggle(button, isDoneItem(items[Number(button.dataset.doneToggle)])));
  if (state.hwDone) paintAll();
  else loadDone().then(paintAll);
  // Отметили в списке или на другом устройстве — кнопки здесь совпадут.
  const repaint = () => {
    if (body.isConnected) paintAll();
    else window.removeEventListener('netschool:hwdone', repaint);
  };
  window.addEventListener('netschool:hwdone', repaint);
  buttons.forEach((button) => button.addEventListener('click', (event) => {
    event.stopPropagation();
    // Заливка расходится от точки нажатия (app.css, .hw-done-toggle.ripple).
    const rect = button.getBoundingClientRect();
    button.style.setProperty('--x', `${event.clientX ? event.clientX - rect.left : rect.width / 2}px`);
    button.style.setProperty('--y', `${event.clientY ? event.clientY - rect.top : rect.height / 2}px`);
    button.classList.add('ripple');
    const item = items[Number(button.dataset.doneToggle)];
    saveDone(item, !isDoneItem(item), (value) => paintToggle(button, value));
  }));
}

/** Статус урока справа от времени, как в официальном приложении. */
export function lessonStatus(lesson, iso, now = new Date()) {
  if (lesson.absence) return absenceText(lesson.absence) || lesson.absence;
  const absence = (lesson.marks || []).map((m) => absenceText(m.mark)).find(Boolean);
  if (absence) return absence;

  const [endH, endM] = String(lesson.end || '').split(':').map(Number);
  const [startH, startM] = String(lesson.start || '').split(':').map(Number);
  const day = new Date(`${iso}T00:00:00`);
  if (Number.isFinite(endH)) {
    const end = new Date(day);
    end.setHours(endH, endM || 0);
    if (now >= end) return 'Урок окончен';
    if (Number.isFinite(startH)) {
      const start = new Date(day);
      start.setHours(startH, startM || 0);
      if (now >= start) return 'Идёт урок';
    }
  } else if (day < new Date(now.getFullYear(), now.getMonth(), now.getDate())) {
    return 'Урок окончен';
  }
  return '';
}

/** Оценки урока без отметок посещаемости: пропуски идут статусом. */
export function realMarks(marks) {
  return (marks || []).filter((m) => !isAbsence(m.mark));
}

/** Оценка в рамке. weight — коэффициент маленькой цифрой в углу
 *  (виден в новом дизайне, см. .mw в new.css). */
export function markBox(mark, extra = '', weight = null) {
  const coef = weight == null ? '' : `<sup class="mw">${esc(String(weight).replace('.', ','))}</sup>`;
  return `<span class="mark-box ${markClass(mark)} ${extra}">${esc(mark)}${coef}</span>`;
}

function timeRange(lesson) {
  return lesson.time || [lesson.start, lesson.end].filter(Boolean).join(' – ');
}

function bindSubject(body, subject, iso) {
  body.querySelector('[data-subject-results]')?.addEventListener('click', async () => {
    const { openSubjectForDate } = await import('./totals.js');
    openSubjectForDate(subject, iso);
  });
}

/** Статус на открытом экране урока («Идёт урок» → «Урок окончен») меняется
 *  на границе минуты, пока экран открыт. */
function keepStatusFresh(body, lesson, iso) {
  const schedule = () => setTimeout(() => {
    // Экран закрыли — отсчёт больше не нужен.
    if (!body.isConnected) return;
    const el = body.querySelector('[data-lesson-status]');
    if (el) el.textContent = lessonStatus(lesson, iso);
    schedule();
  }, 60000 - Date.now() % 60000 + 50);
  schedule();
}

function compactTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date).replace(',', '');
}

export function openLesson(lesson, iso) {
  const body = pushScreen(`Урок ${lessonNo(lesson)}`.trim(), fullDate(iso));
  const status = lessonStatus(lesson, iso);

  // Горизонтальная лента, как в приложении: кабинет, учитель, тема —
  // только то, что школа заполнила.
  const info = [
    ['Кабинет', lesson.room],
    ['Учитель', lesson.teacher],
  ].filter(([, value]) => value);

  const blocks = [
    // Новый дизайн: оценка целиком здесь — с коэффициентом и временем внесения.
    ...realMarks(lesson.marks).map((mark, index) => isNewDesign() ? markBlock(mark) : `
      <button class="task-block task-mark" data-mark="${index}">
        <div class="task-head">
          <span class="task-icon">${ICONS.chat}</span>
          <span class="task-kind">${esc(mark.type || 'Оценка')}</span>
          ${markBox(mark.mark)}
        </div>
        <span class="task-go">Перейти ›</span>
      </button>`),
    // Новый дизайн: задание целиком прямо здесь, без отдельного экрана.
    ...(lesson.homework || []).map((item, index) => isNewDesign() ? homeworkBlock(item, index) : `
      <button class="task-block task-hw" data-hw="${index}">
        <div class="task-head">
          <span class="task-icon">${ICONS.homework}</span>
          <span class="task-kind">${esc(homeworkKind(item))}</span>
          ${item.attachments?.length ? `<span class="clip">${ICONS.clip}</span>` : ''}
        </div>
        <span class="task-go">Перейти ›</span>
      </button>${doneToggle(item, index)}`),
  ];

  body.innerHTML = `
    <div class="card lesson-head">
      <div class="lesson-title"><span class="lc-num">${esc(lessonNo(lesson))}</span>
        <button class="link subject-link" data-subject-results>${esc(lesson.subject)}</button></div>
      ${lesson.is_elective ? '<div class="lc-badges"><span class="elective-badge">Факультатив</span></div>' : ''}
      <div class="lc-bottom"><span>${esc(timeRange(lesson))}</span><span data-lesson-status>${esc(status)}</span></div>
      ${lesson.schoolTime ? `<div class="muted small">В дневнике: ${esc(lesson.schoolTime)}</div>` : ''}
      ${distanceButton(lesson)}
      <button class="link lesson-edit-link" data-lesson-edit>${lesson.addedBy ? 'Изменить свой урок' : 'Изменить время или скрыть'}</button>
    </div>
    ${info.length ? `<div class="info-strip">${info
      .map(([label, value]) => `<div class="info-card"><div class="info-label">${label}</div><div class="info-value">${esc(value)}</div></div>`)
      .join('')}</div>` : ''}
    ${lesson.topic ? `<div class="info-card info-topic"><div class="info-label">Тема урока</div>
      <div class="info-value">${esc(lesson.topic)}</div></div>` : ''}
    ${blocks.join('') || '<div class="empty">Заданий на этот урок нет</div>'}`;

  bindSubject(body, lesson.subject, iso);
  bindDistance(body);
  keepStatusFresh(body, lesson, iso);
  // После правки карточка урока устарела — возвращаемся к дню.
  body.querySelector('[data-lesson-edit]').addEventListener('click', () => openLessonEditor(lesson, iso, popScreen));
  if (isNewDesign()) {
    loadComments(body, lesson.homework || []);
    loadAnswers(body, lesson.homework || []);
  }
  bindDoneToggles(body, lesson.homework || []);

  const marks = realMarks(lesson.marks);
  body.querySelectorAll('[data-mark]').forEach((el) =>
    el.addEventListener('click', () => openMark(marks[Number(el.dataset.mark)], lesson.subject, lesson.topic))
  );
  body.querySelectorAll('[data-hw]').forEach((el) =>
    el.addEventListener('click', () => openHomework(lesson.homework[Number(el.dataset.hw)], lesson.topic))
  );
}

/** Оценка на экране урока: вид работы, оценка с коэффициентом, комментарий
 *  учителя и когда внесена. */
function markBlock(mark) {
  const work = realTopic(mark.topic, mark);
  const entered = compactTime(mark.marked_at || mark.published_at);
  return `<div class="task-block task-mark mark-inline">
      <div class="task-head">
        <span class="task-icon">${ICONS.chat}</span>
        <span class="task-kind">${esc(mark.type || 'Оценка')}</span>
        ${markBox(mark.mark, 'big', mark.weight || 1)}
      </div>
      ${work ? `<div class="detail-text">${esc(work)}</div>` : ''}
      ${mark.comment ? `<div class="mark-comment"><span class="detail-label">Комментарий учителя</span>${esc(mark.comment)}</div>` : ''}
      ${entered ? `<div class="hw-times">Внесена ${esc(entered)}</div>` : ''}
    </div>`;
}

/** Домашнее задание на экране урока: текст, файлы и когда опубликовано. */
function homeworkBlock(item, index) {
  const timestamps = homeworkTimes(item);
  return `<div class="task-block task-hw hw-inline">
      <div class="task-head">
        <span class="task-icon">${ICONS.homework}</span>
        <span class="task-kind">${esc(homeworkKind(item))}</span>
        ${Number(item.weight) > 1 ? `<span class="hw-weight">вес ${esc(item.weight)}</span>` : ''}
      </div>
      <div class="detail-text">${escRich(item.text) || '<span class="muted">—Не указано—</span>'}</div>
      ${commentSlot(item)}
      ${attachmentsHtml(item.attachments)}
      ${timestamps.length ? `<div class="hw-times">${timestamps.map(esc).join(' · ')}</div>` : ''}
      ${answerSlot(item)}
      ${doneToggle(item, index)}
    </div>`;
}

// Комментарий учителя к заданию лежит только в карточке задания —
// подгружаем при открытии и запоминаем до перезапуска приложения.
const comments = new Map();

function commentSlot(item) {
  return item.id ? `<div class="hw-comment" data-comment-for="${esc(item.id)}" hidden></div>` : '';
}

function commentHtml(text) {
  return `<div class="detail-label">Комментарий к заданию</div><div class="detail-text">${escRich(text)}</div>`;
}

/** Заполнить места под комментарии на только что открытом экране. */
function loadComments(body, items) {
  for (const item of items) {
    const slot = item.id && body.querySelector(`[data-comment-for="${item.id}"]`);
    if (!slot) continue;
    const show = (text) => {
      // Иногда школа дублирует в описание сам текст задания — его не повторяем.
      if (!text || text.trim() === String(item.text || '').trim()) return;
      slot.innerHTML = commentHtml(text);
      slot.hidden = false;
    };
    // Мобильное API отдаёт комментарий прямо в дневнике ("" — его нет);
    // карточку спрашиваем, только когда это неизвестно (веб-API).
    if (typeof item.comment === 'string') {
      show(item.comment);
      continue;
    }
    if (comments.has(item.id)) {
      show(comments.get(item.id));
      continue;
    }
    api(`/api/homework/${encodeURIComponent(item.id)}/comment`)
      .then(({ data }) => {
        comments.set(item.id, data.comment || '');
        show(data.comment);
      })
      .catch(() => { /* без комментария экран всё равно полезен */ });
  }
}

/** Когда задание опубликовали и когда его заметила наша система. Второе —
 *  только если разница больше 10 минут (или времени публикации нет):
 *  иначе это одно и то же событие и строка лишняя. */
export function homeworkTimes(item, format = compactTime) {
  const published = format(item.published_at);
  const observed = format(item.observed_at);
  const close = published && observed
    && Math.abs(new Date(item.observed_at) - new Date(item.published_at)) <= 10 * 60 * 1000;
  return [
    published ? `Опубликовано ${published}` : '',
    observed && !close ? `Замечено системой ${observed}` : '',
  ].filter(Boolean);
}

export function openHomework(item, topic = '') {
  topic = topic || item.topic || '';
  const body = pushScreen(homeworkKind(item), forDate(item.date));
  const timestamps = homeworkTimes(item);
  body.innerHTML = `
    <div class="detail">
      <h2><button class="subject-link" data-subject-results>${esc(item.subject)}</button></h2>
      <div class="detail-weight">Вес задания: ${esc(item.weight || 1)}</div>
      ${topic ? `<div class="detail-label">Тема урока</div><div class="detail-text">${esc(topic)}</div>` : ''}
      <div class="assignment-heading"><span class="detail-label">Задание</span>
        ${timestamps.length ? `<span class="assignment-times">${timestamps.map(esc).join('<br>')}</span>` : ''}</div>
      <div class="detail-text">${escRich(item.text) || '<span class="muted">—Не указано—</span>'}</div>
      ${commentSlot(item)}
      ${attachmentsHtml(item.attachments)}
      ${answerSlot(item)}
      ${doneToggle(item, 0)}
    </div>`;
  bindSubject(body, item.subject, item.date);
  loadComments(body, [item]);
  loadAnswers(body, [item]);
  bindDoneToggles(body, [item]);
}

/** Текст, который на самом деле тема, а не повтор оценки или типа работы. */
function realTopic(text, mark) {
  const value = String(text ?? '').trim();
  if (!value) return '';
  const same = [mark.mark, mark.type, mark.title, 'Оценка'].map((item) => String(item ?? '').trim().toLowerCase());
  return same.includes(value.toLowerCase()) ? '' : value;
}

export function openMark(mark, subject, lessonTopic = '') {
  const body = pushScreen(mark.type || 'Оценка', forDate(mark.date));
  // Тема — это тема урока; название работы показываем отдельно, если оно
  // есть и не совпадает с ней. Раньше в «Тему» шло что попало, вплоть до
  // самой оценки, когда у работы не было названия.
  const topic = realTopic(lessonTopic || mark.lesson_topic, mark);
  const work = realTopic(mark.topic, mark);
  const absence = mark.type === 'Посещаемость' || isAbsence(mark.mark);
  body.innerHTML = `
    <div class="detail">
      <h2><button class="subject-link" data-subject-results>${esc(subject || mark.subject)}</button></h2>
      ${!absence ? `<div class="detail-weight">Вес задания: ${esc(mark.weight || 1)}</div>` : ''}
      <div class="detail-label">Тема урока</div>
      <div class="detail-text">${esc(topic) || '<span class="muted">—Не указана—</span>'}</div>
      ${!absence ? `<div class="assignment-heading"><span class="detail-label">Задание</span>
        ${mark.published_at ? `<span class="assignment-times">Опубликовано ${esc(compactTime(mark.published_at))}</span>` : ''}</div>
        <div class="detail-text">${esc(work) || '<span class="muted">—Не указано—</span>'}</div>` : ''}
      <div class="grade-row ${absence ? 'absence' : ''}">
        <span>${absence ? esc(absenceText(mark.mark) || 'Посещаемость') : 'Оценка'}</span>
        ${mark.marked_at ? `<span class="grade-entered">Внесена ${esc(compactTime(mark.marked_at))}</span>` : ''}
        ${markBox(mark.mark)}
      </div>
      ${mark.comment ? `<div class="detail-label">Комментарий учителя</div><div class="detail-text">${esc(mark.comment)}</div>` : ''}
    </div>`;
  bindSubject(body, subject || mark.subject, mark.date);
}
