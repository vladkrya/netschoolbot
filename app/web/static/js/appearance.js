// Оформление: цвет шапки и меню, полноэкранный режим в Telegram.
'use strict';

import { initData, tg } from './core.js';

const ACCENT_KEY = 'ns-accent';

export const ACCENTS = [
  ['#4a6fae', 'Синий'],
  ['#2e8b6e', 'Изумрудный'],
  ['#6b4fa8', 'Фиолетовый'],
  ['#a8434f', 'Вишнёвый'],
  ['#c9702f', 'Оранжевый'],
  ['#2a8fa3', 'Бирюзовый'],
  ['#3d4450', 'Графит'],
  ['#1f2a44', 'Ночной'],
];

export function currentAccent() {
  try {
    return localStorage.getItem(ACCENT_KEY) || ACCENTS[0][0];
  } catch {
    return ACCENTS[0][0];
  }
}

/** Смешать цвет с белым (amount > 0) или чёрным (amount < 0). */
function shade(hex, amount) {
  const value = parseInt(hex.slice(1), 16);
  const channels = [value >> 16, (value >> 8) & 255, value & 255];
  const target = amount > 0 ? 255 : 0;
  const mixed = channels.map((c) => Math.round(c + (target - c) * Math.abs(amount)));
  return `#${mixed.map((c) => c.toString(16).padStart(2, '0')).join('')}`;
}

export function applyAccent(color = currentAccent()) {
  if (!/^#[0-9a-f]{6}$/i.test(color)) color = ACCENTS[0][0];
  const root = document.documentElement.style;
  const dark = shade(color, -0.18);
  root.setProperty('--head', color);
  root.setProperty('--head-2', dark);
  root.setProperty('--bar', shade(color, 0.12));
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', color);
  // Шапка и нижняя панель самого Telegram — в тон приложению.
  if (initData && tg) {
    try {
      tg.setHeaderColor?.(color);
      tg.setBottomBarColor?.(dark);
    } catch {
      /* старый клиент Telegram — просто без покраски */
    }
  }
}

export function saveAccent(color) {
  try {
    localStorage.setItem(ACCENT_KEY, color);
  } catch {
    /* без хранилища — до перезагрузки */
  }
  applyAccent(color);
}

/**
 * Во весь экран внутри Telegram (Bot API 8.0+).
 * Сверху Telegram оставляет свои кнопки «закрыть» и «…» — их отступ
 * (contentSafeAreaInset) учитывает CSS через переменные --tg-*.
 */
export function initFullscreen() {
  if (!initData || !tg) return;
  try {
    tg.disableVerticalSwipes?.();
    if (tg.isVersionAtLeast?.('8.0') && !tg.isFullscreen && ['ios', 'android'].includes(tg.platform)) {
      tg.requestFullscreen();
    }
  } catch {
    /* клиент без полноэкранного режима */
  }
  const sync = () => {
    const root = document.documentElement.style;
    const safe = tg.safeAreaInset || {};
    const content = tg.contentSafeAreaInset || {};
    root.setProperty('--tg-top', `${(safe.top || 0) + (content.top || 0)}px`);
    root.setProperty('--tg-bottom', `${safe.bottom || 0}px`);
  };
  sync();
  for (const event of ['safeAreaChanged', 'contentSafeAreaChanged', 'fullscreenChanged']) {
    tg.onEvent?.(event, sync);
  }
}
