// «День»: уроки выбранного дня, как на главном экране официального приложения.
//
// Заголовок «Вчера / Сегодня / Завтра» со стрелками, календарь по нажатию на
// дату, листание дней свайпом. Карточка урока: номер и предмет, оценка в
// рамке, бейдж «Д/З» (сразу открывает задание), время и статус урока.
'use strict';

import {
  $, addDays, api, dayMonth, errorCard, esc, fullDate, isoOf, isoToday, mondayOf,
  parseIso, plural, showFailure, showLoading, skeleton, state, WEEKDAYS, WEEKDAYS_FULL, weekdayIndex, weekOffsetOf,
} from './core.js';
import { ICONS } from './icons.js';
import { isNewDesign, saveBells, showBells, showHomeworkStatus, showWeekends } from './appearance.js';
import { bellsHtml, weekCardsHtml } from './schedule.js';
import { canGoBack } from './screens.js';
import { EDGE_PX } from './swipeback.js';
import { isDoneItem, lessonStatus, loadDone, markBox, openHomework, openLesson, realMarks } from './details.js';
import { closeSheet, openSheet } from './sheet.js';
import { select, tap } from './haptics.js';
import { bindInstallCard, installCardHtml } from './install.js';

/** Загруженные дни по ISO-дате и недели, которые уже спрашивали. */
const days = new Map();
const loadedWeeks = new Set();
const pending = new Map();

// Ответ фоновой сверки мог прийти до загрузки этого модуля или после неё.
window.addEventListener('netschool:diaryfresh', event => {
  for (const day of event.detail.data.days || []) days.set(day.date, day);
  if (state.date && state.tab === 'day') renderDay();
});

export function dayData(iso) {
  return days.get(iso) || null;
}

/** Загрузить окно недель вокруг даты. Сервер отдаёт неделю и соседние. */
export async function ensureWeek(iso, force = false) {
  const offset = weekOffsetOf(iso);
  if (!force && loadedWeeks.has(offset)) return;
  if (pending.has(offset)) return pending.get(offset);

  const keep = ({ data }) => {
    for (const day of data.days || []) days.set(day.date, day);
  };
  const initial = !force && offset === 0 ? state.initialDiary : null;
  if (initial) state.initialDiary = null;
  const source = initial ? initial.then(result => {
    if (result.error) throw result.error;
    return result.payload;
  }) : api(`/api/diary?week=${offset}&window=current${force ? '&refresh=1' : ''}`, { onFresh: keep });
  const request = source
    .then((payload) => {
      keep(initial && state.initialDiaryFresh ? state.initialDiaryFresh : payload);
      // Первая загрузка — одна неделя; соседние запрашиваются при переходе.
      loadedWeeks.add(offset);
    })
    .finally(() => pending.delete(offset));
  pending.set(offset, request);
  return request;
}

/** Две недели назад и две вперёд — заранее, в фоне после первого экрана:
 *  тогда листание дней и недель не ждёт школьный сервер. Сервер без
 *  window=current отдаёт неделю с соседями, поэтому хватает двух запросов:
 *  week=-1 (−2…0) и week=1 (0…+2). */
let prefetched = false;
export async function prefetchAround() {
  if (prefetched) return;
  prefetched = true;
  for (const center of [1, -1]) {
    const weeks = [center, center * 2];
    if (weeks.every((offset) => loadedWeeks.has(offset))) continue;
    try {
      const { data } = await api(`/api/diary?week=${center}`);
      for (const day of data.days || []) days.set(day.date, day);
      weeks.forEach((offset) => loadedWeeks.add(offset));
    } catch {
      /* не вышло — неделя загрузится, когда до неё долистают */
    }
  }
  // Полоса недели и «Звонки» могли ждать этих данных.
  if (state.tab === 'day') renderHeader();
}

// Пока человек сам не выбрал день (стрелки, свайп, календарь), день
// выбирает приложение: сегодня, а если сегодня спрятанный выходной — первый
// учебный день после него. Выбор пересчитывается при каждой загрузке, а не
// только при первой: настройка «выходные» приходит с сервера уже после
// первого рисования (в Telegram и в веб-приложении она хранится на
// устройстве отдельно), и раньше экран так и оставался на воскресенье.
let autoDay = true;

/** Сегодня или, если сегодня спрятанный выходной, следующий учебный день. */
function pickDay() {
  let iso = isoToday();
  for (let step = 0; step < 2 && isHiddenWeekend(iso); step += 1) iso = addDays(iso, 1);
  return iso;
}

export async function loadDay(force = false) {
  if (!state.date || autoDay) state.date = isoToday();
  renderHeader();
  const container = $('dayContent');
  if (force || !loadedWeeks.has(weekOffsetOf(state.date))) {
    showLoading(container, skeleton());
  }
  try {
    // Спрятан ли выходной, ясно только по загруженной неделе: субботу с
    // уроками показываем.
    if (force || !loadedWeeks.has(weekOffsetOf(state.date))) await ensureWeek(state.date, force);
    if (autoDay) {
      state.date = pickDay();
      renderHeader();
      const other = weekOffsetOf(state.date) !== weekOffsetOf(isoToday());
      if (other && (force || !loadedWeeks.has(weekOffsetOf(state.date)))) {
        showLoading(container, skeleton());
        await ensureWeek(state.date, force);
      }
    }
  } catch (error) {
    showFailure(container, error, errorCard(error, 'dayRetry'));
    $('dayRetry')?.addEventListener('click', () => loadDay(true));
    return;
  }
  renderDay();
  prefetchAround();
  // Отметки «выполнено» — для подсветки Д/З; день рисуется, не дожидаясь их.
  if (showHomeworkStatus() && !state.hwDone) loadDone().then(() => { if (state.tab === 'day') renderDay(true); });
}

function relativeLabel(iso) {
  const today = isoToday();
  if (iso === today) return 'Сегодня';
  if (iso === addDays(today, -1)) return 'Вчера';
  if (iso === addDays(today, 1)) return 'Завтра';
  return WEEKDAYS_FULL[weekdayIndex(iso)];
}

function renderHeader() {
  const label = relativeLabel(state.date);
  $('dayLabel').textContent = label;
  // Когда сверху день недели, снизу только дата — без повтора.
  $('daySub').textContent = WEEKDAYS_FULL.includes(label) ? dayMonth(state.date) : fullDate(state.date);
  // В выходной без уроков «домой» — это ближайший учебный день, как при входе.
  $('dayToday').hidden = state.date === isoToday() || state.date === pickDay();
  renderWeekStrip();
}

/** Неделя полосой под датой (новый дизайн): день недели и число. Все семь
 *  дней всегда — иначе воскресенье появлялось и строка перестраивалась. */
/** Суббота или воскресенье без уроков, а выходные показывать не просили. */
export function isHiddenWeekend(iso) {
  return weekdayIndex(iso) >= 5 && !showWeekends() && !days.get(iso)?.lessons?.length;
}

function renderWeekStrip() {
  const monday = mondayOf(state.date);
  const today = isoToday();
  const dates = Array.from({ length: 7 }, (_, index) => addDays(monday, index))
    .filter((iso) => iso === state.date || !isHiddenWeekend(iso));
  $('weekStrip').innerHTML = dates.map((iso) => {
    const classes = ['week-day', iso === state.date ? 'on' : '', iso === today ? 'today' : '',
      days.get(iso)?.lessons?.length ? '' : 'free'].filter(Boolean).join(' ');
    return `<button class="${classes}" data-week-day="${iso}">
        <span class="wd-name">${WEEKDAYS[weekdayIndex(iso)]}</span>
        <span class="wd-num">${parseIso(iso).getDate()}</span>
      </button>`;
  }).join('');
}

/** Сводка дня над уроками (новый дизайн): сколько уроков, с какого по какое
 *  время, сколько заданий и какие оценки. */
function daySummary(lessons) {
  const first = lessons.find((lesson) => lesson.start)?.start;
  const last = [...lessons].reverse().find((lesson) => lesson.end)?.end;
  const homework = lessons.filter((lesson) => lesson.homework.length).length;
  const marks = lessons.flatMap((lesson) => realMarks(lesson.marks)).map((m) => markBox(m.mark)).join('');
  const parts = [`${lessons.length} ${plural(lessons.length, 'урок', 'урока', 'уроков')}`];
  if (first && last) parts.push(`${first}–${last}`);
  return `<div class="day-summary">
      <div class="ds-main">
        <div class="ds-title">${esc(parts.join(' · '))}</div>
        <div class="ds-sub">${homework ? `Д/З по ${homework} ${plural(homework, 'предмету', 'предметам', 'предметам')}` : 'Без домашних заданий'}</div>
      </div>
      ${marks ? `<div class="ds-marks">${marks}</div>` : ''}
    </div>`;
}

let renderedKey = '';

/** quiet — перерисовка по таймеру: без анимации появления и только если
 *  что-то поменялось (полоска текущего урока), иначе экран «перезагружался»
 *  каждые 30 секунд. */
function renderDay(quiet = false) {
  renderHeader();
  const day = days.get(state.date);
  const lessons = day?.lessons || [];
  const container = $('dayContent');

  if (!lessons.length) {
    renderedKey = '';
    container.innerHTML = installCardHtml()
      + `<div class="empty big">${weekdayIndex(state.date) >= 5 ? 'Выходной' : 'Уроков нет'}</div>`;
    bindInstallCard(container);
    return;
  }

  const progress = activeProgress(lessons);
  const key = `${state.date}|${progress?.index}|${progress?.label}|${state.hwDone?.size ?? '-'}`;
  if (quiet && key === renderedKey && container.children.length) return;
  renderedKey = key;
  container.classList.toggle('quiet', quiet);
  container.innerHTML = installCardHtml() + daySummary(lessons)
    + lessons.map((lesson, index) => lessonCard(lesson, index, progress?.index === index ? progress : null)).join('');
  bindInstallCard(container);
  container.querySelectorAll('[data-lesson]').forEach((card) => {
    card.addEventListener('click', (event) => {
      tap();
      const lesson = lessons[Number(card.dataset.lesson)];
      // Бейдж «Д/З» и строка задания открывают его сразу, минуя экран урока.
      if (event.target.closest('[data-hw-badge]') && lesson.homework.length) {
        openHomework(lesson.homework[0], lesson.topic);
        return;
      }
      openLesson(lesson, state.date);
    });
  });
}

function activeProgress(lessons, now = new Date()) {
  if (state.date !== isoToday()) return null;
  const minute = now.getHours() * 60 + now.getMinutes();
  for (let index = 0; index < lessons.length; index += 1) {
    const [sh, sm] = String(lessons[index].start || '').split(':').map(Number);
    const [eh, em] = String(lessons[index].end || '').split(':').map(Number);
    if (![sh, sm, eh, em].every(Number.isFinite)) continue;
    const start = sh * 60 + sm;
    const end = eh * 60 + em;
    if (end <= start || minute >= end) continue;
    if (minute < start) {
      if (start - minute > 60) return null;
      return { index, label: `Начнётся через ${start - minute} мин`, percent: (60 - start + minute) / 60 * 100 };
    }
    return { index, label: `Закончится через ${end - minute} мин`, percent: (minute - start) / (end - start) * 100 };
  }
  return null;
}

/** Цвет Д/З на главной: hw-todo — не сделано, hw-done — всё сделано.
 *  Пусто, если подсветку выключили или отметки ещё не загрузились. */
function homeworkStatus(homework) {
  if (!homework.length || !showHomeworkStatus() || !state.hwDone) return '';
  const items = homework.filter((item) => item.key || item.auto_done);
  if (!items.length) return '';
  return items.every((item) => isDoneItem(item, state.hwDone)) ? 'hw-done' : 'hw-todo';
}

function lessonCard(lesson, index, progress) {
  // Новый дизайн: коэффициент маленькой цифрой у оценки, как в «Оценках».
  const marks = realMarks(lesson.marks).map((m) => markBox(m.mark, '', isNewDesign() ? m.weight || 1 : null)).join('');
  const hasFiles = lesson.homework.some((h) => (h.attachments || []).length);
  const hasComment = lesson.homework.some((h) => h.comment);
  const status = homeworkStatus(lesson.homework);
  const time = lesson.time || [lesson.start, lesson.end].filter(Boolean).join(' – ');
  // Новый дизайн: вместо бейджа «Д/З» — начало самого задания.
  const preview = isNewDesign()
    ? lesson.homework.map((item) => String(item.text || '').replace(/\s+/g, ' ').trim()).filter(Boolean).join(' · ')
    : '';
  return `<div class="lesson-card" data-lesson="${index}">
      <div class="lc-top">
        <span class="lc-num">${esc(lesson.number ?? '')}</span>
        <span class="lc-subject">${esc(lesson.subject)}</span>
        <span class="lc-marks">${marks}</span>
      </div>
      ${lesson.is_elective ? '<div class="lc-badges"><span class="elective-badge">Факультатив</span></div>' : ''}
      ${lesson.homework.length && !preview ? `<div class="lc-badges">
        <span class="hw-badge ${status}" data-hw-badge>Д/З</span>
        ${hasFiles ? `<span class="hw-badge clip">${ICONS.clip}</span>` : ''}
        ${hasComment ? `<span class="hw-badge clip" title="Есть комментарий к заданию">${ICONS.chat}</span>` : ''}
      </div>` : ''}
      ${preview ? `<div class="lc-hw ${status}">${hasFiles ? ICONS.clip : ''}${hasComment ? ICONS.chat : ''}<span>${esc(preview)}</span></div>` : ''}
      ${progress ? `<div class="lesson-progress-label">${esc(progress.label)}</div><div class="lesson-progress"><span style="width:${Math.max(0, Math.min(100, progress.percent))}%"></span></div>` : ''}
      <div class="lc-bottom"><span>${esc(time)}</span><span>${esc(lessonStatus(lesson, state.date))}</span></div>
    </div>`;
}

/** Шторка «Звонки и неделя» (новый дизайн): звонки или неделя карточками —
 *  переключателем; выбор помнится в той же настройке, что и прежняя вкладка. */
function openWeekSheet() {
  tap();
  const monday = mondayOf(state.date);
  const body = openSheet('Звонки и неделя', '');
  const draw = () => {
    const bells = showBells();
    body.innerHTML = `
      <div class="seg-glass week-seg" role="tablist" style="--n:2;--i:${bells ? 0 : 1}">
        <span class="seg-lens" aria-hidden="true"></span>
        <button class="${bells ? 'on' : ''}" data-mode="bells" role="tab" aria-selected="${bells}">Звонки</button>
        <button class="${bells ? '' : 'on'}" data-mode="week" role="tab" aria-selected="${!bells}">Неделя</button>
      </div>
      ${bells ? `<div class="bells-note">Звонки пока в разработке: время собрано из расписания уроков и может быть неточным.</div>
        <div class="week-sheet">${bellsHtml(monday)}</div>`
        : `<div class="week-sheet">${weekCardsHtml(monday)}</div>`}`;
    body.querySelectorAll('[data-mode]').forEach((button) => button.addEventListener('click', () => {
      if ((button.dataset.mode === 'bells') === showBells()) return;
      select();
      saveBells(button.dataset.mode === 'bells');
      draw();
    }));
    body.querySelectorAll('[data-goto]').forEach((button) => button.addEventListener('click', () => {
      tap();
      closeSheet();
      slideTo(button.dataset.goto, true);
    }));
  };
  draw();
}

export async function goToDay(iso) {
  // Человек выбрал день сам — дальше приложение его не переставляет.
  autoDay = false;
  state.date = iso;
  await loadDay();
}

function shiftDay(delta, felt = false) {
  // Стрелки перешагивают спрятанные выходные.
  let iso = addDays(state.date, delta);
  for (let step = 0; step < 2 && isHiddenWeekend(iso); step += 1) iso = addDays(iso, delta);
  slideTo(iso, felt);
}

/** Перейти на день с листанием в его сторону. felt — отклик уже был (свайп). */
function slideTo(iso, felt = false) {
  if (!felt) select();
  const container = $('dayContent');
  container.classList.remove('slide-left', 'slide-right');
  // Перезапуск анимации: без чтения layout класс не срабатывает повторно.
  void container.offsetWidth;
  container.classList.add(iso > state.date ? 'slide-left' : 'slide-right');
  goToDay(iso);
}

/* ─────────────────────────── Календарь ─────────────────────────── */

export function openCalendar(selectedDate = state.date, onSelect = goToDay, { week = false } = {}) {
  tap();
  const month = parseIso(selectedDate);
  month.setDate(1);

  const body = openSheet('', '');
  const render = () => {
    const today = isoToday();
    const selectedWeek = mondayOf(selectedDate);
    const currentWeek = mondayOf(today);
    const first = new Date(month);
    const start = new Date(first);
    start.setDate(1 - ((first.getDay() + 6) % 7));
    const cells = [];
    for (let i = 0; i < 42; i += 1) {
      const date = new Date(start);
      date.setDate(start.getDate() + i);
      const iso = isoOf(date);
      const classes = [
        'cal-day',
        date.getMonth() !== month.getMonth() ? 'other' : '',
        i % 7 >= 5 ? 'weekend' : '',
        !week && iso === today ? 'today' : '',
        !week && iso === selectedDate ? 'selected' : '',
        week && mondayOf(iso) === selectedWeek ? 'week-selected' : '',
        week && currentWeek !== selectedWeek && mondayOf(iso) === currentWeek ? 'week-current' : '',
        week && i % 7 === 0 ? 'week-start' : '',
        week && i % 7 === 6 ? 'week-end' : '',
      ].filter(Boolean).join(' ');
      cells.push(`<button class="${classes}" data-date="${iso}">${date.getDate()}</button>`);
    }
    const monthName = MONTHS_NOMINATIVE[month.getMonth()];
    body.innerHTML = `
      <div class="cal-head">
        <button class="cal-arrow" data-shift="-1">‹</button>
        <div><div class="cal-month">${monthName}</div><div class="muted small">${month.getFullYear()}</div></div>
        <button class="cal-arrow" data-shift="1">›</button>
      </div>
      <div class="cal-grid${week ? ' cal-week' : ''}">
        ${WEEKDAYS.map((d) => `<div class="cal-dow">${d}</div>`).join('')}
        ${cells.join('')}
      </div>`;
    body.querySelectorAll('[data-shift]').forEach((button) =>
      button.addEventListener('click', () => {
        select();
        month.setMonth(month.getMonth() + Number(button.dataset.shift));
        render();
      })
    );
    body.querySelectorAll('[data-date]').forEach((button) =>
      button.addEventListener('click', () => {
        select();
        closeSheet();
        onSelect(button.dataset.date);
      })
    );
  };
  render();
}

const MONTHS_NOMINATIVE = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];

/* ─────────────────────────── Свайп ─────────────────────────── */

function bindSwipe() {
  const area = $('panel-day');
  let startX = 0;
  let startY = 0;
  let tracking = false;
  let armed = false;
  // Горизонтальный жест заметно длиннее вертикального — иначе это прокрутка.
  const isSwipe = (dx, dy) => Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5;
  area.addEventListener('touchstart', (event) => {
    const touch = event.touches[0];
    startX = touch.clientX;
    startY = touch.clientY;
    armed = false;
    // От самого края — жест «назад», если есть куда, а не листание дней.
    tracking = startX > EDGE_PX || !canGoBack();
  }, { passive: true });
  // Отклик — в момент, когда палец прошёл порог: как у нативного листания,
  // ещё до того, как его отпустили. Вернул палец назад — щёлкнет снова.
  area.addEventListener('touchmove', (event) => {
    if (!tracking) return;
    const touch = event.touches[0];
    const swipe = isSwipe(touch.clientX - startX, touch.clientY - startY);
    if (swipe && !armed) select();
    armed = swipe;
  }, { passive: true });
  area.addEventListener('touchend', (event) => {
    if (!tracking) return;
    tracking = false;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - startX;
    if (!isSwipe(dx, touch.clientY - startY)) return;
    if (!armed) select();
    shiftDay(dx < 0 ? 1 : -1, true);
  }, { passive: true });
}

export function initDay() {
  setInterval(() => { if (state.tab === 'day' && dayData(state.date)) renderDay(true); }, 30000);
  $('dayPrev').addEventListener('click', () => shiftDay(-1));
  $('dayNext').addEventListener('click', () => shiftDay(1));
  $('dayTitle').addEventListener('click', () => openCalendar());
  $('dayBells').addEventListener('click', openWeekSheet);
  $('dayToday').addEventListener('click', () => slideTo(pickDay()));
  // Отметку поставили на экране урока или задания — перекрасить Д/З.
  window.addEventListener('netschool:hwdone', () => { if (dayData(state.date)) renderDay(true); });
  $('weekStrip').addEventListener('click', (event) => {
    const button = event.target.closest('[data-week-day]');
    if (button && button.dataset.weekDay !== state.date) slideTo(button.dataset.weekDay);
  });
  bindSwipe();
}
