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

export function initRepin() {
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
