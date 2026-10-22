// Экраны поверх вкладок: урок, задание, оценка, предмет, письмо.
//
// Как в официальном приложении: экран открывается над вкладкой со стрелкой
// «назад», нижняя навигация остаётся на месте. Стрелку дублирует кнопка
// «Назад» Telegram — она закрывает верхний экран, а не всё приложение.
'use strict';

import { $, esc, tg, stillMotion } from './core.js';
import { tap } from './haptics.js';
import { ICONS } from './icons.js';

const stack = [];

/* ───────────── Возврат после перезапуска на новую версию ─────────────
 * Экран, который умеет открыться заново, помечает себя: что открыть и с
 * чем (update.js сохраняет стопку перед перезапуском и открывает её снова).
 * live() — свежие аргументы на момент сохранения (начатое письмо). */
const restoreOf = new WeakMap();

export function markScreen(body, kind, args = [], live = null) {
  const screen = body?.closest?.('.screen');
  if (screen) restoreOf.set(screen, { kind, args, live });
}

/** Стопка экранов: что открыто, прокрутка и поля — сверху последний. */
export function screenTrail() {
  return stack.map((screen) => {
    const mark = restoreOf.get(screen);
    let args = mark?.args;
    try { if (mark?.live) args = mark.live(); } catch { /* экран уже разобран */ }
    return {
      kind: mark?.kind || '',
      args,
      scroll: screen.scrollTop,
      fields: [...screen.querySelectorAll(FIELDS)].map((field) => (
        field.type === 'checkbox' || field.type === 'radio' ? { c: field.checked } : { v: field.value })),
    };
  });
}

const FIELDS = 'input:not([type="file"]):not([type="hidden"]), textarea, select';

/** Поля экрана — как были до перезапуска, по порядку. */
export function fillFields(screen, saved = []) {
  [...screen.querySelectorAll(FIELDS)].forEach((field, index) => {
    const value = saved[index];
    if (!value) return;
    if ('c' in value && (field.type === 'checkbox' || field.type === 'radio')) field.checked = value.c;
    else if ('v' in value && typeof value.v === 'string' && !field.value) field.value = value.v;
  });
}

// Состояние модулей, которое должно пережить перезапуск (прикидки «что если»).
const keepers = new Map();

export function keepAcrossRestart(name, save, load) {
  keepers.set(name, { save, load });
}

export function keptState() {
  const result = {};
  for (const [name, keeper] of keepers) {
    try { result[name] = keeper.save(); } catch { /* не сохранилось — откроется пустым */ }
  }
  return result;
}

export function restoreKept(saved = {}) {
  for (const [name, keeper] of keepers) {
    if (saved[name] === undefined) continue;
    try { keeper.load(saved[name]); } catch { /* старый формат — без него */ }
  }
}

export function pushScreen(title, subtitle = '') {
  tap();
  const screen = document.createElement('section');
  screen.className = 'screen';
  screen.innerHTML = `
    <header class="screen-head">
      <button class="screen-back" aria-label="Назад">${ICONS.back}</button>
      <div class="screen-titles">
        <div class="screen-title">${esc(title)}</div>
        ${subtitle ? `<div class="screen-subtitle">${esc(subtitle)}</div>` : ''}
      </div>
    </header>
    <div class="screen-body"></div>`;
  screen.querySelector('.screen-back').addEventListener('click', () => {
    tap('soft');
    popScreen();
  });
  $('screens').appendChild(screen);
  stack.push(screen);
  syncBackButton();
  return screen.querySelector('.screen-body');
}

export function popScreen() {
  const screen = stack.pop();
  if (screen) leave(screen);
  syncBackButton();
}

/** Экран уезжает вправо и тает — зеркально тому, как появился. Из стека он
 *  уже убран, так что повторное «назад» закрывает следующий. Если его увёл
 *  свайп (transform уже за краем), просто убираем. */
function leave(screen) {
  const swiped = screen.style.transform.includes('100%');
  const still = stillMotion();
  if (swiped || still || !screen.animate) {
    screen.remove();
    return;
  }
  screen.style.pointerEvents = 'none';
  screen.getAnimations().forEach((animation) => animation.cancel());
  const animation = screen.animate(
    [{ transform: 'none', opacity: 1 }, { transform: 'translateX(40%)', opacity: 0 }],
    { duration: 220, easing: 'cubic-bezier(.4, 0, .6, 1)', fill: 'forwards' },
  );
  animation.onfinish = () => screen.remove();
  animation.oncancel = () => screen.remove();
}

export function clearScreens() {
  while (stack.length) stack.pop().remove();
  syncBackButton();
}

export function hasScreens() {
  return stack.length > 0;
}

/** Верхний экран стека — его уводит свайп «назад». */
export function topScreen() {
  return stack[stack.length - 1] || null;
}

/** Внешний обработчик «назад» для случаев вне стека (вкладка, меню). */
let fallbackBack = () => false;

export function setFallbackBack(handler) {
  fallbackBack = handler;
  syncBackButton();
}

/** Есть ли куда возвращаться: экран, шторка, меню или не «День». */
export function canGoBack() {
  return stack.length > 0 || fallbackBack(true);
}

export function syncBackButton() {
  syncHistory();
  if (!tg?.BackButton) return;
  if (canGoBack()) tg.BackButton.show();
  else tg.BackButton.hide();
}

/* Системное «назад» Android вне Telegram (установленное приложение, браузер)
 * листает историю браузера, а экраны в ней не записаны — и приложение
 * закрывалось. Пока есть куда возвращаться, держим в истории одну служебную
 * запись: «назад» снимает её, и мы делаем то же, что кнопка «Назад». В
 * Telegram «назад» ведёт его кнопка, на iPhone — свой свайп, там не трогаем. */
const historyBack = !tg?.initData && /Android/i.test(navigator.userAgent);
// Служебная запись сейчас верхняя в истории.
let guarded = false;
// Сами сняли запись (history.back) и ждём её popstate — это не жест.
let unguarding = false;

function syncHistory() {
  if (!historyBack || unguarding) return;
  const want = canGoBack();
  if (want && !guarded) {
    history.pushState(history.state, '');
    guarded = true;
  } else if (!want && guarded) {
    guarded = false;
    unguarding = true;
    history.back();
  }
}

if (historyBack) {
  window.addEventListener('popstate', () => {
    if (unguarding) {
      unguarding = false;
      syncHistory();
      return;
    }
    guarded = false;
    if (canGoBack()) handleBack();
  });
}

export function handleBack() {
  if (stack.length) {
    tap('soft');
    popScreen();
    return;
  }
  if (fallbackBack(false)) tap('soft');
  syncBackButton();
}
