// Свайп «назад» от левого края экрана — везде, как жест «назад» в Android.
// Поверх экрана и на вкладках, кроме «Дня», — вправо с любого места. И
// зеркально: от правого края влево, удобно правой рукой.
//
// Если открыт экран поверх вкладки, он едет за пальцем, как в iOS. На
// вкладке, откуда «назад» ведёт на первую, за пальцем едет сама вкладка. И
// всегда у края вылетает кружок со стрелкой — видно, что это жест «назад».
// Отпустил достаточно далеко — то же, что кнопка «Назад»: закрыть экран,
// шторку, меню или вернуться на «День».
'use strict';

import { $, keepTouch, state } from './core.js';
import { ICONS } from './icons.js';
import { canGoBack, handleBack, topScreen } from './screens.js';
import { sheetOpen } from './sheet.js';
import { tap } from './haptics.js';

/** Ширина полосы у левого края, с которой начинается жест. */
export const EDGE_PX = 28;
// Насколько далеко увести палец, чтобы жест сработал.
const COMMIT_PX = 90;
const ARROW_MAX = 64;

let arrow = null;

function ensureArrow() {
  if (arrow) return arrow;
  arrow = document.createElement('div');
  arrow.className = 'back-arrow';
  arrow.ariaHidden = 'true';
  arrow.innerHTML = ICONS.back;
  document.body.append(arrow);
  return arrow;
}

/** Кружок «назад» у края: progress от 0 до 1 (1 — жест взведён), y —
 *  высота пальца, side — у какого края (жест от правого края — справа).
 *  Нужен и трекпаду (trackpad.js). */
export function showBackHint(progress, y = innerHeight / 2, side = 'left') {
  const el = ensureArrow();
  const armed = progress >= 1;
  el.classList.add('show');
  el.classList.toggle('armed', armed);
  el.classList.toggle('right', side === 'right');
  const shift = Math.min(progress, 1) * ARROW_MAX * 0.95 * (side === 'right' ? -1 : 1);
  el.style.transform = `translate(${shift}px, -50%) scale(${armed ? 1 : 0.8})`;
  el.style.top = `${y}px`;
}

export function hideBackHint() {
  if (!arrow) return;
  arrow.classList.remove('show', 'armed');
  arrow.style.transform = '';
}

/** Палец на ленте, которая сама листается вбок (кабинет и учитель, фильтры).
 *  Экран урока и вкладка сюда не входят: они листаются вниз, а браузер,
 *  раз прокрутка вниз включена, считает их прокручиваемыми и вбок — из-за
 *  этого свайп с середины экрана не срабатывал. */
function scrollsSideways(target) {
  for (let el = target; el && el !== document.body; el = el.parentElement) {
    if (el.matches('.screen, main, .panel, .sheet, .drawer')) return false;
    const style = getComputedStyle(el);
    // Лента вбок — это overflow-x без вертикальной прокрутки у того же элемента.
    if (el.scrollWidth > el.clientWidth + 1 && /auto|scroll/.test(style.overflowX) && !/auto|scroll/.test(style.overflowY)) return true;
  }
  return false;
}

export function initSwipeBack() {
  let gesture = null;

  document.addEventListener('touchstart', (event) => {
    if (gesture || event.touches.length !== 1) return;
    const touch = event.touches[0];
    if (!canGoBack()) return;
    const overlay = topScreen() || sheetOpen() || $('drawer').classList.contains('open');
    // От правого края влево — тоже «назад»: удобно правой рукой.
    const fromRight = touch.clientX >= innerWidth - EDGE_PX;
    // Вправо — с любого места поверх экрана и на вкладках: своих жестов вбок
    // там нет. На «Дне» — только от края, иначе спутается с листанием дней.
    // Панель вкладок тянут пальцем, в полях выделяют текст — там жест свой.
    const ownGesture = event.target.closest?.('#bottomNav, input, textarea, select, [contenteditable]');
    const anywhere = (overlay || state.tab !== 'day') && !ownGesture && !scrollsSideways(event.target);
    if (touch.clientX > EDGE_PX && !anywhere && !(fromRight && !ownGesture)) return;
    gesture = {
      x: touch.clientX, y: touch.clientY, dx: 0,
      // Направление жеста «назад»: вправо (1) или от правого края влево (−1).
      // С правого края годится и то и другое — решает первое движение.
      fromRight, dir: 0,
      // Направление ещё не ясно: вертикальное движение — обычная прокрутка.
      decided: false, armed: false,
      screen: topScreen(),
      // Вкладка (main) едет за пальцем, только если «назад» — это смена
      // вкладки, а не закрытие шторки или меню.
      page: null,
    };
    if (!gesture.screen && !sheetOpen() && !$('drawer').classList.contains('open')) {
      gesture.page = document.querySelector('main');
    }
    keepTouch(event.target, { move: onMove, end: () => finish(true), cancel: () => finish(false) });
  }, { passive: true });

  function onMove(event) {
    if (!gesture) return;
    const touch = event.touches[0];
    const raw = touch.clientX - gesture.x;
    const dy = touch.clientY - gesture.y;
    if (!gesture.decided) {
      if (Math.abs(raw) < 8 && Math.abs(dy) < 8) return;
      if (Math.abs(dy) > Math.abs(raw)) {
        gesture = null;
        return;
      }
      gesture.dir = raw > 0 ? 1 : -1;
      // Влево — только от правого края; вправо от правого края на «Дне» не
      // начинали (см. touchstart), так что на «Дне» влево — листание дня.
      if (gesture.dir < 0 && !gesture.fromRight) {
        gesture = null;
        return;
      }
      if (gesture.dir > 0 && gesture.fromRight && state.tab === 'day' && !gesture.screen && !sheetOpen() && !$('drawer').classList.contains('open')) {
        gesture = null;
        return;
      }
      gesture.decided = true;
      // От правого края экран не едет за пальцем — только кружок справа.
      if (gesture.dir < 0) {
        gesture.screen = null;
        gesture.page = null;
      }
      const moving = gesture.screen || gesture.page;
      if (moving) moving.classList.add('swiping');
    }
    const dx = Math.max(0, raw * gesture.dir);
    if (event.cancelable) event.preventDefault();
    gesture.dx = dx;
    const armed = dx > COMMIT_PX;
    // Щелчок в момент, когда жест «взвёлся», — палец чувствует порог.
    if (armed && !gesture.armed) tap('light');
    gesture.armed = armed;
    if (gesture.screen || gesture.page) {
      (gesture.screen || gesture.page).style.transform = `translateX(${dx}px)`;
    }
    showBackHint(dx / COMMIT_PX, touch.clientY, gesture.dir < 0 ? 'right' : 'left');
  }
  document.addEventListener('touchmove', onMove, { passive: false });

  function finish(commit) {
    if (!gesture) return;
    const { screen, page, decided, armed } = gesture;
    gesture = null;
    if (!decided) return;
    const go = commit && armed;
    hideBackHint();
    if (screen) {
      screen.classList.remove('swiping');
      if (go) {
        // Экран доезжает вправо и только потом закрывается.
        screen.classList.add('leaving');
        screen.style.transform = 'translateX(100%)';
        let done = false;
        const close = () => {
          if (done) return;
          done = true;
          handleBack();
        };
        screen.addEventListener('transitionend', close, { once: true });
        setTimeout(close, 300);
      } else {
        screen.style.transform = '';
      }
    } else if (page) {
      page.classList.remove('swiping');
      if (go) {
        // Вкладка уезжает вправо, а первая въезжает слева — как «назад» в iOS.
        page.classList.add('leaving');
        page.style.transform = 'translateX(100%)';
        setTimeout(() => {
          page.classList.remove('leaving');
          handleBack();
          page.style.transform = 'translateX(-30%)';
          page.getBoundingClientRect();
          page.classList.add('leaving');
          page.style.transform = '';
          setTimeout(() => page.classList.remove('leaving'), 260);
        }, 200);
      } else {
        page.classList.add('leaving');
        page.style.transform = '';
        setTimeout(() => page.classList.remove('leaving'), 260);
      }
    } else if (go) {
      handleBack();
    }
  }
  document.addEventListener('touchend', () => finish(true), { passive: true });
  document.addEventListener('touchcancel', () => finish(false), { passive: true });
}
