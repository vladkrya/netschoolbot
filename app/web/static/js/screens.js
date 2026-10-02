// Экраны поверх вкладок: урок, задание, оценка, предмет, письмо.
//
// Как в официальном приложении: экран открывается над вкладкой со стрелкой
// «назад», нижняя навигация остаётся на месте. Стрелку дублирует кнопка
// «Назад» Telegram — она закрывает верхний экран, а не всё приложение.
'use strict';

import { $, esc, tg } from './core.js';
import { tap } from './haptics.js';
import { ICONS } from './icons.js';

const stack = [];

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
  if (screen) screen.remove();
  syncBackButton();
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
  if (!tg?.BackButton) return;
  if (canGoBack()) tg.BackButton.show();
  else tg.BackButton.hide();
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
