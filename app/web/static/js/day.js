// «День»: уроки выбранного дня, как на главном экране официального приложения.
//
// Заголовок «Вчера / Сегодня / Завтра» со стрелками, календарь по нажатию на
// дату, листание дней свайпом, недель — свайпом по полосе недели. Карточка урока: номер и предмет, оценка в
// рамке, бейдж «Д/З» (сразу открывает задание), время и статус урока.
//
// На широком экране компьютера день в две колонки: уроки слева, справа —
// кнопки соседних дней, таймер, сводка и отсчёт. Дни листаются и стрелками
// ← → на клавиатуре.
'use strict';

import {
  $, addDays, api, dayMonth, errorCard, esc, fullDate, isoOf, isoToday, keepTouch, lessonNo, mondayOf,
  parseIso, plural, showFailure, showLoading, skeleton, state, WEEKDAYS, WEEKDAYS_FULL, weekdayIndex, weekOffsetOf, stillMotion,
} from './core.js';
import { ICONS } from './icons.js';
import { isNewDesign, saveBells, showBells, showHomeworkStatus, showWeekends } from './appearance.js';
import { bellsHtml, bellsNoteHtml, loadSchoolBells, weekCardsHtml } from './schedule.js';
import { canGoBack, topScreen } from './screens.js';
import { EDGE_PX, swipeBackMode } from './swipeback.js';
import { isDoneItem, lessonStatus, loadDone, markBox, openHomework, openLesson, realMarks } from './details.js';
import { closeSheet, openSheet, sheetOpen } from './sheet.js';
import { select, tap } from './haptics.js';
import { bindInstallCard, installCardHtml } from './install.js';
import { updateTimer } from './timer.js';
import { isTestWork, loadNotes, noteWhen, openNoteEditor, setNoteDone } from './notes.js';
import { applyEdits, bindHidden, hiddenHtml, openAddLesson } from './edits.js';
import { flyTo, markKey, playFresh, takeFresh } from './motion.js';
import { bindCountdown, countdownHtml, loadCountdown } from './countdown.js';
import { bindShareWeek, shareWeekButton } from './schedimage.js';

/** Загруженные дни по ISO-дате и недели, которые уже спрашивали. */
const days = new Map();
/** Те же дни, как их отдал дневник, — без своих правок (edits.js). */
const schoolDays = new Map();
const loadedWeeks = new Set();
const pending = new Map();

// Ответ фоновой сверки мог прийти до загрузки этого модуля или после неё.
window.addEventListener('netschool:diaryfresh', event => {
  for (const day of event.detail.data.days || []) keepDay(day);
  if (state.date && state.tab === 'day') renderDay();
});

// Оценки дня — чтобы заметить, что дневник принёс новую или изменённую.
const marksOf = (day) => JSON.stringify([day.marks, (day.lessons || []).map((lesson) => lesson.marks)]);

function keepDay(day) {
  const known = schoolDays.get(day.date);
  // «Оценки» загружены раньше и держатся в памяти — пусть переспросят школу.
  if (known && marksOf(known) !== marksOf(day)) window.dispatchEvent(new CustomEvent('netschool:marksoutdated'));
  schoolDays.set(day.date, day);
  days.set(day.date, applyEdits(day));
}

// Правки расписания поменялись (здесь или пришли с другого устройства).
window.addEventListener('netschool:edits', () => {
  for (const day of schoolDays.values()) days.set(day.date, applyEdits(day));
  if (state.date && state.tab === 'day') renderDay();
  window.dispatchEvent(new CustomEvent('netschool:daysedited'));
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
    for (const day of data.days || []) keepDay(day);
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
      for (const day of data.days || []) keepDay(day);
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
  // Тот же выбор дня, что и после загрузки: раньше на время запроса экран
  // прыгал на «сегодня» — и при обновлении в выходной появлялись спрятанные
  // суббота и воскресенье, пока школа не ответит.
  if (!state.date || autoDay) state.date = pickDay();
  renderHeader();
  const container = $('dayContent');
  if (force || !loadedWeeks.has(weekOffsetOf(state.date))) {
    showLoading(container, skeleton());
    placeSide();
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
        placeSide();
        await ensureWeek(state.date, force);
      }
    }
  } catch (error) {
    showFailure(container, error, errorCard(error, 'dayRetry'));
    placeSide();
    $('dayRetry')?.addEventListener('click', () => loadDay(true));
    return;
  }
  renderDay();
  prefetchAround();
  // Отметки «выполнено» — для подсветки Д/З; день рисуется, не дожидаясь их.
  if (showHomeworkStatus() && !state.hwDone) loadDone().then(() => { if (state.tab === 'day') renderDay(true); });
  if (!state.notes) loadNotes().then(() => { if (state.tab === 'day') renderDay(true); });
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
  $('sidePrevLabel').textContent = shortLabel(neighbour(-1));
  $('sideNextLabel').textContent = shortLabel(neighbour(1));
  renderWeekStrip();
}

/** Подпись соседнего дня на кнопке: «Завтра» или «Ср, 14». */
function shortLabel(iso) {
  const label = relativeLabel(iso);
  return WEEKDAYS_FULL.includes(label) ? `${WEEKDAYS[weekdayIndex(iso)]}, ${parseIso(iso).getDate()}` : label;
}

/* ───────────────────── Две колонки на компьютере ───────────────────── */

// Ширина, с которой справа от уроков есть место для колонки (wide.css).
const wideDay = matchMedia('(min-width: 1200px)');

/** Таймер, сводку и отсчёт — в колонку справа или обратно над уроками.
 *  Сводку и отсчёт день рисует в общем списке, а здесь их переносят целиком,
 *  вместе с обработчиками. */
function placeSide() {
  const content = $('dayContent');
  const slot = $('daySideSlot');
  const timer = $('lessonTimer');
  const moved = content.querySelectorAll(':scope > .day-summary, :scope > .countdown-card');
  if (wideDay.matches) {
    if (!timer.closest('#daySide')) slot.before(timer);
    // Старые сводка и отсчёт остались от прошлой отрисовки — заменить.
    if (moved.length || !content.querySelector(':scope > .lesson-card, :scope > .empty')) slot.replaceChildren(...moved);
    return;
  }
  if (timer.closest('#daySide')) content.before(timer);
  const back = [...slot.children];
  if (!back.length) return;
  if (moved.length || !content.children.length) { slot.replaceChildren(); return; }
  const install = content.querySelector(':scope > .install-card');
  if (install) install.after(...back);
  else content.prepend(...back);
}

/** Неделя полосой под датой (новый дизайн): день недели и число. Все семь
 *  дней всегда — иначе воскресенье появлялось и строка перестраивалась. */
/** Суббота или воскресенье без уроков, а выходные показывать не просили. */
export function isHiddenWeekend(iso) {
  return weekdayIndex(iso) >= 5 && !showWeekends() && !days.get(iso)?.lessons?.length;
}

// Полосу нельзя перерисовывать под пальцем: кнопка, на которой начался
// жест, пропадает из документа, и остаток жеста (touchend) до полосы уже не
// доходит — она сдвигалась и не листалась. Пока идёт свайп, перерисовка
// откладывается; одинаковая разметка не перерисовывается вовсе.
let stripTouched = 0;
let stripPending = false;

function renderWeekStrip() {
  // Если конец жеста всё же потерялся, через пару секунд полоса снова живая.
  if (stripTouched && performance.now() - stripTouched < 2000) {
    stripPending = true;
    return;
  }
  stripPending = false;
  const monday = mondayOf(state.date);
  const today = isoToday();
  const dates = Array.from({ length: 7 }, (_, index) => addDays(monday, index))
    .filter((iso) => iso === state.date || !isHiddenWeekend(iso));
  const html = dates.map((iso) => {
    const classes = ['week-day', iso === state.date ? 'on' : '', iso === today ? 'today' : '',
      days.get(iso)?.lessons?.length ? '' : 'free'].filter(Boolean).join(' ');
    return `<button class="${classes}" data-week-day="${iso}">
        <span class="wd-name">${WEEKDAYS[weekdayIndex(iso)]}</span>
        <span class="wd-num">${parseIso(iso).getDate()}</span>
      </button>`;
  }).join('');
  const strip = $('weekStrip');
  if (strip.dataset.html !== html) {
    strip.innerHTML = html;
    strip.dataset.html = html;
  }
}

function releaseStrip() {
  stripTouched = 0;
  if (stripPending) renderWeekStrip();
}

/** Сводка дня над уроками (новый дизайн): сколько уроков, с какого по какое
 *  время, сколько заданий и какие оценки. */
/** Оценки дня, которые человек ещё не видел: вспыхнут при отрисовке. */
let freshMarks = new Set();

function freshClass(mark, subject) {
  return freshMarks.has(markKey(mark, subject, state.date)) ? 'is-fresh' : '';
}

function daySummary(lessons) {
  const first = lessons.find((lesson) => lesson.start)?.start;
  const last = [...lessons].reverse().find((lesson) => lesson.end)?.end;
  const homework = lessons.filter((lesson) => lesson.homework.some((item) => !item.classwork)).length;
  // Оценка — с коэффициентом, как в карточке урока; нажатие открывает урок.
  const marks = lessons.flatMap((lesson, index) => realMarks(lesson.marks).map((m, nth) =>
    `<button class="ds-mark" data-summary-lesson="${index}" data-summary-mark="${nth}" aria-label="${esc(lesson.subject)}: ${esc(m.mark)}">${markBox(m.mark, freshClass(m, lesson.subject), m.weight || 1)}</button>`)).join('');
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
 *  что-то поменялось (статус урока), иначе экран «перезагружался» каждую
 *  минуту. */
function renderDay(quiet = false) {
  renderHeader();
  const day = days.get(state.date);
  const lessons = day?.lessons || [];
  const container = $('dayContent');

  updateTimer(lessons, state.date === isoToday());
  loadCountdown(() => renderDay(true));
  if (!lessons.length) {
    renderedKey = '';
    container.innerHTML = installCardHtml() + countdownHtml()
      + `<div class="empty big">${weekdayIndex(state.date) >= 5 ? 'Выходной' : 'Уроков нет'}</div>`
      + hiddenHtml(day) + addLessonButton();
    bindInstallCard(container);
    bindCountdown(container, () => renderDay(true));
    bindDayEdits(container, day);
    placeSide();
    return;
  }

  // Статусы («Идёт урок», «Урок окончен») меняются по времени — тихая
  // перерисовка по таймеру должна их подхватить.
  const statuses = lessons.map((lesson) => lessonStatus(lesson, state.date)).join();
  const notes = (state.notes || []).filter((note) => note.date === state.date);
  const key = `${state.date}|${countdownHtml().length}|${statuses}|${state.hwDone?.size ?? '-'}|${notes.map((n) => `${n.id}${n.done}${n.time}${n.remind}${n.text}${n.subject}`).join()}|${editsKey(day)}`;
  if (quiet && key === renderedKey && container.children.length) return;
  renderedKey = key;
  freshMarks = takeFresh(lessons.flatMap((lesson) => realMarks(lesson.marks)
    .map((m) => ({ key: markKey(m, lesson.subject, state.date), date: m.date || state.date }))));
  container.classList.toggle('quiet', quiet);
  container.innerHTML = installCardHtml() + daySummary(lessons) + countdownHtml()
    + lessons.map((lesson, index) => lessonCard(lesson, index)).join('')
    + notesBlock(notes) + hiddenHtml(day) + addLessonButton();
  bindInstallCard(container);
  bindCountdown(container, () => renderDay(true));
  bindDayEdits(container, day);
  playFresh(container);
  container.querySelectorAll('[data-note]').forEach((row) => row.addEventListener('click', async (event) => {
    const note = notes[Number(row.dataset.note)];
    if (event.target.closest('[data-note-edit]')) {
      const subjects = [...new Set(lessons.map((lesson) => lesson.subject).filter(Boolean))];
      openNoteEditor(subjects, () => renderDay(true), { note });
      return;
    }
    const next = !note.done;
    row.classList.toggle('done', next);
    if (!await setNoteDone(note, next)) row.classList.toggle('done', !next);
  }));
  container.querySelectorAll('[data-summary-lesson]').forEach((button) => button.addEventListener('click', () => {
    tap();
    const from = button.querySelector('.mark-box')?.getBoundingClientRect();
    openLesson(lessons[Number(button.dataset.summaryLesson)], state.date);
    // Оценка перелетает из сводки в шапку открывшегося урока.
    flyTo(from, lessonMarks()[Number(button.dataset.summaryMark)]);
  }));
  container.querySelectorAll('[data-lesson]').forEach((card) => {
    card.addEventListener('click', (event) => {
      tap();
      const lesson = lessons[Number(card.dataset.lesson)];
      // Бейдж «Д/З» и строка задания открывают его сразу, минуя экран урока.
      if (event.target.closest('[data-hw-badge]') && lesson.homework.length) {
        openHomework(lesson.homework[0], lesson.topic);
        return;
      }
      // Оценки с карточки перелетают на открывшийся экран урока.
      const from = [...card.querySelectorAll('.lc-marks .mark-box')].map((box) => box.getBoundingClientRect());
      openLesson(lesson, state.date);
      const targets = lessonMarks();
      from.forEach((rect, nth) => flyTo(rect, targets[nth]));
    });
  });
  placeSide();
}

/** Стрелки ← → на клавиатуре листают дни, когда открыт «День» и поверх
 *  ничего нет. В полях ввода стрелки — для текста. */
function bindArrowKeys() {
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.defaultPrevented) return;
    if (state.tab !== 'day' || topScreen() || sheetOpen() || $('drawer').classList.contains('open')) return;
    if (document.documentElement.classList.contains('app-locked')) return;
    if (event.target.closest?.('input, textarea, select, [contenteditable]')) return;
    event.preventDefault();
    shiftDay(event.key === 'ArrowRight' ? 1 : -1);
  });
}

/** Оценки на только что открытом экране урока — в том же порядке, что на карточке. */
function lessonMarks() {
  return [...(topScreen()?.querySelectorAll('.task-mark .mark-box') || [])];
}

/** Правки дня меняют разметку — тихая перерисовка не должна их пропустить. */
function editsKey(day) {
  return day.lessons.map((lesson) => `${lesson.subject}@${lesson.time}`).join() + (day.hidden?.length || 0);
}

function addLessonButton() {
  return '<button class="btn outline add-lesson" data-add-lesson>+ Добавить урок</button>';
}

/** Предметы недели — подсказки в шторке «Добавить урок». */
function knownSubjects() {
  const monday = mondayOf(state.date);
  const names = new Set();
  for (let index = 0; index < 7; index += 1) {
    for (const lesson of schoolDays.get(addDays(monday, index))?.lessons || []) names.add(lesson.subject);
  }
  return [...names].sort((a, b) => a.localeCompare(b, 'ru'));
}

function bindDayEdits(container, day) {
  if (day) bindHidden(container, day);
  container.querySelector('[data-add-lesson]')?.addEventListener('click', () => openAddLesson(state.date, knownSubjects()));
}

/** Свои задачи на этот день — под уроками; нажатие отмечает сделанной. */
function notesBlock(notes) {
  if (!notes.length) return '';
  return `<div class="card day-notes"><div class="card-title">Свои задачи</div>${notes.map((note, index) =>
    `<div class="day-note ${note.done ? 'done' : ''}" role="button" tabindex="0" data-note="${index}"><span class="dn-box"><svg viewBox="0 0 14 14" aria-hidden="true"><path d="M2.5 7.5l3 3 6-7"/></svg></span>
      <span class="dn-body"><span class="dn-text">${note.subject ? `<b>${esc(note.subject)}:</b> ` : ''}${esc(note.text)}</span>${note.time ? `<span class="dn-when">${esc(noteWhen(note))}</span>` : ''}</span>
      <button class="note-edit" data-note-edit aria-label="Изменить задачу">${ICONS.pencil}</button></div>`).join('')}</div>`;
}

/** Цвет Д/З на главной: hw-todo — не сделано, hw-done — всё сделано.
 *  Пусто, если подсветку выключили или отметки ещё не загрузились. */
function homeworkStatus(homework) {
  if (!homework.length || !showHomeworkStatus() || !state.hwDone) return '';
  const items = homework.filter((item) => item.key || item.auto_done);
  if (!items.length) return '';
  return items.every((item) => isDoneItem(item, state.hwDone)) ? 'hw-done' : 'hw-todo';
}

function lessonCard(lesson, index) {
  // Новый дизайн: коэффициент маленькой цифрой у оценки, как в «Оценках».
  const marks = realMarks(lesson.marks).map((m) => markBox(m.mark, freshClass(m, lesson.subject), isNewDesign() ? m.weight || 1 : null)).join('');
  const hasFiles = lesson.homework.some((h) => (h.attachments || []).length);
  const hasComment = lesson.homework.some((h) => h.comment);
  const status = homeworkStatus(lesson.homework);
  const test = lesson.homework.find(isTestWork);
  const time = lesson.time || [lesson.start, lesson.end].filter(Boolean).join(' – ');
  // Новый дизайн: вместо бейджа «Д/З» — начало самого задания.
  const preview = isNewDesign()
    ? lesson.homework.map((item) => String(item.text || '').replace(/\s+/g, ' ').trim()).filter(Boolean).join(' · ')
    : '';
  return `<div class="lesson-card" data-lesson="${index}">
      <div class="lc-top">
        <span class="lc-num">${esc(lessonNo(lesson))}</span>
        <span class="lc-subject">${esc(lesson.subject)}</span>
        <span class="lc-marks">${marks}</span>
      </div>
      ${lesson.is_elective || lesson.addedBy || lesson.editedBy ? `<div class="lc-badges">
        ${lesson.is_elective ? '<span class="elective-badge">Внеурочка</span>' : ''}
        ${lesson.addedBy ? '<span class="elective-badge edit-badge">Свой урок</span>' : ''}
        ${lesson.editedBy ? '<span class="elective-badge edit-badge">Своё время</span>' : ''}
      </div>` : ''}
      ${test ? `<div class="lc-badges"><span class="lc-test">📝 ${esc(test.type || 'Проверочная')}</span></div>` : ''}
      ${lesson.homework.length && !preview ? `<div class="lc-badges">
        <span class="hw-badge ${status}" data-hw-badge>Д/З</span>
        ${hasFiles ? `<span class="hw-badge clip">${ICONS.clip}</span>` : ''}
        ${hasComment ? `<span class="hw-badge clip" title="Есть комментарий к заданию">${ICONS.chat}</span>` : ''}
      </div>` : ''}
      ${preview ? `<div class="lc-hw ${status}">${hasFiles ? ICONS.clip : ''}${hasComment ? ICONS.chat : ''}<span>${esc(preview)}</span></div>` : ''}
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
      ${bells ? `${bellsNoteHtml()}
        <div class="week-sheet">${bellsHtml(monday)}</div>`
        : `<div class="week-sheet">${weekCardsHtml(monday)}</div>${shareWeekButton(monday)}`}`;
    bindShareWeek(body);
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
  // Звонки школы приходят позже уроков — перерисовываем открытую шторку.
  loadSchoolBells().then(() => { if (body.isConnected && showBells()) draw(); });
}

/** День, на котором были до перезапуска на новую версию (update.js). */
export function restoreDay(iso) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso || '')) return;
  state.date = iso;
  autoDay = false;
}

export async function goToDay(iso) {
  // Человек выбрал день сам — дальше приложение его не переставляет.
  autoDay = false;
  state.date = iso;
  await loadDay();
}

/** Соседний день: стрелки перешагивают спрятанные выходные. */
function neighbour(delta) {
  let iso = addDays(state.date, delta);
  for (let step = 0; step < 2 && isHiddenWeekend(iso); step += 1) iso = addDays(iso, delta);
  return iso;
}

export function shiftDay(delta, felt = false) {
  slideTo(neighbour(delta), felt);
}

/** Перейти на день с листанием в его сторону. felt — отклик уже был (свайп). */
function slideTo(iso, felt = false) {
  if (!felt) select();
  // Таймер урока над днём едет вместе с уроками, в колонке справа — стоит.
  const timer = $('lessonTimer');
  for (const container of [$('dayContent'), ...(timer.closest('#daySide') ? [] : [timer])]) {
    container.classList.remove('slide-left', 'slide-right');
    // Перезапуск анимации: без чтения layout класс не срабатывает повторно.
    void container.offsetWidth;
    container.classList.add(iso > state.date ? 'slide-left' : 'slide-right');
  }
  goToDay(iso);
}

/* ─────────────────────────── Календарь ─────────────────────────── */

export function openCalendar(selectedDate = state.date, onSelect = goToDay, { week = false } = {}) {
  tap();
  const month = parseIso(selectedDate);
  month.setDate(1);

  const body = openSheet('', '');
  let busy = false;
  // Месяц листается со сдвигом: старая сетка уезжает, новая въезжает.
  const shift = (step) => {
    const grid = body.querySelector('.cal-grid');
    month.setMonth(month.getMonth() + step);
    if (reducedMotion() || !grid?.animate || busy) {
      render();
      return;
    }
    busy = true;
    const out = grid.animate([{ transform: 'none', opacity: 1 }, { transform: `translateX(${-step * 40}%)`, opacity: 0 }],
      { duration: 140, easing: 'ease-in' });
    out.onfinish = out.oncancel = () => {
      busy = false;
      render();
      body.querySelector('.cal-grid')?.animate([{ transform: `translateX(${step * 40}%)`, opacity: 0 }, { transform: 'none', opacity: 1 }],
        { duration: 240, easing: 'cubic-bezier(.2, .8, .3, 1)' });
    };
  };
  // Выбранная дата «перетекает» кружком к новой, потом шторка закрывается.
  const choose = (button) => {
    const old = body.querySelector('.cal-day.selected');
    const done = () => {
      closeSheet();
      onSelect(button.dataset.date);
    };
    if (week || !old || old === button || reducedMotion()) {
      done();
      return;
    }
    // Кружок — под числами сетки, чтобы не закрывал даты на пути.
    const grid = button.parentElement;
    const from = { left: old.offsetLeft, top: old.offsetTop };
    const to = { left: button.offsetLeft, top: button.offsetTop };
    const dot = document.createElement('span');
    dot.className = 'cal-dot';
    Object.assign(dot.style, { left: `${to.left}px`, top: `${to.top}px`, width: `${button.offsetWidth}px`, height: `${button.offsetHeight}px`,
      background: getComputedStyle(old).backgroundColor });
    grid.append(dot);
    old.classList.remove('selected');
    const move = dot.animate([{ transform: `translate(${from.left - to.left}px, ${from.top - to.top}px)` }, { transform: 'none' }],
      { duration: 260, easing: 'cubic-bezier(.34, 1.4, .64, 1)' });
    move.onfinish = move.oncancel = () => {
      button.classList.add('selected');
      dot.remove();
      setTimeout(done, 90);
    };
  };
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
        shift(Number(button.dataset.shift));
      })
    );
    body.querySelectorAll('[data-date]').forEach((button) =>
      button.addEventListener('click', () => {
        select();
        choose(button);
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
  const content = $('dayContent');
  // Таймер урока над днём едет за пальцем вместе с уроками.
  const moving = () => [content, $('lessonTimer')];
  const setStyle = (props) => moving().forEach((el) => Object.assign(el.style, props));
  let gesture = null;
  // Горизонтальный жест заметно длиннее вертикального — иначе это прокрутка.
  const isSwipe = (dx, dy) => Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5;
  area.addEventListener('touchstart', (event) => {
    if (event.touches.length !== 1) return;
    const touch = event.touches[0];
    // От самого края — жест «назад», если есть куда, а не листание дней.
    if (touch.clientX <= EDGE_PX && canGoBack() && swipeBackMode() !== 'off') return;
    gesture = { x: touch.clientX, y: touch.clientY, dx: 0, dy: 0, horizontal: null, armed: false, lastX: touch.clientX, lastT: performance.now(), speed: 0 };
    // Список могут перерисовать посреди жеста (минутный таймер).
    keepTouch(event.target, { move: onMove, end: finish });
  }, { passive: true });
  // День едет за пальцем; отклик — в момент, когда палец прошёл порог: как у
  // нативного листания, ещё до того, как его отпустили.
  const onMove = (event) => {
    if (!gesture) return;
    const touch = event.touches[0];
    const dx = touch.clientX - gesture.x;
    const dy = touch.clientY - gesture.y;
    if (gesture.horizontal === null) {
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
      gesture.horizontal = Math.abs(dx) > Math.abs(dy) * 1.2;
      if (!gesture.horizontal) {
        gesture = null;
        return;
      }
    }
    if (event.cancelable) event.preventDefault();
    const now = performance.now();
    if (now > gesture.lastT) gesture.speed = 0.6 * gesture.speed + 0.4 * ((touch.clientX - gesture.lastX) / (now - gesture.lastT));
    gesture.lastX = touch.clientX;
    gesture.lastT = now;
    gesture.dx = dx;
    gesture.dy = dy;
    const swipe = isSwipe(dx, dy);
    if (swipe && !gesture.armed) select();
    gesture.armed = swipe;
    if (!reducedMotion()) {
      setStyle({
        transition: 'none',
        transform: `translateX(${dx}px)`,
        opacity: String(1 - Math.min(0.5, Math.abs(dx) / (area.offsetWidth || 1))),
      });
    }
  };
  area.addEventListener('touchmove', onMove, { passive: false });
  const finish = () => {
    if (!gesture) return;
    const { dx, dy, speed, armed, horizontal } = gesture;
    gesture = null;
    if (!horizontal) return;
    const fast = Math.abs(dx) > 30 && Math.abs(speed) > 0.4 && Math.sign(speed) === Math.sign(dx);
    const reset = () => setStyle({ transition: '', transform: '', opacity: '' });
    if (!isSwipe(dx, dy) && !fast) {
      // Не дотянул — день пружинит на место.
      if (!reducedMotion() && dx) {
        moving().forEach((el) => el.animate([{ transform: `translateX(${dx}px)` }, { transform: 'none' }], { duration: 260, easing: 'cubic-bezier(.2, .8, .3, 1)' }));
      }
      reset();
      return;
    }
    if (!armed) select();
    const step = dx < 0 ? 1 : -1;
    if (reducedMotion()) {
      reset();
      shiftDay(step, true);
      return;
    }
    // Доезжает за край, и с другой стороны въезжает соседний день.
    const width = area.offsetWidth || 1;
    const frames = [
      { transform: `translateX(${dx}px)`, opacity: content.style.opacity || 1 },
      // Дальше по ходу пальца, а не к фиксированной точке: если палец увёл
      // день дальше неё, день дёргался обратно к центру.
      { transform: `translateX(${dx - step * width * 0.4}px)`, opacity: 0 },
    ];
    // fill: уехавшее не мигает обратно, пока не встал новый день.
    const outs = moving().map((el) => el.animate(frames, { duration: 140, easing: 'ease-in', fill: 'forwards' }));
    const out = outs[0];
    let gone = false;
    const go = () => {
      if (gone) return;
      gone = true;
      reset();
      shiftDay(step, true);
      // Новый день въезжает своей анимацией — уехавшее состояние больше не держим.
      outs.forEach((animation) => animation.cancel());
    };
    out.onfinish = go;
    out.oncancel = go;
  };
  area.addEventListener('touchend', finish, { passive: true });
  area.addEventListener('touchcancel', finish, { passive: true });
}

/** Полоса недели листается неделями, как в календаре iPhone: ведёшь пальцем
 *  по числам — полоса едет за ним; отпустил достаточно далеко или резко —
 *  открывается тот же день недели соседней недели. */
const reducedMotion = stillMotion;

/** Неделя вперёд (step = 1) или назад (−1), тот же день недели. Полоса
 *  уезжает за край — от dx, где её отпустил палец, — и новая неделя
 *  въезжает с другой стороны. Общее для свайпа и стрелок. */
export function pageWeek(step, dx = 0) {
  const strip = $('weekStrip');
  const width = strip.offsetWidth || 1;
  select();
  strip.getAnimations().forEach((animation) => animation.cancel());
  const out = reducedMotion() ? null : strip.animate(
    [{ transform: `translateX(${dx}px)` }, { transform: `translateX(${-step * width}px)`, opacity: 0 }],
    { duration: 140, easing: 'ease-in' });
  const go = () => {
    slideTo(addDays(state.date, step * 7), true);
    if (!reducedMotion()) {
      strip.animate([{ transform: `translateX(${step * width * 0.6}px)`, opacity: 0 }, { transform: 'none', opacity: 1 }],
        { duration: 220, easing: 'cubic-bezier(.2,.8,.3,1)' });
    }
  };
  if (out) out.finished.then(go, go);
  else go();
}

function bindWeekSwipe() {
  const strip = $('weekStrip');
  let gesture = null;
  const reduced = reducedMotion;
  strip.addEventListener('touchstart', (event) => {
    if (event.touches.length !== 1) return;
    const touch = event.touches[0];
    // От самого края — жест «назад», если есть куда.
    if (touch.clientX <= EDGE_PX && canGoBack() && swipeBackMode() !== 'off') return;
    strip.getAnimations().forEach((animation) => animation.cancel());
    const now = performance.now();
    stripTouched = now;
    gesture = { x: touch.clientX, y: touch.clientY, dx: 0, horizontal: null, lastX: touch.clientX, lastT: now, speed: 0 };
  }, { passive: true });
  strip.addEventListener('touchmove', (event) => {
    if (!gesture) return;
    const touch = event.touches[0];
    const dx = touch.clientX - gesture.x;
    const dy = touch.clientY - gesture.y;
    if (gesture.horizontal === null) {
      // Пока направление не ясно, а палец идёт вбок — не отдаём жест
      // прокрутке: иначе iOS забирает его себе с первого же движения.
      if (Math.abs(dx) >= Math.abs(dy) && event.cancelable) event.preventDefault();
      if (Math.abs(dx) < 6 && Math.abs(dy) < 6) return;
      gesture.horizontal = Math.abs(dx) > Math.abs(dy);
    }
    if (!gesture.horizontal) return;
    if (event.cancelable) event.preventDefault();
    // Скорость — по последним движениям, а не от начала: медленный старт
    // и резкий рывок в конце — тоже листание.
    const now = performance.now();
    if (now > gesture.lastT) {
      gesture.speed = 0.6 * gesture.speed + 0.4 * ((touch.clientX - gesture.lastX) / (now - gesture.lastT));
    }
    gesture.lastX = touch.clientX;
    gesture.lastT = now;
    gesture.dx = dx;
    strip.style.transition = 'none';
    strip.style.transform = `translateX(${dx}px)`;
  }, { passive: false });
  const finish = () => {
    if (!gesture) return;
    const { dx, speed, horizontal } = gesture;
    gesture = null;
    releaseStrip();
    if (!horizontal) return;
    const width = strip.offsetWidth || 1;
    // Рывок в ту же сторону, куда ведёт палец.
    const fast = Math.abs(dx) > 20 && Math.abs(speed) > 0.3 && Math.sign(speed) === Math.sign(dx);
    strip.style.transform = '';
    strip.style.transition = '';
    if (Math.abs(dx) < width / 5 && !fast) {
      // Не дотянул — полоса возвращается на место.
      if (!reduced()) strip.animate([{ transform: `translateX(${dx}px)` }, { transform: 'none' }], { duration: 220, easing: 'ease-out' });
      return;
    }
    pageWeek(dx < 0 ? 1 : -1, dx);
  };
  strip.addEventListener('touchend', finish, { passive: true });
  // Жест отняла система (Telegram, прокрутка) — решаем по пройденному
  // пути, как при отпускании, а не бросаем полосу сдвинутой.
  strip.addEventListener('touchcancel', finish, { passive: true });
}

export function initDay() {
  // Статусы уроков («Закончится через 3 мин», «Урок окончен») меняются ровно
  // на границе минуты — тогда же, когда таймер над днём доходит до нуля.
  // Раньше день перерисовывался раз в 30 секунд и отставал от таймера.
  const refreshOnMinute = () => {
    setTimeout(() => {
      if (state.tab === 'day' && dayData(state.date)) renderDay(true);
      refreshOnMinute();
    }, 60000 - Date.now() % 60000 + 50);
  };
  refreshOnMinute();
  $('dayPrev').addEventListener('click', () => shiftDay(-1));
  $('dayNext').addEventListener('click', () => shiftDay(1));
  $('sidePrev').addEventListener('click', () => shiftDay(-1));
  $('sideNext').addEventListener('click', () => shiftDay(1));
  wideDay.addEventListener('change', placeSide);
  bindArrowKeys();
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
  bindWeekSwipe();
  $('weekPrev').addEventListener('click', () => pageWeek(-1));
  $('weekNext').addEventListener('click', () => pageWeek(1));
}
