// «Итоги»: таблица «предмет × четверть» и экран итогов по предмету.
'use strict';

import {
  $, ABSENCES, DEFAULT_THRESHOLDS, api, avgClass, errorCard, esc, formatAverage, isAbsence, isoToday, dayMonth,
  shortDate, showFailure, showLoading, skeleton, state, weightedAverage,
} from './core.js';
import { markBox, openMark } from './details.js';
import { pushScreen } from './screens.js';
import { openSheet, closeSheet } from './sheet.js';
import { select, tap } from './haptics.js';
import { getPref, setPref } from './prefs.js';
import { isNewDesign } from './appearance.js';
import {
  formatThreshold, neededMarks, thresholdPrefs, thresholdsFor, validThresholds,
} from './grading.js';

const SHORT = { q1: '1/4', q2: '2/4', q3: '3/4', q4: '4/4' };
const SCHEME_TITLE = { q: 'Четверти', t: 'Триместры', h: 'Полугодия' };

const shortLabel = (q) => q.short || SHORT[q.key] || q.key;

let selectedYear = null;
let loadVersion = 0;
let yearContext = null;

export async function loadTotals(force = false) {
  const container = $('totalsContent');
  const version = ++loadVersion;
  if (!state.totals || force) {
    const previous = state.totals || yearContext;
    if (showLoading(container, (previous ? yearPicker(previous) : '') + skeleton(8))) bindYearPicker(previous);
    const params = new URLSearchParams();
    if (selectedYear != null) params.set('year', selectedYear);
    if (force) params.set('refresh', '1');
    const path = `/api/marks${params.size ? `?${params}` : ''}`;
    const accept = (data) => {
      if (version !== loadVersion) return;
      state.totals = data;
      yearContext = data;
      if (!data.archived) state.grades = data;
      render();
    };
    try {
      const { data } = await api(path, { onFresh: (fresh) => accept(fresh.data) });
      accept(data);
      if (!force && data.years == null && version === loadVersion) loadTotals(true);
    } catch (error) {
      if (version !== loadVersion) return;
      showFailure(container, error, (previous ? yearPicker(previous) : '') + errorCard(error, 'totalsRetry'));
      bindYearPicker(previous);
      $('totalsRetry')?.addEventListener('click', () => loadTotals(true));
    }
    return;
  }
  render();
}

function yearPicker(data) {
  if (!data) return '';
  return `<div class="totals-year"><button class="year-pill" id="totalsYear" aria-label="Выбрать учебный год">${esc(data.year || '')}${data.years?.length > 1 ? ' ▾' : ''}</button>
    ${data.class_name ? `<span class="muted">${esc(data.class_name)} класс</span>` : ''}</div>`;
}

function bindYearPicker(data) {
  const button = $('totalsYear');
  if (!button) return;
  button.disabled = !(data?.years?.length > 1);
  button.onclick = () => {
    select();
    const body = openSheet('Учебный год', `<div class="year-choices">${data.years.map((year) =>
      `<button class="btn ${year.id === data.year_id ? '' : 'outline'}" data-year="${year.id}"><span>${esc(year.name)}</span><span>${esc(year.class_name ? `${year.class_name} класс` : '')}${year.current ? ' · текущий' : ''}</span></button>`
    ).join('')}</div>`);
    body.querySelectorAll('[data-year]').forEach((choice) => choice.onclick = () => {
      const year = data.years.find((item) => item.id === Number(choice.dataset.year));
      closeSheet();
      if (year.id === data.year_id) return;
      selectedYear = year.current ? null : year.id;
      yearContext = { ...data, year: year.name, year_id: year.id, class_name: year.class_name };
      state.totals = null;
      select();
      loadTotals(true);
    });
  };
}

/** Цвет выставленной оценки: «5» — зелёный … «2» — красный. */
function markGrade(mark) {
  const digit = String(mark || '').match(/[2-5]/);
  return digit ? `g${digit[0]}` : '';
}

function quarterMarks(subject, key) {
  return subject.marks.filter((mark) => mark.quarter === key);
}

/* ───────────── Новый дизайн: карточки предметов вместо таблицы ───────────── */

const VIEW_KEY = 'ns-totals-view';
let view = 'cards';
try { view = localStorage.getItem(VIEW_KEY) || 'cards'; } catch { /* приватный режим */ }
let period = null;

function setView(next) {
  view = next;
  try { localStorage.setItem(VIEW_KEY, next); } catch { /* приватный режим */ }
  select();
  render();
}

function viewToggle(label, next) {
  return `<button class="view-toggle" data-view="${next}">${label}</button>`;
}

/** Период по умолчанию — текущий, а если его нет — последний с оценками. */
function pickPeriod(data, quarters) {
  const keys = quarters.map((q) => q.key);
  if (keys.includes(period)) return period;
  if (keys.includes(data.current_quarter)) return data.current_quarter;
  return [...keys].reverse().find((key) => data.subjects.some((s) => quarterMarks(s, key).length)) || keys[0];
}

function renderCards(data, quarters) {
  period = pickPeriod(data, quarters);
  const quarter = quarters.find((q) => q.key === period);
  const rows = data.subjects.map((subject, index) => {
    const term = subject.totals?.terms?.[period];
    const marks = quarterMarks(subject, period).filter((mark) => !isAbsence(mark.mark));
    return { subject, index, official: term?.mark, marks, avg: term?.average ?? weightedAverage(marks) };
  });
  const averages = rows.map((row) => row.avg).filter((value) => value != null);
  const overall = averages.length ? averages.reduce((a, b) => a + b, 0) / averages.length : null;
  const counts = { 5: 0, 4: 0, 3: 0, 2: 0 };
  for (const row of rows) {
    for (const mark of row.marks) if (Number(mark.mark) in counts) counts[Number(mark.mark)] += 1;
  }
  const total = counts[5] + counts[4] + counts[3] + counts[2];

  const hero = `<div class="totals-hero">
      <div class="th-label">Средний балл · ${esc(quarter?.label || shortLabel(quarter || { key: period }))}</div>
      <div class="th-avg">${formatAverage(overall)}</div>
      ${total ? `<div class="th-bar">${[5, 4, 3, 2].filter((v) => counts[v])
        .map((v) => `<span class="d${v}" style="flex:${counts[v]}"></span>`).join('')}</div>` : ''}
      <div class="th-counts">${[5, 4, 3, 2].map((v) => `<span><b>${v}</b>×${counts[v]}</span>`).join('')}</div>
    </div>`;
  const chips = `<div class="period-row">
      <div class="period-chips">${quarters.map((q) =>
        `<button class="chip ${q.key === period ? 'active' : ''}" data-period="${q.key}">${esc(shortLabel(q))}</button>`).join('')}</div>
      ${viewToggle('Таблица', 'table')}
    </div>`;
  const cards = rows.map((row) => {
    const grade = row.official ? markGrade(row.official)
      : row.avg == null ? '' : avgClass(row.avg, thresholdsFor(row.subject.subject));
    const recent = row.marks.slice(-8).map((mark) => markBox(mark.mark)).join('');
    const fill = row.avg == null ? 0 : Math.max(0, Math.min(100, (row.avg - 1) / 4 * 100));
    return `<button class="card subject-card" data-subject="${row.index}" data-q="${period}">
        <div class="sc-top">
          <div class="sc-name">${esc(row.subject.subject)}</div>
          <div class="sc-avg ${grade}">${row.official ? esc(row.official) : formatAverage(row.avg)}</div>
        </div>
        <div class="sc-bar ${grade}"><span style="width:${fill}%"></span></div>
        <div class="sc-foot">
          <div class="sc-marks">${recent || '<span class="muted small">Оценок пока нет</span>'}</div>
          ${row.official && row.avg != null ? `<span class="muted small">ср. ${formatAverage(row.avg)}</span>` : ''}
        </div>
      </button>`;
  }).join('');

  const container = $('totalsContent');
  container.innerHTML = yearPicker(data) + (data.subjects.length
    ? `<div class="totals-cards">${hero}${chips}${cards}</div>`
    : '<div class="empty">За этот учебный год итоги пока не опубликованы.</div>');
  bindYearPicker(data);
  container.querySelectorAll('[data-period]').forEach((chip) => chip.addEventListener('click', () => {
    if (chip.dataset.period === period) return;
    select();
    period = chip.dataset.period;
    renderCards(data, quarters);
  }));
  container.querySelector('[data-view]')?.addEventListener('click', () => setView('table'));
  container.querySelectorAll('[data-subject]').forEach((card) => card.addEventListener('click', () => {
    tap();
    openSubject(data.subjects[Number(card.dataset.subject)], card.dataset.q);
  }));
}

function render() {
  const data = state.totals;
  const quarters = data.quarters || Object.keys(SHORT).map((key) => ({ key }));
  if (isNewDesign() && view !== 'table') {
    renderCards(data, quarters);
    return;
  }
  // Годовая оценка есть только в мобильном режиме, где итоги приходят с сервера.
  const hasYear = data.subjects.some((subject) => subject.totals?.year);
  const hasFinal = data.subjects.some((subject) => subject.totals?.final);
  const head = `<div class="tt-row tt-head">
      <div class="tt-subject">${new Set(quarters.map((q) => q.key[0])).size > 1 ? 'Периоды' : SCHEME_TITLE[quarters[0]?.key?.[0]] || 'Периоды'}</div>
      ${quarters.map((q) => `<div class="tt-cell ${q.key === data.current_quarter ? 'now' : ''}">${shortLabel(q)}</div>`).join('')}
      ${hasYear ? '<div class="tt-cell">Год</div>' : ''}
      ${hasFinal ? '<div class="tt-cell">Итог</div>' : ''}
    </div>`;

  const rows = data.subjects.map((subject, index) => `
      <div class="tt-row">
        <div class="tt-subject">${esc(subject.subject)}</div>
        ${quarters
          .map((q) => {
            // Выставленная оценка за четверть важнее расчётного среднего.
            const official = subject.totals?.terms?.[q.key]?.mark;
            const avg = subject.totals?.terms?.[q.key]?.average ?? weightedAverage(quarterMarks(subject, q.key));
            const grade = official ? markGrade(official) : avg == null ? '' : avgClass(avg, thresholdsFor(subject.subject));
            return `<button class="tt-cell ${official ? 'official' : ''} ${grade}" data-subject="${index}" data-q="${q.key}">${official ? esc(official) : formatAverage(avg)}</button>`;
          })
          .join('')}
        ${hasYear ? `<div class="tt-cell official ${markGrade(subject.totals?.year)}">${esc(subject.totals?.year || '')}</div>` : ''}
        ${hasFinal ? `<div class="tt-cell official ${markGrade(subject.totals?.final)}">${esc(subject.totals?.final || '')}</div>` : ''}
      </div>`);

  const container = $('totalsContent');
  container.innerHTML = yearPicker(data) + (isNewDesign() ? `<div class="period-row">${viewToggle('Карточки', 'cards')}</div>` : '') + (data.subjects.length
    ? `<div class="totals-table" style="--totals-columns:${quarters.length + Number(hasYear) + Number(hasFinal)}">${head}${rows.join('')}</div>`
    : '<div class="empty">За этот учебный год итоги пока не опубликованы.</div>');
  bindYearPicker(data);
  container.querySelector('[data-view]')?.addEventListener('click', () => setView('cards'));
  container.querySelectorAll('[data-subject]').forEach((cell) =>
    cell.addEventListener('click', () =>
      openSubject(data.subjects[Number(cell.dataset.subject)], cell.dataset.q)
    )
  );
}

function openArchivedSubject(subject) {
  const data = state.totals;
  const body = pushScreen('Итоги по предмету', `${data.year}${data.class_name ? ` · ${data.class_name} класс` : ''}`);
  const totals = subject.totals || {};
  body.innerHTML = `<div class="card"><div class="subject-title">${esc(subject.subject)}</div>
    ${data.quarters.map((q) => {
      const item = totals.terms?.[q.key];
      return `<div class="avg-line"><span>${esc(q.label)}</span><strong class="${markGrade(item?.mark)}">${esc(item?.mark) || formatAverage(item?.average)}</strong></div>`;
    }).join('')}
    ${totals.year ? `<div class="avg-line"><span>Годовая</span><strong>${esc(totals.year)}</strong></div>` : ''}
    ${totals.final ? `<div class="avg-line"><span>Итоговая</span><strong>${esc(totals.final)}</strong></div>` : ''}
    <p class="muted small">Итоги из «Сетевого города» за выбранный учебный год.</p></div>`;
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
    .map((value) => `<button class="dist-part d${value}" style="flex:${weights[value]}" data-mark-filter="${value}" aria-label="Показать оценки ${value}" aria-pressed="false">${value}</button>`)
    .join('')}</div>`;
}

function openSubject(subject, key) {
  const quarter = (state.totals.quarters || []).find((q) => q.key === key);
  if (state.totals.archived) {
    openArchivedSubject(subject);
    return;
  }
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
       <div class="progress-caption"><span>${esc(quarter?.label || SHORT[key] || '')} ${lessons.done}/${lessons.total}</span>
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
      <div class="avg-line"><span>Средний балл ученика:</span><strong data-avg class="${avg == null ? '' : avgClass(avg, thresholdsFor(subject.subject))}">${formatAverage(avg)}</strong></div>
      <div class="muted small class-avg" data-class-avg hidden></div>
      ${official || yearMark || finalMark ? `<div class="muted small">${[
        official ? `Оценка за ${esc((quarter?.label || 'период').replace(/^\d+\s*/, ''))}: <strong>${esc(official)}</strong>` : '',
        yearMark ? `Годовая: <strong>${esc(yearMark)}</strong>` : '',
        finalMark ? `Итоговая: <strong>${esc(finalMark)}</strong>` : '',
      ].filter(Boolean).join(' · ')}</div>` : ''}
      ${attendanceLine(subject.absences?.[key])}
      ${distribution(marks)}
      <div class="muted small">*доли оценок, образующих средний балл</div>
    </div>
    <div class="card">
      <div class="mark-results-head"><div class="card-title">Результаты учебного периода</div>
        <button class="mark-filter-reset" data-mark-reset hidden>Все оценки</button></div>
      ${tiles ? `<div class="tiles">${tiles}</div>` : '<div class="empty">Оценок пока нет</div>'}
    </div>
    <div class="card whatif">
      <div class="whatif-head">
        <div class="card-title">Если получить ещё</div>
        <button class="whatif-clear" data-clear hidden>Очистить всё</button>
      </div>
      <div class="whatif-weights">Вес <span class="whatif-chips"></span>
        <input class="whatif-custom" inputmode="decimal" enterkeyhint="done" placeholder="+ свой" aria-label="Добавить свой вес"></div>
      <div class="whatif-add">${[5, 4, 3, 2]
        .map((value) => `<button class="mark-box mark m${value}" data-add="${value}">${value}</button>`).join('')}</div>
      <div class="whatif-list"></div>
      <div class="whatif-result"></div>
      <div class="muted small whatif-goals"></div>
      <div class="thr">
        <div class="thr-line"><span class="muted small thr-text"></span>
          <button class="thr-edit" data-thr-toggle>Пороги</button></div>
        <div class="thr-editor" hidden>
          <div class="chips thr-scope">
            <button class="chip" data-scope="subject">Только этот предмет</button>
            <button class="chip" data-scope="all">Все предметы</button>
          </div>
          <div class="thr-inputs">${[5, 4, 3].map((grade) => `<label class="thr-field"><span>«${grade}» от</span>
            <input class="whatif-custom" data-thr="${grade}" inputmode="decimal" enterkeyhint="done"></label>`).join('')}</div>
          <div class="muted small thr-error"></div>
          <div class="thr-actions">
            <button class="btn" data-thr-save>Сохранить</button>
            <button class="btn outline" data-thr-reset></button>
          </div>
        </div>
      </div>
    </div>`;

  body.querySelectorAll('[data-mark]').forEach((tile) =>
    tile.addEventListener('click', () => openMark(marks[Number(tile.dataset.mark)], subject.subject))
  );
  let markFilter = null;
  const filterButtons = body.querySelectorAll('[data-mark-filter]');
  const applyMarkFilter = (value) => {
    markFilter = value;
    body.querySelector('.dist')?.classList.toggle('filtered', value != null);
    filterButtons.forEach((button) =>
      button.setAttribute('aria-pressed', String(Number(button.dataset.markFilter) === value)));
    body.querySelectorAll('[data-mark]').forEach((tile) => {
      const mark = marks[Number(tile.dataset.mark)];
      // Посещаемость остаётся видимой, как в итогах «Сетевого города».
      tile.hidden = value != null && !isAbsence(mark.mark) && Number(mark.mark) !== value;
    });
    body.querySelector('[data-mark-reset]').hidden = value == null;
    select();
  };
  filterButtons.forEach((button) => button.addEventListener('click', () => {
    const value = Number(button.dataset.markFilter);
    applyMarkFilter(markFilter === value ? null : value);
  }));
  body.querySelector('[data-mark-reset]').addEventListener('click', () => applyMarkFilter(null));
  const whatIfCard = body.querySelector('.whatif');
  const refreshWhatIf = bindWhatIf(whatIfCard, marks, `${subject.subject}|${key}`, subject.subject);
  bindThresholds(whatIfCard, subject.subject, () => {
    const strong = body.querySelector('[data-avg]');
    if (avg != null) strong.className = avgClass(avg, thresholdsFor(subject.subject));
    refreshWhatIf();
    // Таблица под экраном тоже красится по порогам.
    if ($('totalsContent').querySelector('.totals-table')) render();
  });

  if (state.totals.mobile) loadClassAverage(body, subject.subject, key);
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
const DEFAULT_WEIGHTS = [1, 2, 3, 4];

/** «1,5» вместо «1.5». */
const formatWeight = (weight) => String(weight).replace('.', ',');

/** Свой вес из поля: положительное число до 100, с запятой или точкой. */
function parseWeight(text) {
  const value = Number(String(text).trim().replace(',', '.'));
  return Number.isFinite(value) && value > 0 && value <= 100 ? Math.round(value * 100) / 100 : null;
}

/** Набор весов-чипов: свой у пользователя, общий для всех предметов, Telegram и сайта. */
function savedWeights() {
  const weights = getPref('weights');
  return Array.isArray(weights) && weights.length && weights.every((w) => parseWeight(w) === w)
    ? weights
    : [...DEFAULT_WEIGHTS];
}

function bindWhatIf(card, marks, id, subjectName) {
  const weights = savedWeights();
  if (!whatIf.has(id)) whatIf.set(id, { weight: weights[0], extra: [] });
  const plan = whatIf.get(id);
  if (!weights.includes(plan.weight)) plan.weight = weights[0];
  const custom = card.querySelector('.whatif-custom');

  const saveWeights = () => setPref('weights', weights);

  const render = () => {
    const thresholds = thresholdsFor(subjectName);
    // Последний чип не убрать: без веса нечего добавлять.
    card.querySelector('.whatif-chips').innerHTML = weights
      .map((w) => `<span class="wchip"><button class="chip ${w === plan.weight ? 'active' : ''}" data-weight="${w}">${formatWeight(w)}</button>${
        weights.length > 1 ? `<button class="wchip-x" data-drop="${w}" aria-label="Убрать вес ${formatWeight(w)}">×</button>` : ''}</span>`)
      .join('');
    card.querySelector('[data-clear]').hidden = !plan.extra.length;

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
      ? `<span>Средний балл станет:</span><span><strong class="${after == null ? '' : avgClass(after, thresholds)}">${formatAverage(after)}</strong>${
        Math.abs(delta) >= 0.005
          ? `<span class="whatif-delta ${delta > 0 ? 'up' : 'down'}">${delta > 0 ? '+' : '−'}${Math.abs(delta).toFixed(2).replace('.', ',')}</span>`
          : ''}</span>`
      : '';

    const all = [...marks, ...plan.extra];
    card.querySelector('.whatif-goals').innerHTML = [5, 4]
      .map((target) => {
        const needed = neededMarks(all, thresholds[target], 5, plan.weight);
        const text = needed === 0 ? 'уже выходит'
          : needed == null ? 'не набрать' : `ещё ${needed} × «5» с весом ${formatWeight(plan.weight)}`;
        return `До «${target}»: ${text}`;
      })
      .join(' · ');
  };

  card.addEventListener('click', (event) => {
    const drop = event.target.closest('[data-drop]');
    const weight = event.target.closest('[data-weight]');
    const add = event.target.closest('[data-add]');
    const remove = event.target.closest('[data-remove]');
    const clear = event.target.closest('[data-clear]');
    if (drop) {
      const index = weights.indexOf(Number(drop.dataset.drop));
      if (index < 0 || weights.length < 2) return;
      weights.splice(index, 1);
      if (!weights.includes(plan.weight)) plan.weight = weights[Math.min(index, weights.length - 1)];
      saveWeights();
      tap('soft');
    } else if (weight) {
      plan.weight = Number(weight.dataset.weight);
      select();
    } else if (add) {
      plan.extra.push({ mark: add.dataset.add, weight: plan.weight });
      tap();
    } else if (remove) {
      plan.extra.splice(Number(remove.dataset.remove), 1);
      tap('soft');
    } else if (clear) {
      plan.extra = [];
      plan.weight = weights[0];
      tap('soft');
    } else {
      return;
    }
    render();
  });

  // Свой вес становится чипом: по «Готово»/Enter или когда поле теряет фокус.
  const addCustom = () => {
    const value = parseWeight(custom.value);
    if (value == null) {
      custom.classList.toggle('invalid', Boolean(custom.value.trim()));
      return;
    }
    custom.value = '';
    custom.classList.remove('invalid');
    if (!weights.includes(value)) {
      if (weights.length >= 12) weights.shift();
      weights.push(value);
      weights.sort((a, b) => a - b);
      saveWeights();
    }
    plan.weight = value;
    select();
    render();
  };
  custom.addEventListener('change', addCustom);
  custom.addEventListener('input', () => custom.classList.remove('invalid'));
  custom.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') custom.blur();
  });
  render();
  return render;
}

/* ─────────────────────── Пороги оценки за четверть ─────────────────────── */

// С какого среднего ставится «5», «4», «3». Общие пороги на все предметы,
// у отдельных предметов могут быть свои: { all: {5, 4, 3}, subjects: { «Алгебра»: {…} } }.


function bindThresholds(card, subject, onChange) {
  const editor = card.querySelector('.thr-editor');
  const inputs = [...card.querySelectorAll('[data-thr]')];
  const error = card.querySelector('.thr-error');
  const resetButton = card.querySelector('[data-thr-reset]');
  // По умолчанию правим то, что сейчас действует: свои пороги предмета или общие.
  let scope = validThresholds(thresholdPrefs().subjects[subject]) ? 'subject' : 'all';

  const fill = () => {
    const prefs = thresholdPrefs();
    const own = validThresholds(prefs.subjects[subject]);
    const shown = scope === 'subject' ? own || prefs.all || DEFAULT_THRESHOLDS : prefs.all || DEFAULT_THRESHOLDS;
    inputs.forEach((input) => {
      input.value = formatThreshold(shown[input.dataset.thr]);
      input.classList.remove('invalid');
    });
    error.textContent = '';
    card.querySelectorAll('[data-scope]').forEach((chip) =>
      chip.classList.toggle('active', chip.dataset.scope === scope));
    resetButton.textContent = scope === 'subject' ? 'Как у всех' : 'По умолчанию';
    resetButton.hidden = scope === 'subject' ? !own : !prefs.all;
  };

  const renderLine = () => {
    const t = thresholdsFor(subject);
    const own = Boolean(validThresholds(thresholdPrefs().subjects[subject]));
    card.querySelector('.thr-text').textContent =
      `«5» от ${formatThreshold(t[5])} · «4» от ${formatThreshold(t[4])} · «3» от ${formatThreshold(t[3])}${own ? ' · свои для предмета' : ''}`;
  };

  const save = (next) => {
    const prefs = thresholdPrefs();
    const subjects = { ...prefs.subjects };
    let all = prefs.all;
    if (scope === 'subject') {
      if (next) subjects[subject] = next;
      else delete subjects[subject];
    } else {
      all = next;
    }
    setPref('thresholds', { ...(all ? { all } : {}), subjects });
    renderLine();
    fill();
    onChange();
  };

  card.addEventListener('click', (event) => {
    if (event.target.closest('[data-thr-toggle]')) {
      editor.hidden = !editor.hidden;
      if (!editor.hidden) fill();
      select();
    } else if (event.target.closest('[data-scope]')) {
      scope = event.target.closest('[data-scope]').dataset.scope;
      fill();
      select();
    } else if (event.target.closest('[data-thr-save]')) {
      const values = {};
      for (const input of inputs) values[input.dataset.thr] = Number(input.value.trim().replace(',', '.'));
      const next = validThresholds(values);
      inputs.forEach((input) => input.classList.toggle('invalid', !next));
      if (!next) {
        error.textContent = 'Числа от 1 до 5, и у «5» порог выше, чем у «4», а у «4» — выше, чем у «3».';
        return;
      }
      save(next);
      editor.hidden = true;
      tap();
    } else if (event.target.closest('[data-thr-reset]')) {
      save(null);
      tap('soft');
    }
  });
  inputs.forEach((input) => input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') input.blur();
  }));
  renderLine();
}
