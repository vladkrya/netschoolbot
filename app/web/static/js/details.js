// Детальные экраны: урок, домашнее задание, оценка.
// Открываются из «Дня», «Д/З» и «Итогов» — поэтому живут отдельно.
'use strict';

import {
  absenceText, attachmentsHtml, esc, escRich, forDate, fullDate, isAbsence,
  markClass,
} from './core.js';
import { pushScreen } from './screens.js';
import { ICONS } from './icons.js';
import { isNewDesign } from './appearance.js';

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

function compactTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date).replace(',', '');
}

export function openLesson(lesson, iso) {
  const body = pushScreen(`Урок ${lesson.number ?? ''}`.trim(), fullDate(iso));
  const status = lessonStatus(lesson, iso);

  // Горизонтальная лента, как в приложении: кабинет, учитель, тема —
  // только то, что школа заполнила.
  const info = [
    ['Кабинет', lesson.room],
    ['Учитель', lesson.teacher],
  ].filter(([, value]) => value);

  const blocks = [
    ...realMarks(lesson.marks).map((mark, index) => `
      <button class="task-block task-mark" data-mark="${index}">
        <div class="task-head">
          <span class="task-icon">${ICONS.chat}</span>
          <span class="task-kind">${esc(mark.type || 'Оценка')}</span>
          ${markBox(mark.mark)}
        </div>
        <span class="task-go">Перейти ›</span>
      </button>`),
    // Новый дизайн: задание целиком прямо здесь, без отдельного экрана.
    ...(lesson.homework || []).map((item, index) => isNewDesign() ? homeworkBlock(item) : `
      <button class="task-block task-hw" data-hw="${index}">
        <div class="task-head">
          <span class="task-icon">${ICONS.homework}</span>
          <span class="task-kind">Домашнее задание</span>
          ${item.attachments?.length ? `<span class="clip">${ICONS.clip}</span>` : ''}
        </div>
        <span class="task-go">Перейти ›</span>
      </button>`),
  ];

  body.innerHTML = `
    <div class="card lesson-head">
      <div class="lesson-title"><span class="lc-num">${esc(lesson.number ?? '')}</span>
        <button class="link subject-link" data-subject-results>${esc(lesson.subject)}</button></div>
      ${lesson.is_elective ? '<div class="lc-badges"><span class="elective-badge">Факультатив</span></div>' : ''}
      <div class="lc-bottom"><span>${esc(timeRange(lesson))}</span><span>${esc(status)}</span></div>
    </div>
    ${info.length ? `<div class="info-strip">${info
      .map(([label, value]) => `<div class="info-card"><div class="info-label">${label}</div><div class="info-value">${esc(value)}</div></div>`)
      .join('')}</div>` : ''}
    ${lesson.topic ? `<div class="info-card info-topic"><div class="info-label">Тема урока</div>
      <div class="info-value">${esc(lesson.topic)}</div></div>` : ''}
    ${blocks.join('') || '<div class="empty">Заданий на этот урок нет</div>'}`;

  bindSubject(body, lesson.subject, iso);

  const marks = realMarks(lesson.marks);
  body.querySelectorAll('[data-mark]').forEach((el) =>
    el.addEventListener('click', () => openMark(marks[Number(el.dataset.mark)], lesson.subject, lesson.topic))
  );
  body.querySelectorAll('[data-hw]').forEach((el) =>
    el.addEventListener('click', () => openHomework(lesson.homework[Number(el.dataset.hw)], lesson.topic))
  );
}

/** Домашнее задание на экране урока: текст, файлы и когда опубликовано. */
function homeworkBlock(item) {
  const timestamps = homeworkTimes(item);
  return `<div class="task-block task-hw hw-inline">
      <div class="task-head">
        <span class="task-icon">${ICONS.homework}</span>
        <span class="task-kind">Домашнее задание</span>
        ${Number(item.weight) > 1 ? `<span class="hw-weight">вес ${esc(item.weight)}</span>` : ''}
      </div>
      <div class="detail-text">${escRich(item.text) || '<span class="muted">—Не указано—</span>'}</div>
      ${attachmentsHtml(item.attachments)}
      ${timestamps.length ? `<div class="hw-times">${timestamps.map(esc).join(' · ')}</div>` : ''}
    </div>`;
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
  const body = pushScreen('Домашнее задание', forDate(item.date));
  const timestamps = homeworkTimes(item);
  body.innerHTML = `
    <div class="detail">
      <h2><button class="subject-link" data-subject-results>${esc(item.subject)}</button></h2>
      <div class="detail-weight">Вес задания: ${esc(item.weight || 1)}</div>
      ${topic ? `<div class="detail-label">Тема урока</div><div class="detail-text">${esc(topic)}</div>` : ''}
      <div class="assignment-heading"><span class="detail-label">Задание</span>
        ${timestamps.length ? `<span class="assignment-times">${timestamps.map(esc).join('<br>')}</span>` : ''}</div>
      <div class="detail-text">${escRich(item.text) || '<span class="muted">—Не указано—</span>'}</div>
      ${attachmentsHtml(item.attachments)}
    </div>`;
  bindSubject(body, item.subject, item.date);
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
