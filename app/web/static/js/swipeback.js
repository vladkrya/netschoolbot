// Свайп «назад» от левого края экрана — везде, как жест «назад» в Android.
//
// Если открыт экран поверх вкладки, он едет за пальцем, как в iOS. На
// вкладке, откуда «назад» ведёт на первую, за пальцем едет сама вкладка. И
// всегда у края вылетает кружок со стрелкой — видно, что это жест «назад».
// Отпустил достаточно далеко — то же, что кнопка «Назад»: закрыть экран,
// шторку, меню или вернуться на «День».
'use strict';

import { $ } from './core.js';
import { ICONS } from './icons.js';
import { canGoBack, handleBack, topScreen } from './screens.js';
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

/** Кружок «назад» у левого края: progress от 0 до 1 (1 — жест взведён),
 *  y — высота пальца. Нужен и трекпаду (trackpad.js). */
export function showBackHint(progress, y = innerHeight / 2) {
  const el = ensureArrow();
  const armed = progress >= 1;
  el.classList.add('show');
  el.classList.toggle('armed', armed);
  const shift = Math.min(progress, 1) * ARROW_MAX * 0.95;
  el.style.transform = `translate(${shift}px, -50%) scale(${armed ? 1 : 0.8})`;
  el.style.top = `${y}px`;
}

export function hideBackHint() {
  if (!arrow) return;
  arrow.classList.remove('show', 'armed');
  arrow.style.transform = '';
}

/** Палец на ленте, которая сама листается вбок (кабинет и учитель, фильтры). */
function scrollsSideways(target) {
  for (let el = target; el && el !== document.body; el = el.parentElement) {
    if (el.scrollWidth > el.clientWidth + 1 && /auto|scroll/.test(getComputedStyle(el).overflowX)) return true;
  }
  return false;
}

export function initSwipeBack() {
  let gesture = null;

  document.addEventListener('touchstart', (event) => {
    if (gesture || event.touches.length !== 1) return;
    const touch = event.touches[0];
    if (!canGoBack()) return;
    // Поверх открытого экрана (урок, задание, письмо) свайп вправо — «назад»
    // с любого места, хоть от правого края: там нет своих жестов вбок. На
    // вкладках — только от левого края, иначе спутается с листанием дней.
    const anywhere = topScreen() && !scrollsSideways(event.target);
    if (touch.clientX > EDGE_PX && !anywhere) return;
    gesture = {
      x: touch.clientX, y: touch.clientY, dx: 0,
      // Направление ещё не ясно: вертикальное движение — обычная прокрутка.
      decided: false, armed: false,
      screen: topScreen(),
      // Вкладка (main) едет за пальцем, только если «назад» — это смена
      // вкладки, а не закрытие шторки или меню.
      page: null,
    };
    if (!gesture.screen && $('sheet').hidden && !$('drawer').classList.contains('open')) {
      gesture.page = document.querySelector('main');
    }
  }, { passive: true });

  document.addEventListener('touchmove', (event) => {
    if (!gesture) return;
    const touch = event.touches[0];
    const dx = Math.max(0, touch.clientX - gesture.x);
    const dy = touch.clientY - gesture.y;
    if (!gesture.decided) {
      if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
      if (Math.abs(dy) > Math.abs(dx)) {
        gesture = null;
        return;
      }
      gesture.decided = true;
      const moving = gesture.screen || gesture.page;
      if (moving) moving.classList.add('swiping');
    }
    if (event.cancelable) event.preventDefault();
    gesture.dx = dx;
    const armed = dx > COMMIT_PX;
    // Щелчок в момент, когда жест «взвёлся», — палец чувствует порог.
    if (armed && !gesture.armed) tap('light');
    gesture.armed = armed;
    if (gesture.screen || gesture.page) {
      (gesture.screen || gesture.page).style.transform = `translateX(${dx}px)`;
    }
    showBackHint(dx / COMMIT_PX, touch.clientY);
  }, { passive: false });

  const finish = (commit) => {
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
  };
  document.addEventListener('touchend', () => finish(true), { passive: true });
  document.addEventListener('touchcancel', () => finish(false), { passive: true });
}
