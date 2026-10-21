// Нижняя панель на своём месте после смены размера окна.
//
// На iPhone WebKit после изменения окна — Telegram разворачивает или
// сворачивает мини-приложение, переходит в полный экран, приложение
// возвращается из фона — иногда оставляет position: fixed на прежней высоте
// окна. Панель вкладок, кнопка ИИ-помощника и размытие под панелью
// повисали посередине экрана, а под ними был виден дневник. Прокрутка на
// пиксель туда и обратно и смена их position на одну раскладку (html.repin
// в app.css) заставляют WebKit пересчитать их положение. Одной прокрутки мало:
// короткая вкладка ровно в высоту окна не прокручивается.
//
// Бывает, что окно меняется без единого события — панель уезжает прямо во
// время прокрутки. Поэтому, когда прокрутка останавливается, сверяем низ
// панели с низом экрана и, если она съехала, ставим её на место.
'use strict';

import { tg } from './core.js';

let frame = 0;
let later = 0;

function nudge() {
  const y = window.scrollY;
  // В самом низу страницы вниз уже некуда — сдвигаемся вверх.
  window.scrollTo(window.scrollX, y > 0 ? y - 1 : y + 1);
  window.scrollTo(window.scrollX, y);
  const root = document.documentElement;
  root.classList.add('repin');
  void root.offsetHeight;
  root.classList.remove('repin');
}

/** Пересчитать fixed-элементы сейчас и ещё раз, когда доиграет анимация окна. */
export function repinFixed() {
  cancelAnimationFrame(frame);
  clearTimeout(later);
  frame = requestAnimationFrame(nudge);
  later = setTimeout(nudge, 400);
}

/** Панель стоит не у нижнего края экрана: WebKit держит её по старому окну. */
function misplaced() {
  const nav = document.querySelector('.bottom-nav');
  const view = window.visualViewport;
  // С клавиатурой или увеличением панель законно не у края видимой области.
  if (!nav || (view && view.scale > 1.01) || document.activeElement?.matches('input, textarea, [contenteditable]')) return false;
  const rect = nav.getBoundingClientRect();
  if (!rect.height) return false;
  const gap = parseFloat(getComputedStyle(nav).bottom) || 0;
  const height = view?.height || window.innerHeight;
  return Math.abs(height - gap - rect.bottom) > 2;
}

let settle = 0;
let fixedAt = 0;

function checkAfterScroll() {
  clearTimeout(settle);
  settle = setTimeout(() => {
    // Прокрутка из nudge тоже приходит сюда — не зацикливаемся, если
    // панель не встала на место с первого раза.
    if (Date.now() - fixedAt < 2000 || !misplaced()) return;
    fixedAt = Date.now();
    repinFixed();
  }, 150);
}

export function initRepin() {
  window.addEventListener('scroll', checkAfterScroll, { passive: true });
  window.addEventListener('resize', repinFixed);
  window.visualViewport?.addEventListener('resize', repinFixed);
  window.addEventListener('pageshow', repinFixed);
  window.addEventListener('orientationchange', repinFixed);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') repinFixed();
  });
  if (!tg?.onEvent) return;
  tg.onEvent('viewportChanged', (event) => { if (!event || event.isStateStable) repinFixed(); });
  for (const name of ['activated', 'fullscreenChanged', 'safeAreaChanged', 'contentSafeAreaChanged']) {
    tg.onEvent(name, repinFixed);
  }
}
