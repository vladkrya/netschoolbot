// Детальные экраны: урок, домашнее задание, оценка.
// Открываются из «Дня», «Д/З» и «Итогов» — поэтому живут отдельно.
'use strict';

import {
  absenceText, attachmentsHtml, esc, escRich, forDate, fullDate, isAbsence,
  markClass, observedTime,
} from './core.js';
import { pushScreen } from './screens.js';
import { ICONS } from './icons.js';

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

export function markBox(mark, extra = '') {
  return `<span class="mark-box ${markClass(mark)} ${extra}">${esc(mark)}</span>`;
}

function timeRange(lesson) {
  return lesson.time || [lesson.start, lesson.end].filter(Boolean).join(' – ');
}

export function openLesson(lesson, iso) {
  const body = pushScreen(`Урок ${lesson.number ?? ''}`.trim(), fullDate(iso));
  const status = lessonStatus(lesson, iso);

  // Горизонтальная лента, как в приложении: кабинет, учитель, тема —
  // только то, что школа заполнила.
  const info = [
    ['Кабинет', lesson.room],
    ['Учитель', lesson.teacher],
    ['Тема урока', lesson.topic],
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
    ...(lesson.homework || []).map((item, index) => `
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
        <span class="link">${esc(lesson.subject)}</span></div>
      ${lesson.is_elective ? '<div class="lc-badges"><span class="elective-badge">Факультатив</span></div>' : ''}
      <div class="lc-bottom"><span>${esc(timeRange(lesson))}</span><span>${esc(status)}</span></div>
    </div>
    ${info.length ? `<div class="info-strip">${info
      .map(([label, value]) => `<div class="info-card"><div class="info-label">${label}</div><div class="info-value">${esc(value)}</div></div>`)
      .join('')}</div>` : ''}
    ${blocks.join('') || '<div class="empty">Заданий на этот урок нет</div>'}`;

  const marks = realMarks(lesson.marks);
  body.querySelectorAll('[data-mark]').forEach((el) =>
    el.addEventListener('click', () => openMark(marks[Number(el.dataset.mark)], lesson.subject, lesson.topic))
  );
  body.querySelectorAll('[data-hw]').forEach((el) =>
    el.addEventListener('click', () => openHomework(lesson.homework[Number(el.dataset.hw)], lesson.topic))
  );
}

export function openHomework(item, topic = '') {
  topic = topic || item.topic || '';
  const body = pushScreen('Домашнее задание', forDate(item.date));
  const observed = observedTime(item.observed_at);
  const published = observedTime(item.published_at);
  body.innerHTML = `
    <div class="detail">
      <h2>${esc(item.subject)}</h2>
      ${topic ? `<div class="detail-label">Тема урока</div><div class="detail-text">${esc(topic)}</div>` : ''}
      <div class="detail-row"><span class="detail-label">Задание</span>${item.type ? `<span class="muted small">${esc(item.type)}</span>` : ''}</div>
      <div class="detail-text">${escRich(item.text) || '<span class="muted">—Не указано—</span>'}</div>
      ${item.weight ? `<div class="detail-row"><span class="detail-label">Вес задания</span><span class="muted small">${esc(String(item.weight))}</span></div>` : ''}
      ${published ? `<div class="detail-label">Опубликовано учителем</div><div class="detail-text">${esc(published)}</div>` : ''}
      ${observed ? `<div class="detail-label">Впервые замечено ботом</div><div class="detail-text">${esc(observed)}</div>` : ''}
      ${attachmentsHtml(item.attachments)}
    </div>`;
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
  body.innerHTML = `
    <div class="detail">
      <h2>${esc(subject || mark.subject)}</h2>
      <div class="detail-label">Тема урока</div>
      <div class="detail-text">${esc(topic) || '<span class="muted">—Не указана—</span>'}</div>
      ${work && work !== topic ? `<div class="detail-label">Работа</div><div class="detail-text">${esc(work)}</div>` : ''}
      <div class="muted small">Вес задания ${esc(mark.weight || 1)}</div>
      <div class="grade-row ${isAbsence(mark.mark) ? 'absence' : ''}">
        <span>${isAbsence(mark.mark) ? esc(absenceText(mark.mark)) : 'Оценка'}</span>
        ${markBox(mark.mark)}
      </div>
    </div>`;
}
