// «Итоги»: таблица «предмет × четверть» и экран итогов по предмету.
'use strict';

import {
  $, ABSENCES, api, avgClass, errorCard, esc, formatAverage, isAbsence, isoToday, dayMonth,
  shortDate, skeleton, state, weightedAverage,
} from './core.js';
import { markBox, openMark } from './details.js';
import { pushScreen } from './screens.js';
import { select, tap } from './haptics.js';

const SHORT = { q1: '1/4', q2: '2/4', q3: '3/4', q4: '4/4' };

export async function loadTotals(force = false) {
  const container = $('totalsContent');
  if (!state.grades || force) {
    container.innerHTML = skeleton(8);
    try {
      const { data } = await api(`/api/marks${force ? '?refresh=1' : ''}`, {
        onFresh: (fresh) => { state.grades = fresh.data; },
      });
      state.grades = data;
    } catch (error) {
      container.innerHTML = errorCard(error, 'totalsRetry');
      $('totalsRetry')?.addEventListener('click', () => loadTotals(true));
      return;
    }
  }
  render();
}

/** Цвет выставленной оценки: «5» — зелёный … «2» — красный. */
function markGrade(mark) {
  const digit = String(mark || '').match(/[2-5]/);
  return digit ? `g${digit[0]}` : '';
}

function quarterMarks(subject, key) {
  return subject.marks.filter((mark) => mark.quarter === key);
}

function render() {
  const data = state.grades;
  const quarters = data.quarters || Object.keys(SHORT).map((key) => ({ key }));
  // Годовая оценка есть только в мобильном режиме, где итоги приходят с сервера.
  const hasYear = data.subjects.some((subject) => subject.totals?.year);
  const head = `<div class="tt-row tt-head">
      <div class="tt-subject">Четверти <span class="year-pill">${esc(data.year || '')}</span></div>
      ${quarters.map((q) => `<div class="tt-cell ${q.key === data.current_quarter ? 'now' : ''}">${SHORT[q.key]}</div>`).join('')}
      ${hasYear ? '<div class="tt-cell">Год</div>' : ''}
    </div>`;

  const rows = data.subjects.map((subject, index) => `
      <div class="tt-row">
        <div class="tt-subject">${esc(subject.subject)}</div>
        ${quarters
          .map((q) => {
            // Выставленная оценка за четверть важнее расчётного среднего.
            const official = subject.totals?.terms?.[q.key]?.mark;
            const avg = weightedAverage(quarterMarks(subject, q.key));
            const grade = official ? markGrade(official) : avg == null ? '' : avgClass(avg);
            return `<button class="tt-cell ${official ? 'official' : ''} ${grade}" data-subject="${index}" data-q="${q.key}">${official ? esc(official) : formatAverage(avg)}</button>`;
          })
          .join('')}
        ${hasYear ? `<div class="tt-cell official ${markGrade(subject.totals?.year)}">${esc(subject.totals?.year || '')}</div>` : ''}
      </div>`);

  const container = $('totalsContent');
  container.innerHTML = `<div class="totals-table">${head}${rows.join('')}</div>`;
  container.querySelectorAll('[data-subject]').forEach((cell) =>
    cell.addEventListener('click', () =>
      openSubject(data.subjects[Number(cell.dataset.subject)], cell.dataset.q)
    )
  );
}

/** Сводка отметок посещаемости за четверть: «Посещаемость: Пропуск по болезни — 2, Опоздал — 1». */
function attendanceLine(counts) {
  const entries = Object.entries(counts || {});
  if (!entries.length) return '';
  const parts = entries.map(([code, count]) => `${esc(ABSENCES[code.toUpperCase()] || code)} — ${count}`);
  return `<div class="muted small attendance">Посещаемость: ${parts.join(', ')}</div>`;
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
  const official = subject.totals?.terms?.[key]?.mark;
  const yearMark = subject.totals?.year;
  const finalMark = subject.totals?.final;

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
      <div class="avg-line"><span>Средний балл ученика:</span><strong class="${avg == null ? '' : avgClass(avg)}">${formatAverage(avg)}</strong></div>
      <div class="muted small class-avg" data-class-avg hidden></div>
      ${official || yearMark || finalMark ? `<div class="muted small">${[
        official ? `Оценка за четверть: <strong>${esc(official)}</strong>` : '',
        yearMark ? `Годовая: <strong>${esc(yearMark)}</strong>` : '',
        finalMark ? `Итоговая: <strong>${esc(finalMark)}</strong>` : '',
      ].filter(Boolean).join(' · ')}</div>` : ''}
      ${attendanceLine(subject.absences?.[key])}
      ${distribution(marks)}
      <div class="muted small">*доли оценок, образующих средний балл</div>
    </div>
    <div class="card">
      <div class="card-title">Результаты учебного периода</div>
      ${tiles ? `<div class="tiles">${tiles}</div>` : '<div class="empty">Оценок пока нет</div>'}
    </div>
    <div class="card whatif">
      <div class="whatif-head">
        <div class="card-title">Если получить ещё</div>
        <button class="whatif-clear" data-clear hidden>Очистить всё</button>
      </div>
      <div class="whatif-weights">Вес
        ${WEIGHTS.map((w) => `<button class="chip" data-weight="${w}">${w}</button>`).join('')}
        <input class="whatif-custom" inputmode="decimal" placeholder="свой" aria-label="Свой вес"></div>
      <div class="whatif-add">${[5, 4, 3, 2]
        .map((value) => `<button class="mark-box mark m${value}" data-add="${value}">${value}</button>`).join('')}</div>
      <div class="whatif-list"></div>
      <div class="whatif-result"></div>
      <div class="muted small whatif-goals"></div>
    </div>`;

  body.querySelectorAll('[data-mark]').forEach((tile) =>
    tile.addEventListener('click', () => openMark(marks[Number(tile.dataset.mark)], subject.subject))
  );
  bindWhatIf(body.querySelector('.whatif'), marks, `${subject.subject}|${key}`);

  if (state.grades.mobile) loadClassAverage(body, subject.subject, key);
}

/** Средний балл класса приходит отдельным запросом: только мобильное API, и не всегда. */
async function loadClassAverage(body, subject, key) {
  try {
    const { data } = await api(`/api/subject?subject=${encodeURIComponent(subject)}&quarter=${key}`);
    const line = body.querySelector('[data-class-avg]');
    if (line && data?.available && data.class_average != null) {
      line.textContent = `у класса ${formatAverage(data.class_average)}`;
      line.hidden = false;
    }
  } catch {
    // Строка необязательная: без неё экран остаётся полным.
  }
}

/* ─────────────────────── Калькулятор «что если» ─────────────────────── */

// Свои оценки по предмету и четверти — живут до перезагрузки страницы,
// чтобы можно было выйти из предмета и вернуться к тем же прикидкам.
const whatIf = new Map();
const WEIGHTS = [1, 2, 3, 4];

/** «1,5» вместо «1.5». */
const formatWeight = (weight) => String(weight).replace('.', ',');

/** Свой вес из поля: положительное число до 100, с запятой или точкой. */
function parseWeight(text) {
  const value = Number(String(text).trim().replace(',', '.'));
  return Number.isFinite(value) && value > 0 && value <= 100 ? Math.round(value * 100) / 100 : null;
}

function bindWhatIf(card, marks, id) {
  if (!whatIf.has(id)) whatIf.set(id, { weight: 1, extra: [] });
  const plan = whatIf.get(id);
  const custom = card.querySelector('.whatif-custom');
  if (!WEIGHTS.includes(plan.weight)) custom.value = formatWeight(plan.weight);

  const render = () => {
    card.querySelectorAll('[data-weight]').forEach((chip) =>
      chip.classList.toggle('active', Number(chip.dataset.weight) === plan.weight));
    custom.classList.toggle('active', !WEIGHTS.includes(plan.weight));
    card.querySelector('[data-clear]').hidden = !plan.extra.length && WEIGHTS.includes(plan.weight);

    card.querySelector('.whatif-list').innerHTML = plan.extra
      .map((item, index) => `<button class="whatif-item" data-remove="${index}" aria-label="Убрать">
          <span class="mark-box mark m${item.mark}">${item.mark}</span>
          <span>вес ${formatWeight(item.weight)}</span><span class="x">×</span>
        </button>`)
      .join('') || '<div class="muted small">Нажмите оценку, чтобы добавить её к настоящим</div>';

    const before = weightedAverage(marks);
    const after = weightedAverage([...marks, ...plan.extra]);
    const delta = before != null && after != null ? after - before : 0;
    card.querySelector('.whatif-result').innerHTML = plan.extra.length
      ? `<span>Средний балл станет:</span><span><strong class="${after == null ? '' : avgClass(after)}">${formatAverage(after)}</strong>${
        Math.abs(delta) >= 0.005
          ? `<span class="whatif-delta ${delta > 0 ? 'up' : 'down'}">${delta > 0 ? '+' : '−'}${Math.abs(delta).toFixed(2).replace('.', ',')}</span>`
          : ''}</span>`
      : '';

    const all = [...marks, ...plan.extra];
    card.querySelector('.whatif-goals').innerHTML = [5, 4]
      .map((target) => {
        const needed = neededMarks(all, target - 0.5, 5, plan.weight);
        const text = needed === 0 ? 'уже выходит'
          : needed == null ? 'не набрать' : `ещё ${needed} × «5» с весом ${formatWeight(plan.weight)}`;
        return `До «${target}»: ${text}`;
      })
      .join(' · ');
  };

  card.addEventListener('click', (event) => {
    const weight = event.target.closest('[data-weight]');
    const add = event.target.closest('[data-add]');
    const remove = event.target.closest('[data-remove]');
    const clear = event.target.closest('[data-clear]');
    if (weight) {
      plan.weight = Number(weight.dataset.weight);
      custom.value = '';
      select();
    } else if (clear) {
      plan.extra = [];
      plan.weight = 1;
      custom.value = '';
      tap('soft');
    } else if (add) {
      plan.extra.push({ mark: add.dataset.add, weight: plan.weight });
      tap();
    } else if (remove) {
      plan.extra.splice(Number(remove.dataset.remove), 1);
      tap('soft');
    } else {
      return;
    }
    render();
  });
  custom.addEventListener('input', () => {
    const value = parseWeight(custom.value);
    // Пустое поле возвращает вес 1, недописанное («1,») ничего не меняет.
    if (value != null) plan.weight = value;
    else if (!custom.value.trim()) plan.weight = 1;
    else return;
    render();
  });
  custom.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') custom.blur();
  });
  render();
}

/** Сколько оценок `value` с весом `weight` нужно, чтобы среднее дошло до `target`. */
function neededMarks(marks, target, value, weight) {
  const current = weightedAverage(marks);
  if (current != null && current >= target) return 0;
  const extra = [];
  // Предел на случай недостижимой цели.
  while (extra.length < 100) {
    extra.push({ mark: value, weight });
    if (weightedAverage([...marks, ...extra]) >= target) return extra.length;
  }
  return null;
}
