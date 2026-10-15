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
 *  («назад»). null — жесту здесь нечего делать. */
function actionFor(step, target) {
  if (document.documentElement.classList.contains('app-locked')) return null;
  const overlay = topScreen() || !$('sheet').hidden || $('drawer').classList.contains('open');
  if (!overlay && state.tab === 'day') {
    if (target.closest('#weekStrip')) return () => pageWeek(step);
    if (target.closest('#panel-day, #appHeader')) return () => shiftDay(step);
  }
  if (step < 0 && canGoBack()) return handleBack;
  return null;
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
  window.addEventListener('wheel', (event) => {
    // Щипок трекпадом приходит как wheel с ctrlKey — это масштаб.
    if (event.ctrlKey || event.deltaMode !== 0) return;
    const now = performance.now();
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
    if (gesture.done || Math.abs(x) < COMMIT_PX) return;
    gesture.done = true;
    gesture.firedAt = now;
    action();
  }, { passive: false });
}
