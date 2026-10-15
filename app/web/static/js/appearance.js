// Оформление: цвет шапки и меню, полноэкранный режим в Telegram.
'use strict';

import { initData, tg } from './core.js';
import { getPref, setPref } from './prefs.js';
import { ICONS } from './icons.js';

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
  const color = getPref('accent');
  return /^#[0-9a-f]{6}$/i.test(color || '') ? color : ACCENTS[0][0];
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
  syncChromeColor();
  // Шапка и нижняя панель самого Telegram — в тон приложению.
  if (initData && tg) {
    try {
      // Панель вкладок теперь стеклянная и парит над фоном — низ Telegram в цвет фона.
      tg.setBottomBarColor?.('bg_color');
    } catch {
      /* старый клиент Telegram — просто без покраски */
    }
  }
}

/** Цвет системной области совпадает с шапкой поверх фона текущей темы. */
export function syncChromeColor() {
  const styles = getComputedStyle(document.documentElement);
  const head = styles.getPropertyValue('--head').trim();
  const bg = styles.getPropertyValue('--bg').trim();
  if (![head, bg].every((value) => /^#[0-9a-f]{6}$/i.test(value))) return;
  const glass = CSS.supports('background', 'color-mix(in srgb, red 50%, transparent)')
    && (CSS.supports('backdrop-filter', 'blur(1px)') || CSS.supports('-webkit-backdrop-filter', 'blur(1px)'));
  const channels = [1, 3, 5].map((offset) => {
    const foreground = parseInt(head.slice(offset, offset + 2), 16);
    const background = parseInt(bg.slice(offset, offset + 2), 16);
    return Math.round(glass ? foreground * 0.8 + background * 0.2 : foreground);
  });
  // В новом дизайне шапка в тон фону — и системная область тоже.
  const color = isNewDesign() ? bg
    : `#${channels.map((value) => value.toString(16).padStart(2, '0')).join('')}`;
  document.documentElement.style.setProperty('--chrome-head', color);
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', color);
  if (initData && tg) {
    try { tg.setHeaderColor?.(color); } catch { /* старый клиент */ }
  }
}

// В режиме «как в системе» оформление меняется без перезапуска приложения.
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', syncChromeColor);

/** Новый дизайн — по умолчанию у всех; старый остаётся тумблером «Старый дизайн». */
export function isNewDesign() {
  return getPref('design') !== 'classic';
}

/** Включить или выключить новый дизайн: атрибут на <html>, правила в new.css. */
export function applyDesign() {
  if (isNewDesign()) document.documentElement.setAttribute('data-design', 'new');
  else document.documentElement.removeAttribute('data-design');
  syncChromeColor();
}

export function saveDesign(on) {
  setPref('design', on ? 'new' : 'classic');
  applyDesign();
  // Часть экранов в новом дизайне устроена иначе — их надо перерисовать.
  window.dispatchEvent(new CustomEvent('netschool:design'));
}

/** Субботу и воскресенье без уроков прячем, если не включено «Показывать выходные». */
export function showWeekends() {
  return getPref('weekends') === 'show';
}

/** Подсветка Д/З на главной: красным — не сделано, зелёным — сделано. По умолчанию включена. */
export function showHomeworkStatus() {
  return getPref('hwStatus') !== 'hide';
}

export function saveHomeworkStatus(on) {
  setPref('hwStatus', on ? 'show' : 'hide');
  window.dispatchEvent(new CustomEvent('netschool:design'));
}

export function saveWeekends(on) {
  setPref('weekends', on ? 'show' : 'hide');
  // Полоса недели и расписание перерисуются (слушатель в app.js).
  window.dispatchEvent(new CustomEvent('netschool:design'));
}

/** Вторая вкладка: расписание звонков (по умолчанию) или неделя уроков. */
export function showBells() {
  return getPref('scheduleTab') !== 'week';
}

export function saveBells(on) {
  setPref('scheduleTab', on ? 'bells' : 'week');
  applyScheduleTab();
  window.dispatchEvent(new CustomEvent('netschool:design'));
}

/** Подпись и значок второй вкладки в нижнем меню. */
export function applyScheduleTab() {
  const item = document.querySelector('.nav-item[data-tab="schedule"]');
  if (!item) return;
  const bells = showBells();
  const icon = item.querySelector('.ico');
  icon.dataset.icon = bells ? 'clock' : 'calendar';
  icon.innerHTML = ICONS[icon.dataset.icon];
  item.querySelector('.lbl-old').textContent = bells ? 'Звонки' : 'Расписание';
  item.querySelector('.lbl-new').textContent = bells ? 'Звонки' : 'Неделя';
}

export function saveAccent(color) {
  setPref('accent', color);
  applyAccent(color);
}

// Свои цвета, сохранённые рядом с готовыми: выбрал готовый — свой не пропал.
const MAX_SAVED_ACCENTS = 8;

export function savedAccents() {
  const list = getPref('accents');
  return Array.isArray(list) ? list.filter((color) => /^#[0-9a-f]{6}$/i.test(color)) : [];
}

export function rememberAccent(color) {
  const list = [...savedAccents().filter((c) => c !== color), color].slice(-MAX_SAVED_ACCENTS);
  setPref('accents', list);
}

export function forgetAccent(color) {
  setPref('accents', savedAccents().filter((c) => c !== color));
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
    document.documentElement.classList.toggle('tg-inset', Boolean(content.top));
  };
  sync();
  for (const event of ['safeAreaChanged', 'contentSafeAreaChanged', 'fullscreenChanged']) {
    tg.onEvent?.(event, sync);
  }
}
