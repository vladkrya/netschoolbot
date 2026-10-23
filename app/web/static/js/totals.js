// «Итоги»: таблица «предмет × четверть» и экран итогов по предмету.
'use strict';

import {
  $, ABSENCES, DEFAULT_THRESHOLDS, api, avgClass, errorCard, esc, formatAverage, isAbsence, isoToday, dayMonth,
  markValue, plural, shortDate, showFailure, showLoading, skeleton, state, syncFinished, syncStarted, weightedAverage, motionAtLeast, demo,
} from './core.js';
import { markBox, openMark } from './details.js';
import { keepAcrossRestart, markScreen, pushScreen } from './screens.js';
import { ICONS } from './icons.js';
import { openSheet, closeSheet } from './sheet.js';
import { select, tap } from './haptics.js';
import { getPref, setPref } from './prefs.js';
import { isNewDesign } from './appearance.js';
import { playAverages, tweenNumber } from './motion.js';
import {
  formatThreshold, neededMarks, thresholdPrefs, thresholdsFor, validThresholds,
} from './grading.js';

const SHORT = { q1: '1/4', q2: '2/4', q3: '3/4', q4: '4/4' };
const SCHEME_TITLE = { q: 'Четверти', t: 'Триместры', h: 'Полугодия' };

const shortLabel = (q) => q.short || SHORT[q.key] || q.key;
// Переключатель периода в новом дизайне: «1 четв.» понятнее, чем «1/4».
const SEG_WORD = { q: 'четв.', t: 'трим.', h: 'полуг.' };
const segLabel = (q) => (/^[qth]\d$/.test(q.key) ? `${q.key[1]} ${SEG_WORD[q.key[0]]}` : shortLabel(q));

let selectedYear = null;
let loadVersion = 0;
let yearContext = null;

// Дневник принёс новую оценку (day.js) или о ней сообщил сервер (app.js):
// год, загруженный раньше, при следующем открытии сверяется со школой.
let outdated = false;
window.addEventListener('netschool:marksoutdated', () => { outdated = true; });

export async function loadTotals(force = false) {
  const container = $('totalsContent');
  // Устаревшее остаётся на экране, пока идёт запрос, — без скелетона.
  const behind = !force && outdated && state.totals && !state.totals.archived;
  if (state.totals && !force && !behind) {
    render();
    return;
  }
  outdated = false;
  const version = ++loadVersion;
  const previous = state.totals || yearContext;
  if (behind) {
    render();
    syncStarted();
  } else if (showLoading(container, (previous ? yearPicker(previous) : '') + skeleton(8))) bindYearPicker(previous);
  const params = new URLSearchParams();
  if (selectedYear != null) params.set('year', selectedYear);
  if (force || behind) params.set('refresh', '1');
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
    // Не вышло — на экране остаётся показанное, сверим в следующий раз.
    if (behind) {
      outdated = true;
      return;
    }
    showFailure(container, error, (previous ? yearPicker(previous) : '') + errorCard(error, 'totalsRetry'));
    bindYearPicker(previous);
    $('totalsRetry')?.addEventListener('click', () => loadTotals(true));
  } finally {
    if (behind) syncFinished();
  }
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

function isAttendance(mark) {
  return mark.type === 'Посещаемость' || isAbsence(mark.mark);
}

function quarterMarks(subject, key) {
  const attendance = (subject.attendance || []).filter((entry) => !subject.marks.some((mark) =>
    mark.mark === entry.mark && mark.date === entry.date && mark.lesson_number === entry.lesson_number));
  return [...subject.marks, ...attendance].filter((mark) => mark.quarter === key)
    .sort((a, b) => a.date.localeCompare(b.date));
}

/* ───────────── Новый дизайн: карточки предметов вместо таблицы ───────────── */

const VIEW_KEY = 'ns-totals-view';
const SORT_KEY = 'ns-totals-sort';
const readLocal = (key, fallback) => {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
};
const writeLocal = (key, value) => {
  try { localStorage.setItem(key, value); } catch { /* приватный режим */ }
};
let view = readLocal(VIEW_KEY, 'cards');
// alpha — по алфавиту, weak — слабые сверху, strong — сильные сверху.
const SORTS = { alpha: 'По алфавиту', weak: 'Слабые сверху', strong: 'Сильные сверху' };
let sort = readLocal(SORT_KEY, 'alpha');
if (!(sort in SORTS)) sort = 'alpha';
// Что считают плашки 5/4/3/2 в сводке: subjects — сколько предметов выходит
// на «5», «4»…, marks — сколько всего таких оценок.
const COUNTS_KEY = 'ns-totals-counts';
let countMode = readLocal(COUNTS_KEY, 'subjects') === 'marks' ? 'marks' : 'subjects';
let period = null;

/** Закреплённые предметы — сверху списка, в общих настройках. */
const pins = () => new Set(Array.isArray(getPref('pins')) ? getPref('pins') : []);
function togglePin(name) {
  const set = pins();
  if (set.has(name)) set.delete(name);
  else set.add(name);
  setPref('pins', [...set]);
}

const normName = (value) => String(value || '').toLowerCase().split(/\s+/).filter(Boolean).join(' ');

/** Архивный год приходит без оценок: период подгружается целиком, когда его
 *  выбирают. Без этого карточки показывали «Оценок пока нет». */
async function ensureArchivedPeriod(data, key) {
  data.loadedPeriods = data.loadedPeriods || new Set();
  if (!data.archived || data.loadedPeriods.has(key)) return false;
  const params = new URLSearchParams({ year: data.year_id, quarter: key });
  const merge = (payload) => {
    const byName = new Map();
    for (const mark of payload.marks || []) {
      const name = normName(mark.subject);
      byName.set(name, [...(byName.get(name) || []), mark]);
    }
    for (const subject of data.subjects) {
      subject.marks = subject.marks.filter((mark) => mark.quarter !== key)
        .concat(byName.get(normName(subject.subject)) || []);
    }
    data.loadedPeriods.add(key);
  };
  const { data: payload } = await api(`/api/marks/period?${params}`, { onFresh: (fresh) => merge(fresh.data) });
  merge(payload);
  return true;
}

function setView(next) {
  view = next;
  writeLocal(VIEW_KEY, next);
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

const signed = (value) => `${value > 0 ? '+' : '−'}${formatAverage(Math.abs(value))}`;

const MARK_WORDS = {
  5: ['пятёрка', 'пятёрки', 'пятёрок'],
  4: ['четвёрка', 'четвёрки', 'четвёрок'],
  3: ['тройка', 'тройки', 'троек'],
};
// Больше стольких оценок одного вида в подсказку не пишем — строка разрастётся.
const GOAL_LIMIT = 20;

/** Какими оценками добрать средний до порога: «1 пятёрка или 2 четвёрки».
 *  Оценки, которыми порога не достичь, в подсказку не попадают. */
function neededText(marks, threshold) {
  const options = [5, 4, 3]
    .map((value) => ({ value, needed: neededMarks(marks, threshold, value) }))
    .filter(({ value, needed }) => needed && (value === 5 || needed <= GOAL_LIMIT))
    .map(({ value, needed }) => `${needed} ${plural(needed, ...MARK_WORDS[value])}`);
  if (!options.length) return '';
  return options.length > 1 ? `${options.slice(0, -1).join(', ')} или ${options.at(-1)}` : options[0];
}

/** Цели на четверть по предметам (prefs.goals): «5», «4» или «3». */
export function subjectGoal(subject) {
  const goal = Number((getPref('goals') || {})[subject]);
  return [3, 4, 5].includes(goal) ? goal : null;
}

function saveGoal(subject, goal) {
  const goals = { ...(getPref('goals') || {}) };
  if (goal) goals[subject] = String(goal);
  else delete goals[subject];
  setPref('goals', goals);
}

/** Своя цель: сколько осталось до неё. Без цели — до следующей оценки. */
function goalProgress(subject, marks, avg) {
  const goal = subjectGoal(subject);
  if (!goal || avg == null) return '';
  const threshold = thresholdsFor(subject)[goal];
  if (avg >= threshold) return `Цель «${goal}» — выходит ✓`;
  const needed = neededText(marks, threshold);
  return needed ? `Цель «${goal}»: ещё ${needed}` : `Цель «${goal}»: в этой четверти уже не успеть`;
}

/** Что нужно до следующей оценки: «до «4» ещё 1 пятёрка или 2 четвёрки». */
function goalText(row) {
  if (row.official || row.avg == null) return '';
  const own = goalProgress(row.subject.subject, row.marks, row.avg);
  if (own) return own;
  const thresholds = thresholdsFor(row.subject.subject);
  const target = row.avg >= thresholds[5] ? null : row.avg >= thresholds[4] ? 5 : row.avg >= thresholds[3] ? 4 : 3;
  if (!target) return 'Выходит «5»';
  const text = neededText(row.marks, thresholds[target]);
  return text ? `До «${target}» — ещё ${text}` : '';
}

function periodRows(data) {
  return data.subjects.map((subject, index) => {
    const term = subject.totals?.terms?.[period];
    const marks = quarterMarks(subject, period).filter((mark) => !isAttendance(mark));
    const avg = term?.average ?? weightedAverage(marks);
    // Как сдвинула среднее последняя оценка. Оба средних — по оценкам из
    // списка: школьный средний сюда не годится, иначе в разницу попадёт
    // расхождение журнала с нашим счётом, а не сама оценка.
    const now = weightedAverage(marks);
    const before = marks.length > 1 ? weightedAverage(marks.slice(0, -1)) : null;
    const trend = now != null && before != null ? now - before : 0;
    return { subject, index, official: term?.mark, marks, avg, trend };
  });
}

/** Стрелка в таблице: куда сдвинула средний последняя оценка периода. */
function trendArrow(marks) {
  const numeric = marks.filter((mark) => !isAttendance(mark));
  if (numeric.length < 2) return '';
  const now = weightedAverage(numeric);
  const before = weightedAverage(numeric.slice(0, -1));
  if (now == null || before == null || Math.abs(now - before) < 0.005) return '';
  return `<span class="tt-trend ${now > before ? 'up' : 'down'}" aria-label="${now > before ? 'растёт' : 'падает'}">${now > before ? '↑' : '↓'}</span>`;
}

/** Какая оценка за период выходит по предмету: выставленная или по среднему. */
function expectedGrade(row) {
  const grade = row.official ? markGrade(row.official)
    : row.avg == null ? '' : avgClass(row.avg, thresholdsFor(row.subject.subject));
  const digit = Number(String(grade).slice(1));
  return digit >= 2 && digit <= 5 ? digit : null;
}

/** Все предметы с оценками за период уже получили выставленную оценку. */
const allOfficial = (rows) => rows.some((row) => row.official)
  && rows.every((row) => row.official || row.avg == null);

const markWeight = (mark) => (Number(mark.weight) > 0 ? Number(mark.weight) : 1);

/** Сводка периода: сколько предметов выходит на «5», «4»… или сколько всего
 *  таких оценок — переключателем. Общий средний балл по всем предметам ничего
 *  не говорит, его здесь больше нет. */
function heroHtml(rows, quarter) {
  const counts = { 5: 0, 4: 0, 3: 0, 2: 0 };
  // Сумма весов: оценка с весом 3 тянет средний, как три с весом 1, и полоса
  // показывает доли по весу, как «доли оценок» на экране предмета.
  const weights = { 5: 0, 4: 0, 3: 0, 2: 0 };
  if (countMode === 'subjects') {
    for (const row of rows) {
      const grade = expectedGrade(row);
      if (grade) counts[grade] += 1;
    }
  } else {
    for (const row of rows) {
      for (const mark of row.marks) {
        const value = markValue(mark.mark);
        if (!(value in counts)) continue;
        counts[value] += 1;
        weights[value] += markWeight(mark);
      }
    }
  }
  const total = counts[5] + counts[4] + counts[3] + counts[2];
  const weightTotal = weights[5] + weights[4] + weights[3] + weights[2];
  const share = countMode === 'subjects' ? counts : weights;
  const caption = countMode === 'subjects'
    ? (total ? `${allOfficial(rows) ? 'Вышли' : 'Выходит'} по ${total} ${plural(total, 'предмету', 'предметам', 'предметам')}` : 'Оценок за период пока нет')
    : weightTotal !== total ? `${weightTotal} с\u00a0весом` : `${total} ${plural(total, 'оценка', 'оценки', 'оценок')}`;
  // Сколько штук без учёта веса — второй строкой помельче, а не в заголовке:
  // «83 оценки · с весом 102» не влезал в строку рядом с переключателем.
  const plain = countMode === 'marks' && weightTotal !== total
    ? `<div class="th-plain">без учёта веса: ${total}</div>` : '';
  const unit = (value) => (countMode === 'subjects' ? plural(value, 'предмет', 'предмета', 'предметов') : plural(value, 'оценка', 'оценки', 'оценок'));
  return `<div class="totals-hero glass">
      <div class="th-head">
        <div>
          <div class="th-label">${esc(quarter?.label || shortLabel(quarter || { key: period }))}</div>
          <div class="th-caption">${esc(caption)}</div>${plain}
        </div>
        <div class="seg-glass th-mode" style="--n:2;--i:${countMode === 'subjects' ? 0 : 1}">
          <span class="seg-lens" aria-hidden="true"></span>
          <button class="${countMode === 'subjects' ? 'on' : ''}" data-count="subjects">Итоговые</button>
          <button class="${countMode === 'marks' ? 'on' : ''}" data-count="marks">Все</button>
        </div>
      </div>
      ${total ? `<div class="th-bar">${[5, 4, 3, 2].filter((v) => share[v])
        .map((v) => `<span class="d${v}" style="flex:${share[v]}"></span>`).join('')}</div>` : ''}
      <div class="th-counts">${[5, 4, 3, 2].map((v) => {
        // «Все» — сразу с весом: оценка с весом 3 считается за три.
        const weighted = countMode === 'marks' && weights[v] !== counts[v];
        const shown = countMode === 'marks' ? weights[v] : counts[v];
        return `<button class="c${v}" data-grade="${v}" ${counts[v] ? '' : 'disabled'}
          aria-label="${v}: ${counts[v]} ${unit(counts[v])}${weighted ? `, с весом ${weights[v]}` : ''}"><b>${v}</b><span>${shown} <small>${
          weighted ? 'с весом' : unit(shown)}</small>${weighted ? `<small class="th-weight">без веса: ${counts[v]}</small>` : ''}</span></button>`;
      }).join('')}</div>
    </div>`;
}

/** Порядок карточек: закреплённые сверху, внутри — по выбранной сортировке. */
function orderRows(rows) {
  const score = (row) => row.avg ?? expectedGrade(row);
  const ordered = [...rows];
  if (sort === 'weak') ordered.sort((a, b) => (score(a) ?? 9) - (score(b) ?? 9));
  if (sort === 'strong') ordered.sort((a, b) => (score(b) ?? -1) - (score(a) ?? -1));
  const pinned = pins();
  return [...ordered.filter((row) => pinned.has(row.subject.subject)),
    ...ordered.filter((row) => !pinned.has(row.subject.subject))];
}

/** Заготовки карточек предметов, пока грузятся оценки. */
function skeletonCards(count) {
  return Array.from({ length: count }, () => `<div class="card subject-card glass sc-skeleton">
      <div class="skeleton" style="width:55%"></div>
      <div class="skeleton" style="height:6px"></div>
      <div class="skeleton" style="width:40%"></div>
    </div>`).join('');
}

function cardsHtml(rows, averages) {
  const pinned = pins();
  return orderRows(rows).map((row) => {
    const isPinned = pinned.has(row.subject.subject);
    const grade = row.official ? markGrade(row.official)
      : row.avg == null ? '' : avgClass(row.avg, thresholdsFor(row.subject.subject));
    const recent = row.marks.slice(-10).map((mark) => markBox(mark.mark, '', mark.weight || 1)).join('');
    const fill = row.avg == null ? 0 : Math.max(0, Math.min(100, (row.avg - 1) / 4 * 100));
    const trend = Math.abs(row.trend) >= 0.005 && !row.official
      ? `<span class="sc-trend ${row.trend > 0 ? 'up' : 'down'}">${row.trend > 0 ? '▲' : '▼'} ${signed(row.trend)}</span>` : '';
    return `<button class="card subject-card glass" data-subject="${row.index}">
        <div class="sc-top">
          <span class="sc-pin${isPinned ? ' on' : ''}" role="button" data-pin aria-pressed="${isPinned}"
            aria-label="${isPinned ? 'Открепить' : 'Закрепить сверху'}">${ICONS.pin}</span>
          <div class="sc-name">${esc(row.subject.subject)}</div>
          ${trend}
          <div class="sc-avg ${grade}"${row.official || row.avg == null ? '' : ` data-odo="${esc(`${row.subject.subject}|${period}`)}"`}>${row.official ? esc(row.official) : formatAverage(row.avg)}</div>
        </div>
        <div class="sc-bar ${grade}"><span style="width:${fill}%"></span>${goalMark(row)}</div>
        <div class="sc-marks">${recent || '<span class="muted small">Оценок пока нет</span>'}</div>
        ${footHtml(row, averages?.[row.subject.subject])}
      </button>`;
  }).join('');
}

/** Риска на полосе среднего: где порог своей цели. */
function goalMark(row) {
  const goal = subjectGoal(row.subject.subject);
  if (!goal || row.official) return '';
  const at = Math.max(0, Math.min(100, (thresholdsFor(row.subject.subject)[goal] - 1) / 4 * 100));
  return `<i class="sc-goal" style="left:${at}%" aria-hidden="true"></i>`;
}

/** Низ карточки: что нужно до следующей оценки, справа — средний ученика
 *  (если стоит выставленная) и средний класса. */
function footHtml(row, classAvg) {
  const goal = goalText(row);
  const right = [
    row.official && row.avg != null ? `ср. ${formatAverage(row.avg)}` : '',
    classAvg != null ? `у класса ${formatAverage(classAvg)}` : '',
  ].filter(Boolean).join(' · ');
  return goal || right ? `<div class="sc-foot"><span>${esc(goal)}</span>${right ? `<span>${right}</span>` : ''}</div>` : '';
}

// Средние баллы класса за период (только мобильное API): «год|период» → { предмет: балл }.
const classAverages = new Map();

/** Средние класса приходят одним запросом на период и дописываются в карточки. */
async function loadClassAverages(data, list, rows) {
  if (!(data.mobile || demo) || data.archived) return;
  const key = `${data.year_id}|${period}`;
  if (!classAverages.has(key)) {
    const wanted = period;
    try {
      const { data: payload } = await api(`/api/class-averages?quarter=${wanted}`);
      classAverages.set(key, payload?.available ? payload.averages : null);
    } catch {
      return; // Строка необязательная.
    }
    if (wanted !== period || !list.isConnected) return;
  }
  const averages = classAverages.get(key);
  if (!averages) return;
  for (const row of rows) {
    const card = list.querySelector(`[data-subject="${row.index}"]`);
    if (!card) continue;
    card.querySelector('.sc-foot')?.remove();
    card.insertAdjacentHTML('beforeend', footHtml(row, averages[row.subject.subject]));
  }
}

let lastEntrance = 0;

function renderCards(data, quarters) {
  period = pickPeriod(data, quarters);
  const index = Math.max(0, quarters.findIndex((q) => q.key === period));
  const container = $('totalsContent');
  container.innerHTML = yearPicker(data) + (data.subjects.length
    ? `<div class="totals-cards">
        <div data-hero></div>
        <div class="period-row">
          <div class="seg-glass glass" style="--n:${quarters.length};--i:${index}">
            <span class="seg-lens" aria-hidden="true"></span>
            ${quarters.map((q) => `<button class="${q.key === period ? 'on' : ''}" data-period="${q.key}">${esc(segLabel(q))}</button>`).join('')}
          </div>
        </div>
        <div class="period-row tools">
          <button class="glass-pill glass ${sort === 'alpha' ? '' : 'on'}" data-sort>${SORTS[sort]}</button>
          ${viewToggle('Таблица', 'table')}
        </div>
        <div data-list></div>
      </div>`
    : '<div class="empty">За этот учебный год итоги пока не опубликованы.</div>');
  bindYearPicker(data);
  if (!data.subjects.length) return;

  const hero = container.querySelector('[data-hero]');
  const list = container.querySelector('[data-list]');
  const fill = (animate) => {
    const rows = periodRows(data);
    hero.innerHTML = heroHtml(rows, quarters.find((q) => q.key === period));
    list.innerHTML = cardsHtml(rows, classAverages.get(`${data.year_id}|${period}`));
    loadClassAverages(data, list, rows);
    // Средний сдвинулся с прошлого показа — цифры прокручиваются.
    // При входе во вкладку цифры прокручиваются всегда, при смене периода и
    // повторной отрисовке свежими данными — только если средний сдвинулся.
    const entrance = animate && Date.now() - lastEntrance > 4000;
    if (entrance) lastEntrance = Date.now();
    playAverages(list, { entrance });
    // Лесенка появления — только при открытии вкладки, не при смене периода.
    list.classList.toggle('rise', animate);
    if (data.archived && !data.loadedPeriods?.has(period)) {
      // Оценки прошлого года ещё едут: вместо карточек «Оценок пока нет» —
      // мерцающие заготовки, как при обычной загрузке.
      const key = period;
      hero.querySelector('.totals-hero')?.classList.add('is-loading');
      list.innerHTML = skeletonCards(Math.min(rows.length || 4, 6));
      list.classList.remove('rise');
      ensureArchivedPeriod(data, key).then(() => {
        if (key === period && list.isConnected) fill(false);
      }, () => {
        if (key !== period || !list.isConnected) return;
        hero.querySelector('.totals-hero')?.classList.remove('is-loading');
        list.innerHTML = '<div class="empty">Не удалось загрузить оценки за этот период.</div>';
      });
    }
  };
  fill(true);
  hero.addEventListener('click', (event) => {
    const grade = event.target.closest('[data-grade]');
    if (grade) {
      openGrade(data, quarters.find((q) => q.key === period), Number(grade.dataset.grade));
      return;
    }
    const button = event.target.closest('[data-count]');
    if (!button || button.dataset.count === countMode) return;
    select();
    countMode = button.dataset.count;
    writeLocal(COUNTS_KEY, countMode);
    // Переключатель остаётся тем же элементом — линза переезжает, как у
    // периода; заменяется только остальная сводка.
    const control = button.closest('.th-mode');
    control.style.setProperty('--i', countMode === 'subjects' ? 0 : 1);
    control.querySelectorAll('[data-count]').forEach((b) => b.classList.toggle('on', b === button));
    const fresh = document.createElement('div');
    fresh.innerHTML = heroHtml(periodRows(data), quarters.find((q) => q.key === period));
    const box = hero.querySelector('.totals-hero');
    box.querySelector('.th-head > div').replaceWith(fresh.querySelector('.th-head > div'));
    box.querySelector('.th-bar')?.remove();
    const bar = fresh.querySelector('.th-bar');
    if (bar) box.querySelector('.th-counts').before(bar);
    box.querySelector('.th-counts').replaceWith(fresh.querySelector('.th-counts'));
  });

  // Период меняется на месте: линза переезжает, карточки перерисовываются.
  // Именно переключатель периода: в сводке над ним есть свой (.th-mode).
  const control = container.querySelector('.period-row .seg-glass');
  control.addEventListener('click', (event) => {
    const button = event.target.closest('[data-period]');
    if (!button || button.dataset.period === period) return;
    select();
    period = button.dataset.period;
    control.style.setProperty('--i', quarters.findIndex((q) => q.key === period));
    control.querySelectorAll('[data-period]').forEach((b) => b.classList.toggle('on', b === button));
    fill(false);
    list.animate([{ opacity: 0.4, transform: 'translateY(4px)' }, { opacity: 1, transform: 'none' }],
      { duration: 260, easing: 'ease-out' });
  });
  container.querySelector('[data-sort]').addEventListener('click', (event) => {
    select();
    const order = Object.keys(SORTS);
    sort = order[(order.indexOf(sort) + 1) % order.length];
    writeLocal(SORT_KEY, sort);
    event.currentTarget.classList.toggle('on', sort !== 'alpha');
    event.currentTarget.textContent = SORTS[sort];
    fill(false);
  });
  container.querySelector('[data-view]')?.addEventListener('click', () => setView('table'));
  list.addEventListener('click', (event) => {
    const card = event.target.closest('[data-subject]');
    if (!card) return;
    if (event.target.closest('[data-pin]')) {
      select();
      togglePin(data.subjects[Number(card.dataset.subject)].subject);
      fill(false);
      return;
    }
    tap();
    openSubject(data.subjects[Number(card.dataset.subject)], period);
  });
}

/** Плашка «5»/«4»… в сводке: предметы, которые на неё выходят, или где
 *  стоят такие оценки — смотря что считает сводка. */
function openGrade(data, quarter, value) {
  const byMarks = countMode === 'marks';
  const rows = periodRows(data).filter((row) => (byMarks
    ? row.marks.some((mark) => markValue(mark.mark) === value)
    : expectedGrade(row) === value));
  const title = byMarks ? `Оценки «${value}»`
    : `${rows.length && rows.every((row) => row.official) ? 'Вышла' : 'Выходит'} «${value}»`;
  const body = pushScreen(title, quarter?.label || '');
  body.closest('.screen')?.classList.add('glassy');
  const shown = period;
  const draw = () => {
    body.innerHTML = rows.length
      ? `<div class="totals-cards">${byMarks ? gradeCardsHtml(rows, value) : cardsHtml(rows)}</div>`
      : '<div class="empty">Таких оценок за период нет.</div>';
  };
  draw();
  body.addEventListener('click', (event) => {
    const card = event.target.closest('[data-subject]');
    if (!card) return;
    if (event.target.closest('[data-pin]')) {
      select();
      togglePin(data.subjects[Number(card.dataset.subject)].subject);
      draw();
      return;
    }
    tap();
    openSubject(data.subjects[Number(card.dataset.subject)], shown);
  });
}

/** Карточки предметов с оценками одного значения: сколько их, общий вес, даты. */
function gradeCardsHtml(rows, value) {
  return orderRows(rows).map((row) => {
    const marks = row.marks.filter((mark) => markValue(mark.mark) === value);
    const weight = marks.reduce((sum, mark) => sum + markWeight(mark), 0);
    const grade = row.official ? markGrade(row.official)
      : row.avg == null ? '' : avgClass(row.avg, thresholdsFor(row.subject.subject));
    return `<button class="card subject-card glass" data-subject="${row.index}">
        <div class="sc-top">
          <div class="sc-name">${esc(row.subject.subject)}</div>
          <div class="sc-avg ${grade}">${row.official ? esc(row.official) : formatAverage(row.avg)}</div>
        </div>
        <div class="sc-marks">${marks.map((mark) => markBox(mark.mark, '', markWeight(mark))).join('')}</div>
        <div class="sc-foot"><span>${marks.length} ${plural(marks.length, 'оценка', 'оценки', 'оценок')}${
          weight !== marks.length ? ` · с учётом веса ${weight}` : ''}</span><span>${esc(marks.map((mark) => shortDate(mark.date)).join(', '))}</span></div>
      </button>`;
  }).join('');
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
            return `<button class="tt-cell ${official ? 'official' : ''} ${grade}" data-subject="${index}" data-q="${q.key}">${official ? esc(official) : formatAverage(avg)}${!official && q.key === data.current_quarter ? trendArrow(quarterMarks(subject, q.key)) : ''}</button>`;
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

async function openArchivedSubject(subject, key, data = state.totals, existingBody = null) {
  const quarter = data.quarters.find((q) => q.key === key);
  const body = existingBody || pushScreen('Итоги по предмету', `${quarter?.label || ''} · ${data.year}`);
  const load = async (force = false) => {
    showLoading(body, skeleton(3));
    const params = new URLSearchParams({ year: data.year_id, subject: subject.subject, quarter: key });
    if (force) params.set('refresh', '1');
    const accept = (fresh) => {
      if (body.isConnected) openSubject({ ...subject, ...fresh.subject }, key, body, data);
    };
    try {
      const result = await api(`/api/marks/subject?${params}`, { onFresh: (fresh) => accept(fresh.data) });
      accept(result.data);
    } catch (error) {
      if (!body.isConnected) return;
      showFailure(body, error, errorCard(error, 'archiveMarksRetry'));
      body.querySelector('#archiveMarksRetry')?.addEventListener('click', () => load(true));
    }
  };
  load();
}

/** Переход из урока/задания с периодом, соответствующим его дате. */
export async function openSubjectForDate(name, iso) {
  const body = pushScreen('Итоги по предмету', name);
  markScreen(body, 'subject-date', [name, iso]);
  showLoading(body, skeleton(3));
  try {
    let data = state.grades || (await api('/api/marks')).data;
    let quarter = data.quarters?.find((q) => q.start <= iso && iso <= q.end);
    if (!quarter) {
      const year = data.years?.find((y) => y.start <= iso && iso <= y.end);
      if (year && !year.current) data = (await api(`/api/marks?year=${year.id}`)).data;
      quarter = data.quarters?.find((q) => q.start <= iso && iso <= q.end);
    }
    const subject = data.subjects.find((s) => s.subject.trim().toLowerCase() === name.trim().toLowerCase());
    if (!body.isConnected) return;
    if (!subject || !quarter) {
      body.innerHTML = '<div class="empty">Итоги по этому предмету за выбранный период недоступны.</div>';
      return;
    }
    if (data.archived) openArchivedSubject(subject, quarter.key, data, body);
    else openSubject(subject, quarter.key, body, data);
  } catch (error) {
    if (body.isConnected) body.innerHTML = errorCard(error);
  }
}

/** Сводка отметок посещаемости за четверть: «Посещаемость: Пропуск по болезни — 2, Опоздал — 1». */
function attendanceLine(counts) {
  const entries = Object.entries(counts || {});
  if (!entries.length) return '';
  const parts = entries.map(([code, count]) => `${esc(ABSENCES[code.toUpperCase()] || code)} — ${count}`);
  return `<div class="muted small attendance">Посещаемость: ${parts.join(', ')}</div>`;
}

/** Средний после каждой числовой оценки: точки графика. */
function averagePoints(marks) {
  const points = [];
  let sum = 0;
  let weights = 0;
  for (const mark of marks) {
    const value = markValue(mark.mark);
    if (!(value >= 1 && value <= 5)) continue;
    const weight = Number(mark.weight) > 0 ? Number(mark.weight) : 1;
    sum += value * weight;
    weights += weight;
    points.push({ avg: sum / weights, mark: value, date: mark.date, weight });
  }
  return points;
}

/** Как менялся средний балл за период: после каждой оценки — средний с учётом
 *  веса. Пунктир — пороги «5», «4», «3»; нажатие на точку — подсказка. */
function averageChart(marks, thresholds) {
  const points = averagePoints(marks);
  if (points.length < 2) return '';
  const W = 320, H = 150, L = 26, R = 10, T = 12, B = 22;
  const low = Math.min(2, Math.floor(Math.min(...points.map((p) => p.avg)) * 2) / 2);
  const x = (i) => L + (W - L - R) * (points.length === 1 ? 0.5 : i / (points.length - 1));
  const y = (v) => T + (H - T - B) * (5 - v) / (5 - low);
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.avg).toFixed(1)}`).join('');
  const area = `${path}L${x(points.length - 1).toFixed(1)},${H - B}L${x(0).toFixed(1)},${H - B}Z`;
  const lines = [5, 4, 3].filter((g) => thresholds[g] > low).map((g) => `
      <line class="ac-thr" x1="${L}" x2="${W - R}" y1="${y(thresholds[g]).toFixed(1)}" y2="${y(thresholds[g]).toFixed(1)}"/>
      <text class="ac-axis" x="${L - 4}" y="${(y(thresholds[g]) + 3).toFixed(1)}" text-anchor="end">${formatAverage(thresholds[g])}</text>`).join('');
  const last = points[points.length - 1];
  const delta = last.avg - points[0].avg;
  return `<details class="card avg-chart"${readLocal(CHART_KEY, '') === 'open' ? ' open' : ''}>
      <summary class="ac-head"><div class="card-title">Как менялся средний</div>
        <span class="ac-delta ${delta > 0.005 ? 'up' : delta < -0.005 ? 'down' : ''}">${Math.abs(delta) < 0.005 ? 'без изменений' : `${delta > 0 ? '↑' : '↓'} ${formatAverage(Math.abs(delta))} за период`}</span></summary>
      <div class="ac-wrap">
        <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="График среднего балла: от ${formatAverage(points[0].avg)} до ${formatAverage(last.avg)}">
          ${lines}
          <path class="ac-area" d="${area}"/>
          <path class="ac-line" d="${path}" pathLength="1"/>
          ${points.map((p, i) => `<circle class="ac-dot d${p.mark}" style="--i:${i}" cx="${x(i).toFixed(1)}" cy="${y(p.avg).toFixed(1)}" r="4"/>`).join('')}
          ${points.map((p, i) => `<rect class="ac-hit" data-point="${i}" x="${(x(i) - (W - L - R) / points.length / 2).toFixed(1)}" y="0" width="${((W - L - R) / points.length).toFixed(1)}" height="${H}"/>`).join('')}
          <text class="ac-axis" x="${L}" y="${H - 6}">${esc(shortDate(points[0].date))}</text>
          <text class="ac-axis" x="${W - R}" y="${H - 6}" text-anchor="end">${esc(shortDate(last.date))}</text>
        </svg>
        <div class="ac-tip" hidden></div>
      </div>
    </details>`;
}

// График среднего свёрнут, пока его не раскроют; выбор запоминается.
const CHART_KEY = 'ns-avg-chart';

function bindAverageChart(body, marks) {
  const wrap = body.querySelector('.ac-wrap');
  if (!wrap) return;
  const details = wrap.closest('details');
  details.addEventListener('toggle', () => {
    writeLocal(CHART_KEY, details.open ? 'open' : '');
    if (!details.open) return;
    select();
    // Линия и точки рисуются заново, как при открытии экрана.
    wrap.querySelectorAll('.ac-line, .ac-dot').forEach((el) => el.getAnimations().forEach((a) => {
      a.cancel();
      a.play();
    }));
  });
  const points = averagePoints(marks);
  const tip = wrap.querySelector('.ac-tip');
  const dots = wrap.querySelectorAll('.ac-dot');
  const show = (index) => {
    const point = points[index];
    if (!point) return;
    dots.forEach((dot, i) => dot.classList.toggle('on', i === index));
    const dot = dots[index].getBoundingClientRect();
    const box = wrap.getBoundingClientRect();
    tip.innerHTML = `<b>${point.mark}</b>${point.weight !== 1 ? ` ×${String(point.weight).replace('.', ',')}` : ''} · ${esc(shortDate(point.date))}<br>средний ${formatAverage(point.avg)}`;
    tip.hidden = false;
    const left = Math.min(Math.max(dot.left - box.left, 50), box.width - 50);
    tip.style.left = `${left}px`;
    tip.style.top = `${dot.top - box.top}px`;
  };
  wrap.querySelectorAll('[data-point]').forEach((hit) => {
    hit.addEventListener('pointerenter', () => show(Number(hit.dataset.point)));
    hit.addEventListener('click', () => { select(); show(Number(hit.dataset.point)); });
  });
  wrap.addEventListener('pointerleave', (event) => {
    if (event.pointerType !== 'mouse') return;
    tip.hidden = true;
    dots.forEach((dot) => dot.classList.remove('on'));
  });
}

/** Доли оценок в среднем балле: полоса из отрезков по весу каждой оценки. */
function distribution(marks) {
  const weights = { 5: 0, 4: 0, 3: 0, 2: 0 };
  let total = 0;
  for (const mark of marks) {
    const value = markValue(mark.mark);
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

/** Полоса уроков периода: сколько прошло и сколько осталось. */
function lessonsHtml(lessons, quarter, key, archived) {
  if (!lessons?.total) return `<div class="muted small">${esc(quarter?.label || '')}</div>`;
  const left = Math.max(0, lessons.total - lessons.done);
  return `<div class="progress">${Array.from({ length: lessons.total }, (_, i) =>
      `<span class="seg ${i < lessons.done ? 'done' : ''}" style="--i:${i}"></span>`).join('')}</div>
     <div class="progress-caption"><span>${esc(quarter?.label || SHORT[key] || '')} ${lessons.done}/${lessons.total}</span>
     <span>${archived ? 'Закончилась' : `Осталось ${left} ${plural(left, 'урок', 'урока', 'уроков')}`}</span></div>`;
}

// Уроки будущих периодов: «год|период» → обещание { предмет: { total, done } }.
const upcomingLessons = new Map();

function loadUpcomingLessons(data, key) {
  const id = `${data.year_id}|${key}`;
  if (!upcomingLessons.has(id)) {
    const request = api(`/api/marks/lessons?quarter=${encodeURIComponent(key)}`)
      .then(({ data: payload }) => {
        // Расписание ещё не опубликовано до конца — спросим снова в следующий раз.
        if (!payload?.lessons) upcomingLessons.delete(id);
        return payload?.lessons || null;
      });
    // Сбой тоже не запоминаем.
    request.catch(() => upcomingLessons.delete(id));
    upcomingLessons.set(id, request);
  }
  return upcomingLessons.get(id);
}

function openSubject(subject, key, existingBody = null, data = state.totals) {
  const quarter = (data.quarters || []).find((q) => q.key === key);
  if (data.archived && !existingBody) {
    openArchivedSubject(subject, key);
    return;
  }
  const body = existingBody || pushScreen('Итоги по предмету', `на ${dayMonth(isoToday())}`);
  if (!existingBody) markScreen(body, 'subject', [subject.subject, key]);
  // Экран предмета — в том же стекле, что и вкладка (new.css).
  body.closest('.screen')?.classList.add('glassy');
  const marks = quarterMarks(subject, key);
  // Будущий период «Итоги» не загружают — уроки спрашиваются отдельно.
  const upcoming = !data.archived && quarter?.start > isoToday();
  const lessons = (!upcoming && subject.lessons?.[key]) || { total: 0, done: 0 };
  // Тот же средний, что на карточке в списке: школьный, а без него — свой.
  const avg = subject.totals?.terms?.[key]?.average ?? weightedAverage(marks);
  const official = subject.totals?.terms?.[key]?.mark;
  const yearMark = subject.totals?.year;
  const finalMark = subject.totals?.final;

  const segments = `<div data-lessons>${lessonsHtml(lessons, quarter, key, data.archived)}</div>`;

  const renderTiles = (attendance) => marks
    .map(
      (mark, index) => isAttendance(mark) !== attendance ? '' : `<button class="tile ${isAttendance(mark) ? 'absence' : ''}" data-mark="${index}">
          ${markBox(mark.mark, 'big', isAttendance(mark) ? null : mark.weight || 1)}
          ${!isAttendance(mark) ? `<span class="tile-weight">${esc(mark.weight || 1)}</span>` : ''}
          <span class="tile-date">${esc(shortDate(mark.date))}</span>
        </button>`
    )
    .join('');
  const tiles = renderTiles(false);
  const attendanceTiles = renderTiles(true);

  body.innerHTML = `
    <div class="card">
      <div class="subject-title">${esc(subject.subject)}</div>
      ${segments}
      ${subject.teacher ? `<div class="muted small" style="margin-top:8px">Учитель: <span class="link">${esc(subject.teacher)}</span></div>` : ''}
    </div>
    <div class="card">
      <div class="avg-line"><span>Средний балл ученика:</span><strong data-avg ${avg == null ? '' : `data-count="${esc(`${subject.subject}|${key}`)}" data-odo-trend`} class="${avg == null ? '' : avgClass(avg, thresholdsFor(subject.subject))}">${formatAverage(avg)}</strong></div>
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
    ${data.archived ? '' : `<div class="card goal-card">
      <div class="card-title">Цель на ${esc((quarter?.label || 'период').replace(/^\d+\s*/, ''))}</div>
      <div class="chips goal-chips" role="radiogroup" aria-label="Цель">${[5, 4, 3].map((goal) =>
        `<button class="chip" data-goal="${goal}" role="radio">«${goal}»</button>`).join('')}
        <button class="chip" data-goal="0" role="radio">Без цели</button></div>
      <div class="goal-progress"><div class="goal-bar"><span></span></div>
        <div class="muted small goal-text"></div></div>
    </div>`}
    ${averageChart(marks.filter((mark) => !isAttendance(mark)), thresholdsFor(subject.subject))}
    <div class="card">
      <div class="mark-results-head"><div class="card-title">Результаты учебного периода</div>
        <button class="mark-filter-reset" data-mark-reset hidden>Все оценки</button></div>
      ${tiles ? `<div class="tiles">${tiles}</div>` : '<div class="empty">Оценок пока нет</div>'}
      ${attendanceTiles ? `<div class="tiles attendance-tiles">${attendanceTiles}</div>` : ''}
    </div>
    <div class="card whatif"${data.archived ? ' hidden' : ''}>
      <div class="whatif-head">
        <div class="card-title">Что если</div>
        <button class="whatif-clear" data-clear hidden>Очистить всё</button>
      </div>
      <div class="seg-glass whatif-mode" role="tablist" style="--n:2;--i:0" hidden>
        <span class="seg-lens" aria-hidden="true"></span>
        <button class="on" data-mode="add" role="tab" aria-selected="true">Добавить</button>
        <button data-mode="remove" role="tab" aria-selected="false">Убрать</button>
      </div>
      <div class="whatif-pane" data-pane="add">
        <div class="whatif-weights">Вес <span class="whatif-chips"></span>
          <input class="whatif-custom" inputmode="decimal" enterkeyhint="done" placeholder="+ свой" aria-label="Добавить свой вес"></div>
        <div class="whatif-add">${[5, 4, 3, 2]
          .map((value) => `<button class="mark-box mark m${value}" data-add="${value}">${value}</button>`).join('')}</div>
        <div class="whatif-list"></div>
      </div>
      <div class="whatif-pane" data-pane="remove" hidden>
        <div class="muted small whatif-hint">Нажмите оценку, чтобы убрать её из среднего</div>
        <div class="whatif-real"></div>
      </div>
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
  playAverages(body);

  body.querySelectorAll('[data-mark]').forEach((tile) =>
    tile.addEventListener('click', () => openMark(marks[Number(tile.dataset.mark)], subject.subject))
  );
  bindAverageChart(body, marks.filter((mark) => !isAttendance(mark)));
  if (upcoming) {
    loadUpcomingLessons(data, key).then((found) => {
      // Имя в расписании может отличаться регистром или пробелами.
      const name = normName(subject.subject);
      const counts = Object.entries(found || {}).find(([other]) => normName(other) === name)?.[1];
      const box = body.querySelector('[data-lessons]');
      if (counts?.total && box?.isConnected) box.innerHTML = lessonsHtml(counts, quarter, key, false);
    }, () => { /* строка необязательная */ });
  }
  let markFilter = null;
  let filterAnimation = null;
  const filterButtons = body.querySelectorAll('[data-mark-filter]');
  const applyMarkFilter = (value) => {
    const tiles = body.querySelector('.tiles');
    const oldHeight = tiles?.getBoundingClientRect().height;
    const positions = new Map([...body.querySelectorAll('[data-mark]:not([hidden])')]
      .map((tile) => [tile, tile.getBoundingClientRect()]));
    filterAnimation?.cancel();
    markFilter = value;
    body.querySelector('.dist')?.classList.toggle('filtered', value != null);
    filterButtons.forEach((button) =>
      button.setAttribute('aria-pressed', String(Number(button.dataset.markFilter) === value)));
    body.querySelectorAll('[data-mark]').forEach((tile) => {
      const mark = marks[Number(tile.dataset.mark)];
      // Посещаемость остаётся видимой, как в итогах «Сетевого города».
      tile.hidden = value != null && !isAttendance(mark) && markValue(mark.mark) !== value;
    });
    body.querySelector('[data-mark-reset]').hidden = value == null;
    if (tiles && motionAtLeast(50)) {
      const height = tiles.getBoundingClientRect().height;
      filterAnimation = tiles.animate([{ height: `${oldHeight}px` }, { height: `${height}px` }], {
        duration: 220, easing: 'cubic-bezier(.2,.8,.3,1)',
      });
      body.querySelectorAll('[data-mark]:not([hidden])').forEach((tile) => {
        tile.getAnimations().forEach((animation) => animation.cancel());
        const old = positions.get(tile);
        const next = tile.getBoundingClientRect();
        tile.animate(old ? [
          { transform: `translate(${old.left - next.left}px, ${old.top - next.top}px)` }, { transform: 'translate(0,0)' },
        ] : [ { opacity: 0, transform: 'translateY(6px) scale(.94)' }, { opacity: 1, transform: 'none' } ], {
          duration: 220, easing: 'cubic-bezier(.2,.8,.3,1)',
        });
      });
    }
    select();
  };
  filterButtons.forEach((button) => button.addEventListener('click', () => {
    const value = Number(button.dataset.markFilter);
    applyMarkFilter(markFilter === value ? null : value);
  }));
  body.querySelector('[data-mark-reset]').addEventListener('click', () => applyMarkFilter(null));
  if (data.archived) return;
  const numericMarks = marks.filter((mark) => !isAttendance(mark));
  const paintGoal = () => {
    const goal = subjectGoal(subject.subject);
    body.querySelectorAll('[data-goal]').forEach((chip) => {
      const on = Number(chip.dataset.goal) === (goal || 0);
      chip.classList.toggle('active', on);
      chip.setAttribute('aria-checked', String(on));
    });
    const progress = body.querySelector('.goal-progress');
    progress.hidden = !goal;
    if (!goal) return;
    const threshold = thresholdsFor(subject.subject)[goal];
    const share = avg == null ? 0 : Math.max(0, Math.min(100, ((avg - 1) / (threshold - 1)) * 100));
    progress.querySelector('.goal-bar span').style.width = `${share}%`;
    progress.querySelector('.goal-bar').classList.toggle('reached', avg != null && avg >= threshold);
    progress.querySelector('.goal-text').textContent = avg == null
      ? `Нужен средний от ${formatThreshold(threshold)} — оценок пока нет`
      : `Средний ${formatAverage(avg)} из нужных ${formatThreshold(threshold)}. ${goalProgress(subject.subject, numericMarks, avg)}`;
  };
  body.querySelectorAll('[data-goal]').forEach((chip) => chip.addEventListener('click', () => {
    select();
    saveGoal(subject.subject, Number(chip.dataset.goal));
    paintGoal();
    // Карточки под экраном показывают ту же цель.
    if ($('totalsContent').querySelector('.subject-card')) render();
  }));
  paintGoal();
  const whatIfCard = body.querySelector('.whatif');
  const refreshWhatIf = bindWhatIf(whatIfCard, marks, `${subject.subject}|${key}`, subject.subject);
  bindThresholds(whatIfCard, subject.subject, () => {
    const strong = body.querySelector('[data-avg]');
    if (avg != null) strong.className = avgClass(avg, thresholdsFor(subject.subject));
    refreshWhatIf();
    // Таблица под экраном тоже красится по порогам.
    if ($('totalsContent').querySelector('.totals-table')) render();
  });

  if (state.totals.mobile || demo) loadClassAverage(body, subject.subject, key);
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
// Перезапуск на новую версию (update.js) возвращает и прикидки.
keepAcrossRestart('whatif', () => [...whatIf], (entries) => entries.forEach(([id, plan]) => whatIf.set(id, plan)));

/** Предмет по названию — для возврата после перезапуска. */
export function openSubjectNamed(name, key) {
  const subject = state.totals?.subjects?.find((item) => item.subject === name);
  if (subject && !state.totals.archived) openSubject(subject, key);
  else return openSubjectForDate(name, isoToday());
}
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

/**
 * Ключи настоящих оценок, чтобы убранная осталась убранной после обновления
 * журнала: объекты оценок пересоздаются, а дата, урок и значение — нет.
 */
function markKeys(marks) {
  const seen = new Map();
  return marks.map((mark) => {
    const base = [mark.date, mark.lesson_number, mark.type, mark.mark, mark.weight].join('|');
    const n = seen.get(base) || 0;
    seen.set(base, n + 1);
    return `${base}|${n}`;
  });
}

function bindWhatIf(card, marks, id, subjectName) {
  const weights = savedWeights();
  if (!whatIf.has(id)) whatIf.set(id, { weight: weights[0], extra: [], removed: [], mode: 'add' });
  const plan = whatIf.get(id);
  // Убрать можно только оценку, которая входит в средний, — не пропуск.
  const keys = markKeys(marks);
  const graded = marks
    .map((mark, index) => ({ mark, key: keys[index] }))
    .filter(({ mark }) => !isAttendance(mark) && weightedAverage([mark]) != null);
  // Оценку исправили или удалили в журнале — её ключа больше нет.
  plan.removed = plan.removed.filter((key) => graded.some((item) => item.key === key));
  // Вкладка «Добавить» или «Убрать» запоминается вместе с прикидками.
  if (!graded.length) plan.mode = 'add';
  const kept = () => marks.filter((_, index) => !plan.removed.includes(keys[index]));
  if (!weights.includes(plan.weight)) plan.weight = weights[0];
  const custom = card.querySelector('.whatif-custom');

  const saveWeights = () => setPref('weights', weights);
  // Какой средний «станет» показан сейчас — новый досчитывает от него.
  let shown = weightedAverage(marks);

  const render = () => {
    const thresholds = thresholdsFor(subjectName);
    // Последний чип не убрать: без веса нечего добавлять.
    card.querySelector('.whatif-chips').innerHTML = weights
      .map((w) => `<span class="wchip"><button class="chip ${w === plan.weight ? 'active' : ''}" data-weight="${w}">${formatWeight(w)}</button>${
        weights.length > 1 ? `<button class="wchip-x" data-drop="${w}" aria-label="Убрать вес ${formatWeight(w)}">×</button>` : ''}</span>`)
      .join('');
    const changed = plan.extra.length || plan.removed.length;
    card.querySelector('[data-clear]').hidden = !changed;

    const removing = plan.mode === 'remove';
    const mode = card.querySelector('.whatif-mode');
    mode.hidden = !graded.length;
    mode.style.setProperty('--i', removing ? 1 : 0);
    mode.querySelectorAll('[data-mode]').forEach((button) => {
      const on = button.dataset.mode === plan.mode;
      button.classList.toggle('on', on);
      button.setAttribute('aria-selected', String(on));
    });
    mode.querySelector('[data-mode="remove"]').textContent = plan.removed.length ? `Убрать · ${plan.removed.length}` : 'Убрать';
    mode.querySelector('[data-mode="add"]').textContent = plan.extra.length ? `Добавить · ${plan.extra.length}` : 'Добавить';
    card.querySelectorAll('[data-pane]').forEach((pane) => { pane.hidden = pane.dataset.pane !== plan.mode; });
    card.querySelector('.whatif-real').innerHTML = removing
      ? graded
        .map(({ mark, key }) => {
          const off = plan.removed.includes(key);
          return `<button class="whatif-item real ${off ? 'off' : ''}" data-toggle="${esc(key)}" aria-pressed="${off}"
              aria-label="${off ? 'Вернуть' : 'Убрать'} ${esc(mark.mark)} за ${esc(shortDate(mark.date))}">
            ${markBox(mark.mark, '', mark.weight || 1)}
            <span>${esc(shortDate(mark.date))}${Number(mark.weight) > 1 ? `<span class="whatif-w"> · ${formatWeight(Number(mark.weight))}</span>` : ''}</span>
            ${off ? '<span class="x">↺</span>' : ''}
          </button>`;
        })
        .join('')
      : '';

    card.querySelector('.whatif-list').innerHTML = plan.extra
      .map((item, index) => `<button class="whatif-item" data-remove="${index}" aria-label="Убрать">
          <span class="mark-box mark m${item.mark}">${item.mark}</span>
          <span>вес ${formatWeight(item.weight)}</span><span class="x">×</span>
        </button>`)
      .join('') || '<div class="muted small">Нажмите оценку, чтобы добавить её к настоящим</div>';

    const before = weightedAverage(marks);
    const after = weightedAverage([...kept(), ...plan.extra]);
    const delta = before != null && after != null ? after - before : 0;
    card.querySelector('.whatif-result').innerHTML = changed
      ? `<span>Средний балл станет:</span><span><strong class="${after == null ? '' : avgClass(after, thresholds)}">${formatAverage(after)}</strong>${
        Math.abs(delta) >= 0.005
          ? `<span class="whatif-delta ${delta > 0 ? 'up' : 'down'}">${delta > 0 ? '+' : '−'}${Math.abs(delta).toFixed(2).replace('.', ',')}</span>`
          : ''}</span>`
      : '';
    // Добавили или убрали оценку — средний плавно переходит к новому значению.
    if (changed) tweenNumber(card.querySelector('.whatif-result strong'), shown, after);
    shown = changed && after != null ? after : before;

    const all = [...kept(), ...plan.extra];
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
    const mode = event.target.closest('[data-mode]');
    const toggle = event.target.closest('[data-toggle]');
    if (mode) {
      if (mode.dataset.mode === plan.mode) return;
      plan.mode = mode.dataset.mode;
      select();
    } else if (toggle) {
      const key = toggle.dataset.toggle;
      const index = plan.removed.indexOf(key);
      if (index < 0) plan.removed.push(key);
      else plan.removed.splice(index, 1);
      tap('soft');
    } else if (drop) {
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
      plan.removed = [];
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
