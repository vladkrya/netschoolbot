// Свайпы двумя пальцами по трекпаду (Mac, ноутбуки) — те же жесты, что
// пальцем на телефоне: на «Дне» влево-вправо листаются дни, по полосе недели —
// недели, а поверх экрана, шторки или меню свайп вправо — «назад», как в
// Safari.
//
// Трекпад не шлёт касаний, только события прокрутки (wheel) с deltaX. Один
// жест — серия событий без пауз, включая инерцию после того, как пальцы
// сняли; срабатывает он один раз, когда пройден порог. Вертикальная прокрутка
// остаётся обычной прокруткой.
'use strict';

import { $, state } from './core.js';
import { canGoBack, handleBack, topScreen } from './screens.js';
import { pageWeek, shiftDay } from './day.js';

// Пауза, после которой следующие события — уже новый жест.
const QUIET_MS = 160;
// Сколько пикселей прокрутки вбок нужно, чтобы жест сработал.
const COMMIT_PX = 80;
// Столько вертикали в начале — и жест считается прокруткой.
const VERTICAL_PX = 24;

/** Может ли элемент сам прокрутиться вбок в эту сторону — тогда жест его. */
function scrollsSideways(target, dx) {
  for (let el = target; el && el !== document.body; el = el.parentElement) {
    if (el.scrollWidth <= el.clientWidth) continue;
    const overflow = getComputedStyle(el).overflowX;
    if (overflow !== 'auto' && overflow !== 'scroll') continue;
    if (dx > 0 ? el.scrollLeft + el.clientWidth < el.scrollWidth - 1 : el.scrollLeft > 0) return true;
  }
  return false;
}

/** Что сделает жест: step = 1 — пальцы влево («дальше»), −1 — вправо
 *  («назад»). null — жесту здесь нечего делать. el — что едет за пальцами
 *  до срабатывания: так видно, что жест пойман. */
function actionFor(step, target) {
  if (document.documentElement.classList.contains('app-locked')) return null;
  const overlay = topScreen() || !$('sheet').hidden || $('drawer').classList.contains('open');
  if (!overlay && state.tab === 'day') {
    if (target.closest('#weekStrip')) return { el: $('weekStrip'), run: (dx) => pageWeek(step, dx) };
    if (target.closest('#panel-day, #appHeader')) return { el: $('dayContent'), run: () => shiftDay(step) };
  }
  if (step < 0 && canGoBack()) return { el: null, run: handleBack };
  return null;
}

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Сдвиг за пальцами: до порога с сопротивлением, чтобы день не «уезжал». */
function follow(el, x) {
  if (!el || reduced()) return 0;
  const shift = -Math.sign(x) * Math.min(Math.abs(x), COMMIT_PX) * 0.45;
  el.style.transition = 'none';
  el.style.transform = `translateX(${shift}px)`;
  return shift;
}

/** Жест не дотянул — вернуть на место пружинкой. */
function release(el) {
  if (!el?.style.transform) return;
  const from = el.style.transform;
  el.style.transform = '';
  el.style.transition = '';
  if (!reduced()) el.animate([{ transform: from }, { transform: 'none' }], { duration: 260, easing: 'cubic-bezier(.2, .8, .3, 1)' });
}

/** Новый свайп посреди инерции прошлого: хвост инерции только затухает,
 *  а пальцы снова на трекпаде — рывок вверх или движение в другую сторону.
 *  Без этого после одного свайпа следующий, сделанный сразу, сливался с
 *  хвостом и не срабатывал. */
export function startsAnew(gesture, dx, now) {
  if (!gesture.done || now - gesture.firedAt < 120) return false;
  const size = Math.abs(dx);
  if (Math.sign(dx) && Math.sign(dx) !== Math.sign(gesture.x) && size > 4) return true;
  // В ту же сторону — только когда инерция уже пошла на спад: иначе пальцы,
  // которые всё ещё ведут прежний свайп, листали бы второй раз.
  return gesture.decayed && size > 6 && size > gesture.prev * 1.6;
}

export function initTrackpad() {
  let gesture = null;
  let moved = null;
  let quiet = 0;
  window.addEventListener('wheel', (event) => {
    // Щипок трекпадом приходит как wheel с ctrlKey — это масштаб.
    if (event.ctrlKey || event.deltaMode !== 0) return;
    const now = performance.now();
    // Пальцы сняли, не дотянув, — сдвинутое возвращается на место.
    clearTimeout(quiet);
    quiet = setTimeout(() => {
      release(moved);
      moved = null;
    }, QUIET_MS);
    if (!gesture || now - gesture.last > QUIET_MS || startsAnew(gesture, event.deltaX, now)) {
      gesture = { x: 0, y: 0, last: now, done: false, vertical: false, prev: 0, peak: 0, decayed: false, firedAt: 0 };
    }
    const size = Math.abs(event.deltaX);
    gesture.last = now;
    gesture.prev = size;
    gesture.peak = Math.max(gesture.peak, size);
    if (gesture.done && size < gesture.peak * 0.35) gesture.decayed = true;
    if (gesture.vertical) return;
    gesture.x += event.deltaX;
    gesture.y += event.deltaY;
    const { x, y } = gesture;
    if (Math.abs(y) > VERTICAL_PX && Math.abs(y) > Math.abs(x)) {
      gesture.vertical = true;
      return;
    }
    if (Math.abs(x) < Math.abs(y) || !x) return;
    if (scrollsSideways(event.target, x)) {
      gesture.vertical = true;
      return;
    }
    const action = actionFor(x > 0 ? 1 : -1, event.target);
    if (!action) return;
    // Свой жест не отдаём браузеру: иначе Safari и Chrome листают историю.
    if (event.cancelable) event.preventDefault();
    if (gesture.done) return;
    if (Math.abs(x) < COMMIT_PX) {
      if (moved && moved !== action.el) release(moved);
      moved = action.el;
      follow(moved, x);
      return;
    }
    gesture.done = true;
    gesture.firedAt = now;
    const shift = moved ? Number.parseFloat((moved.style.transform || '').replace('translateX(', '')) || 0 : 0;
    if (moved) {
      moved.style.transform = '';
      moved.style.transition = '';
      moved = null;
    }
    action.run(shift);
  }, { passive: false });
}
