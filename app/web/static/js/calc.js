// Калькулятор баллов: сколько и каких оценок нужно до желаемого среднего.
//
// Считается из уже загруженных оценок: количества пятёрок, четвёрок и так
// далее подставляются с учётом веса, дальше их можно править руками и
// смотреть, что получится. Предмет «Нет» — счёт с нуля, без оценок журнала.
'use strict';

import { $, api, avgClass, errorCard, esc, plural, skeleton, state } from './core.js';
import { neededMarks, thresholdsFor, thresholdsText } from './grading.js';

const MARKS = [5, 4, 3, 2];
const MARK_LABELS = { 5: 'Отлично', 4: 'Хорошо', 3: 'Удовлетв.', 2: 'Неудовл.' };

export async function loadCalc(force = false) {
  const container = $('calcContent');
  if (!state.grades || force) {
    container.innerHTML = skeleton();
    try {
      const { data } = await api('/api/marks');
      state.grades = data;
    } catch (error) {
      container.innerHTML = errorCard(error, 'calcRetry');
      $('calcRetry')?.addEventListener('click', () => loadCalc(true));
      return;
    }
  }
  if (!state.calc) {
    state.calc = {
      // По умолчанию — с нуля: калькулятор для «а что если», а оценки
      // предметов и так видны в «Оценках».
      subject: '',
      quarter: state.grades.current_quarter || state.grades.quarters?.[0]?.key || 'q1',
      counts: null,
    };
  }
  ensureCounts();
  render();
}

/** Пересчитать количества из настоящих оценок выбранного предмета. */
function ensureCounts(force = false) {
  if (state.calc.counts && !force) return;
  const subject = state.grades.subjects.find((item) => item.subject === state.calc.subject);
  const counts = { 5: 0, 4: 0, 3: 0, 2: 0 };
  for (const mark of subject?.marks || []) {
    if (mark.quarter !== state.calc.quarter) continue;
    const value = Number(mark.mark);
    if (!MARKS.includes(value)) continue;
    // Вес учитывается как количество: контрольная с весом 3 тянет
    // средний балл как три обычные оценки.
    counts[value] += Number(mark.weight) > 0 ? Number(mark.weight) : 1;
  }
  state.calc.counts = counts;
}

function average(counts) {
  const total = MARKS.reduce((sum, mark) => sum + (counts[mark] || 0), 0);
  if (!total) return { count: 0, value: null };
  const sum = MARKS.reduce((acc, mark) => acc + mark * (counts[mark] || 0), 0);
  return { count: total, value: sum / total };
}

/** Количества как оценки с весом: тот же расчёт, что в «Итогах». */
function asMarks(counts) {
  return MARKS.filter((mark) => counts[mark]).map((mark) => ({ mark, weight: counts[mark] }));
}

function render() {
  const { counts } = state.calc;
  const stats = average(counts);

  const subjects = `<option value=""${state.calc.subject ? '' : ' selected'}>Нет — считать с нуля</option>` + state.grades.subjects
    .map(
      (item) =>
        `<option value="${esc(item.subject)}"${item.subject === state.calc.subject ? ' selected' : ''}>${esc(item.subject)}</option>`
    )
    .join('');

  // Периоды — те же, что в таблице «Итогов»: четверти, триместры или полугодия школы.
  const periods = state.grades.quarters?.length
    ? state.grades.quarters
    : ['q1', 'q2', 'q3', 'q4'].map((key, i) => ({ key, label: `${i + 1} четверть` }));
  const quarters = periods
    .map(
      ({ key, label }) =>
        `<button class="chip ${state.calc.quarter === key ? 'active' : ''}" data-q="${key}">${esc(label)}</button>`
    )
    .join('');
  const thresholds = thresholdsFor(state.calc.subject);

  const rows = MARKS.map(
    (mark) => `<div class="calc-row">
        <span class="calc-label"><span class="mark m${mark}">${mark}</span> ${MARK_LABELS[mark]}</span>
        <button class="calc-btn" data-step="-1" data-mark="${mark}">−</button>
        <input class="calc-input" data-mark="${mark}" inputmode="numeric" value="${counts[mark] || 0}">
        <button class="calc-btn" data-step="1" data-mark="${mark}">+</button>
      </div>`
  ).join('');

  const goals = [4, 5]
    .map((target) => {
      const needed = neededMarks(asMarks(counts), thresholds[target]);
      const text =
        needed === 0
          ? 'уже достигнуто'
          : needed === null
            ? 'недостижимо'
            : `ещё ${needed} ${plural(needed, 'пятёрка', 'пятёрки', 'пятёрок')}`;
      return `<div class="row"><span class="label">До «${target}»</span><span>${text}</span></div>`;
    })
    .join('');

  $('calcContent').innerHTML = `
    <div class="card">
      <div class="card-title">Предмет</div>
      <select class="calc-input" id="calcSubject" style="width:100%;text-align:left">${subjects}</select>
      ${state.calc.subject ? `<div class="chips" style="margin-top:10px">${quarters}</div>` : ''}
    </div>
    <div class="card">
      <div class="card-title">Оценки с учётом веса</div>
      ${rows}
      <div class="calc-result">
        Средний балл: <strong class="avg ${stats.value == null ? '' : avgClass(stats.value, thresholds)}">${stats.value ? stats.value.toFixed(2) : '—'}</strong>
        <span class="muted small"> · суммарный вес ${stats.count}</span>
      </div>
    </div>
    <div class="card">
      <div class="card-title">Что нужно, чтобы вышло</div>
      ${goals}
      <p class="muted small" style="margin-top:8px">
        Пороги как в «Итогах»: ${esc(thresholdsText(state.calc.subject))}.
        ${state.calc.subject ? 'Веса берутся из журнала, значения можно править вручную.' : 'Добавляйте оценки кнопками «+», вес считается количеством.'}
      </p>
    </div>`;

  bind();
}

function bind() {
  $('calcSubject').addEventListener('change', (event) => {
    state.calc.subject = event.target.value;
    ensureCounts(true);
    render();
  });
  $('calcContent').querySelectorAll('[data-q]').forEach((chip) => {
    chip.addEventListener('click', () => {
      state.calc.quarter = chip.dataset.q;
      ensureCounts(true);
      render();
    });
  });
  $('calcContent').querySelectorAll('[data-step]').forEach((button) => {
    button.addEventListener('click', () => {
      const mark = button.dataset.mark;
      const next = (state.calc.counts[mark] || 0) + Number(button.dataset.step);
      state.calc.counts[mark] = Math.max(0, next);
      render();
    });
  });
  $('calcContent').querySelectorAll('.calc-input[data-mark]').forEach((input) => {
    input.addEventListener('change', () => {
      const value = Math.max(0, Number(input.value) || 0);
      state.calc.counts[input.dataset.mark] = value;
      render();
    });
  });
}
