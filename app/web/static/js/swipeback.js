// Свайп «назад» от левого края экрана — везде, как жест «назад» в Android.
//
// Если открыт экран поверх вкладки, он едет за пальцем, как в iOS. Иначе
// у края растёт кружок со стрелкой. Отпустил достаточно далеко — то же,
// что кнопка «Назад»: закрыть экран, шторку, меню или вернуться на «День».
'use strict';

import { ICONS } from './icons.js';
import { canGoBack, handleBack, topScreen } from './screens.js';
import { tap } from './haptics.js';

/** Ширина полосы у левого края, с которой начинается жест. */
export const EDGE_PX = 24;
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

export function initSwipeBack() {
  let gesture = null;

  document.addEventListener('touchstart', (event) => {
    if (gesture || event.touches.length !== 1) return;
    const touch = event.touches[0];
    if (touch.clientX > EDGE_PX || !canGoBack()) return;
    gesture = {
      x: touch.clientX, y: touch.clientY, dx: 0,
      // Направление ещё не ясно: вертикальное движение — обычная прокрутка.
      decided: false, armed: false,
      screen: topScreen(),
    };
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
      if (gesture.screen) gesture.screen.classList.add('swiping');
      else ensureArrow().classList.add('show');
    }
    if (event.cancelable) event.preventDefault();
    gesture.dx = dx;
    const armed = dx > COMMIT_PX;
    // Щелчок в момент, когда жест «взвёлся», — палец чувствует порог.
    if (armed && !gesture.armed) tap('light');
    gesture.armed = armed;
    if (gesture.screen) {
      gesture.screen.style.transform = `translateX(${dx}px)`;
    } else {
      const shift = Math.min(dx, ARROW_MAX * 1.6) * 0.6;
      arrow.style.transform = `translate(${shift}px, -50%) scale(${armed ? 1 : 0.8})`;
      arrow.style.top = `${touch.clientY}px`;
      arrow.classList.toggle('armed', armed);
    }
  }, { passive: false });

  const finish = (commit) => {
    if (!gesture) return;
    const { screen, decided, armed } = gesture;
    gesture = null;
    if (!decided) return;
    const go = commit && armed;
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
    } else {
      arrow.classList.remove('show', 'armed');
      arrow.style.transform = '';
      if (go) handleBack();
    }
  };
  document.addEventListener('touchend', () => finish(true), { passive: true });
  document.addEventListener('touchcancel', () => finish(false), { passive: true });
}
