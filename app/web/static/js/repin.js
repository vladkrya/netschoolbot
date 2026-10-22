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
//
// Пересчёт помогает не всегда: бывает, WebKit так и держит панель по
// старому окну, а visualViewport врёт с ним заодно — панель висела посреди
// экрана, и проверка этого не видела. Поэтому низ экрана берём по самой
// большой из известных высот (включая ту, что сообщает сам Telegram), а
// если панель всё равно не у края — сдвигаем её на разницу (--pin-shift).
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
  frame = requestAnimationFrame(() => { nudge(); pin(); });
  later = setTimeout(() => { nudge(); pin(); }, 400);
}

/** Высота экрана. WebKit со старым окном занижает часть своих чисел, но
 *  не все сразу, а Telegram сообщает высоту окна сам. Больше экрана
 *  телефона окно не бывает — этим отсекаем неправдоподобное. */
function screenHeight() {
  const view = window.visualViewport;
  const heights = [window.innerHeight, document.documentElement.clientHeight, view?.height];
  // Только iPhone: там и бывает старое окно, а другие клиенты считают высоту иначе.
  if (tg?.platform === 'ios' && tg.isExpanded && tg.viewportStableHeight > 0) heights.push(tg.viewportStableHeight);
  const limit = Math.max(window.screen?.height || 0, window.screen?.width || 0) || Infinity;
  return Math.max(0, ...heights.filter((h) => h > 0 && h <= limit + 1));
}

/** На сколько панель не дотягивает до нижнего края экрана без поправки.
 *  null — когда мерить не к месту: клавиатура, увеличение, панели нет. */
function drift() {
  const nav = document.querySelector('.bottom-nav');
  const view = window.visualViewport;
  // С клавиатурой или увеличением панель законно не у края видимой области.
  if (!nav || (view && view.scale > 1.01) || document.activeElement?.matches('input, textarea, [contenteditable]')) return null;
  const rect = nav.getBoundingClientRect();
  if (!rect.height) return null;
  const style = getComputedStyle(nav);
  // Панель сбоку (широкий экран) — тянется во всю высоту, мерить нечего.
  if (style.top !== 'auto' && style.bottom !== 'auto' && rect.height > 200) return null;
  const gap = parseFloat(style.bottom) || 0;
  const shift = parseFloat(document.documentElement.style.getPropertyValue('--pin-shift')) || 0;
  const diff = screenHeight() - gap - (rect.bottom - shift);
  return Math.abs(diff) > 2 ? Math.round(diff) : 0;
}

/** Довести панель и всё, что при ней, до нижнего края (app.css: --pin-shift).
 *  true — если она съехала с прошлой проверки. */
function pin() {
  const shift = drift();
  const root = document.documentElement;
  const was = parseFloat(root.style.getPropertyValue('--pin-shift')) || 0;
  if (shift === null || Math.abs(shift - was) <= 2) return false;
  if (shift) root.style.setProperty('--pin-shift', `${shift}px`);
  else root.style.removeProperty('--pin-shift');
  return true;
}

let settle = 0;
let fixedAt = 0;

function checkAfterScroll() {
  clearTimeout(settle);
  settle = setTimeout(() => {
    if (!pin()) return;
    // Съехала — поправили сдвигом и ещё просим WebKit переставить по-честному. Прокрутка из
    // nudge тоже приходит сюда — не зацикливаемся.
    if (Date.now() - fixedAt < 2000) return;
    fixedAt = Date.now();
    repinFixed();
  }, 150);
}

export function initRepin() {
  repinFixed();
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
