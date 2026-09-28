// «Итоги»: таблица «предмет × четверть» и экран итогов по предмету.
'use strict';

import {
  $, api, errorCard, esc, formatAverage, isAbsence, isoToday, dayMonth,
  shortDate, skeleton, state, weightedAverage,
} from './core.js';
import { markBox, openMark } from './details.js';
import { pushScreen } from './screens.js';

const SHORT = { q1: '1/4', q2: '2/4', q3: '3/4', q4: '4/4' };

export async function loadTotals(force = false) {
  const container = $('totalsContent');
  if (!state.grades || force) {
    container.innerHTML = skeleton(8);
    try {
      const { data } = await api(`/api/marks${force ? '?refresh=1' : ''}`);
      state.grades = data;
    } catch (error) {
      container.innerHTML = errorCard(error, 'totalsRetry');
      $('totalsRetry')?.addEventListener('click', () => loadTotals(true));
      return;
    }
  }
  render();
}

function quarterMarks(subject, key) {
  return subject.marks.filter((mark) => mark.quarter === key);
}

function render() {
  const data = state.grades;
  const quarters = data.quarters || Object.keys(SHORT).map((key) => ({ key }));
  const head = `<div class="tt-row tt-head">
      <div class="tt-subject">Четверти <span class="year-pill">${esc(data.year || '')}</span></div>
      ${quarters.map((q) => `<div class="tt-cell ${q.key === data.current_quarter ? 'now' : ''}">${SHORT[q.key]}</div>`).join('')}
    </div>`;

  const rows = data.subjects.map((subject, index) => `
      <div class="tt-row">
        <div class="tt-subject">${esc(subject.subject)}</div>
        ${quarters
          .map((q) => {
            const avg = weightedAverage(quarterMarks(subject, q.key));
            return `<button class="tt-cell" data-subject="${index}" data-q="${q.key}">${formatAverage(avg)}</button>`;
          })
          .join('')}
      </div>`);

  const container = $('totalsContent');
  container.innerHTML = `<div class="totals-table">${head}${rows.join('')}</div>`;
  container.querySelectorAll('[data-subject]').forEach((cell) =>
    cell.addEventListener('click', () =>
      openSubject(data.subjects[Number(cell.dataset.subject)], cell.dataset.q)
    )
  );
}

/** Доли оценок в среднем балле: полоса из отрезков по весу каждой оценки. */
function distribution(marks) {
  const weights = { 5: 0, 4: 0, 3: 0, 2: 0 };
  let total = 0;
  for (const mark of marks) {
    const value = Number(mark.mark);
    if (!weights.hasOwnProperty(value)) continue;
    const weight = Number(mark.weight) > 0 ? Number(mark.weight) : 1;
    weights[value] += weight;
    total += weight;
  }
  if (!total) return '<div class="dist empty-dist">Оценок пока нет</div>';
  return `<div class="dist">${[5, 4, 3, 2]
    .filter((value) => weights[value])
    .map((value) => `<div class="dist-part d${value}" style="flex:${weights[value]}">${value}</div>`)
    .join('')}</div>`;
}

function openSubject(subject, key) {
  const quarter = (state.grades.quarters || []).find((q) => q.key === key);
  const body = pushScreen('Итоги по предмету', `на ${dayMonth(isoToday())}`);
  const marks = quarterMarks(subject, key);
  const lessons = subject.lessons?.[key] || { total: 0, done: 0 };
  const left = Math.max(0, lessons.total - lessons.done);
  const avg = weightedAverage(marks);

  const segments = lessons.total
    ? `<div class="progress">${Array.from({ length: lessons.total }, (_, i) =>
        `<span class="seg ${i < lessons.done ? 'done' : ''}" style="--i:${i}"></span>`).join('')}</div>
       <div class="progress-caption"><span>${esc(quarter?.label || SHORT[key])} ${lessons.done}/${lessons.total}</span>
       <span>Осталось ${left} ${left === 1 ? 'урок' : [2, 3, 4].includes(left % 10) && ![12, 13, 14].includes(left % 100) ? 'урока' : 'уроков'}</span></div>`
    : `<div class="muted small">${esc(quarter?.label || '')}</div>`;

  const tiles = marks
    .map(
      (mark, index) => `<button class="tile ${isAbsence(mark.mark) ? 'absence' : ''}" data-mark="${index}">
          ${markBox(mark.mark, 'big')}
          <span class="tile-weight">${esc(mark.weight || 1)}</span>
          <span class="tile-date">${esc(shortDate(mark.date))}</span>
        </button>`
    )
    .join('');

  body.innerHTML = `
    <div class="card">
      <div class="subject-title">${esc(subject.subject)}</div>
      ${segments}
      ${subject.teacher ? `<div class="muted small" style="margin-top:8px">Учитель: <span class="link">${esc(subject.teacher)}</span></div>` : ''}
    </div>
    <div class="card">
      <div class="avg-line"><span>Средний балл ученика:</span><strong>${formatAverage(avg)}</strong></div>
      ${distribution(marks)}
      <div class="muted small">*доли оценок, образующих средний балл</div>
    </div>
    <div class="card">
      <div class="card-title">Результаты учебного периода</div>
      ${tiles ? `<div class="tiles">${tiles}</div>` : '<div class="empty">Оценок пока нет</div>'}
    </div>`;

  body.querySelectorAll('[data-mark]').forEach((tile) =>
    tile.addEventListener('click', () => openMark(marks[Number(tile.dataset.mark)], subject.subject))
  );
}
