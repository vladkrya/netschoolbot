// Оформление: цвет шапки и меню, полноэкранный режим в Telegram.
'use strict';

import { initData, tg } from './core.js';
import { getPref, setPref } from './prefs.js';
import { HALLOWEEN_ICONS, ICONS } from './icons.js';

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
  // У хеллоуинского оформления свой акцент — обычный выбранный цвет не трогаем.
  if (isHalloween()) color = halloweenAccent();
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

/*
 * Хеллоуинское оформление — у всех в праздничный сезон (флаг halloween в
 * профиле). Выбор тумблером — общая настройка halloween ('off' — выключили),
 * одна на Telegram и сайт: раньше он жил только на устройстве, и в браузере
 * тема могла остаться выключенной, когда в Telegram она есть. ns-halloween —
 * копия итога для скрипта в <head>: по 'on' он ставит data-holiday до
 * отрисовки. Правила — в halloween.css.
 */
const HALLOWEEN_KEY = 'ns-halloween';
let halloweenAllowed = null;

/** Свои цвета акцента на Хеллоуин: в меню вместо обычных, пока тема включена. */
export const HALLOWEEN_ACCENTS = [
  ['#e8740c', 'Тыквенный'],
  ['#8e4fd6', 'Ведьмин'],
  ['#5ea82e', 'Зелье'],
  ['#c7303d', 'Кровавая луна'],
  ['#3f9fae', 'Призрачный'],
  ['#c2a46a', 'Костяной'],
];

export function halloweenAccent() {
  const color = getPref('halloweenAccent');
  return /^#[0-9a-f]{6}$/i.test(color || '') ? color : HALLOWEEN_ACCENTS[0][0];
}

export function saveHalloweenAccent(color) {
  setPref('halloweenAccent', color);
  applyAccent();
}

/** Иконка вкладки нижней панели и кнопки помощника: в Хеллоуин — праздничная, если такая есть. */
export function navIcon(name) {
  return (isHalloween() && HALLOWEEN_ICONS[name]) || ICONS[name] || '';
}

function readHalloween() {
  try { return localStorage.getItem(HALLOWEEN_KEY); } catch { return null; }
}

function writeHalloween(on) {
  try {
    if (on) localStorage.setItem(HALLOWEEN_KEY, 'on');
    else localStorage.removeItem(HALLOWEEN_KEY);
  } catch { /* приватный режим — продержится до перезагрузки */ }
}

export function halloweenAvailable() {
  return halloweenAllowed === true;
}

export function isHalloween() {
  // Пока профиль не пришёл — верим прошлому запуску, чтобы не мигать.
  if (halloweenAllowed === null) return readHalloween() === 'on';
  return halloweenAllowed && getPref('halloween') !== 'off';
}

export function applyHalloween() {
  const on = isHalloween();
  if (halloweenAllowed !== null) writeHalloween(on);
  const root = document.documentElement;
  const was = root.getAttribute('data-holiday') === 'halloween';
  if (on) root.setAttribute('data-holiday', 'halloween');
  else root.removeAttribute('data-holiday');
  // Луна, паутина, мыши и паук — слой внутри шапки, под её кнопками и
  // именем: мышь пролетает за заголовком, паук не закрывает стрелки.
  // Мерцающие звёзды — за всем экраном, туман — над панелью вкладок
  // (halloween.css). В сам Хеллоуин мышей больше.
  let decor = document.querySelector('.hw-decor');
  if (on && !decor) {
    decor = document.createElement('div');
    decor.className = 'hw-decor';
    decor.ariaHidden = 'true';
    const bats = halloweenDaysLeft() === 0 ? 6 : 3;
    decor.innerHTML = '<span class="hw-moon"></span><span class="hw-web"></span><span class="hw-spider"></span>'
      + '<span class="hw-bat"></span>'.repeat(bats);
    const stars = document.createElement('div');
    stars.className = 'hw-stars';
    stars.ariaHidden = 'true';
    stars.innerHTML = '<span></span>'.repeat(7);
    const fog = document.createElement('div');
    fog.className = 'hw-fog';
    fog.ariaHidden = 'true';
    (document.getElementById('appHeader') || document.body).prepend(decor);
    document.body.append(stars, fog);
    swoop();
  } else if (!on) {
    document.querySelectorAll('.hw-decor, .hw-stars, .hw-fog, .hw-swoop').forEach((el) => el.remove());
  }
  if (on === was) return;
  // Фон, который скрипт в <head> записал в style до загрузки стилей, —
  // по прошлому запуску. Если Хеллоуин включился (или выключился) позже,
  // он перебивал ночное небо: под прозрачным body светлел весь экран.
  root.style.removeProperty('background');
  root.style.removeProperty('--boot-bg');
  applyAccent();
  // Иконки панели и кнопки помощника меняются на месте — значки
  // непрочитанного остаются.
  document.querySelectorAll('.bottom-nav .ico[data-icon], .ai-fab[data-icon]').forEach((icon) => {
    icon.querySelector('svg.icon')?.remove();
    icon.insertAdjacentHTML('afterbegin', navIcon(icon.dataset.icon));
  });
}

/** Сколько дней до 31 октября этого года; 0 — сегодня; после — отрицательное. */
export function halloweenDaysLeft(now = new Date()) {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const day = new Date(now.getFullYear(), 9, 31);
  return Math.round((day - today) / 86400000);
}

/** Подпись к тумблеру: обратный отсчёт до праздника. */
export function halloweenCountdown(now = new Date()) {
  const left = halloweenDaysLeft(now);
  if (left === 0) return 'сегодня Хеллоуин! 🎃';
  if (left < 0) return 'Хеллоуин прошёл — до следующего года';
  const mod10 = left % 10;
  const mod100 = left % 100;
  const word = mod10 === 1 && mod100 !== 11 ? 'день'
    : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? 'дня' : 'дней';
  return `до Хеллоуина ${left} ${word}`;
}

/** Раз за запуск большая летучая мышь пролетает через весь экран. */
let swooped = false;
function swoop() {
  if (swooped || !showMotion() || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  swooped = true;
  setTimeout(() => {
    if (!isHalloween()) return;
    const bat = document.createElement('div');
    bat.className = 'hw-swoop';
    bat.ariaHidden = 'true';
    bat.addEventListener('animationend', () => bat.remove(), { once: true });
    document.body.append(bat);
  }, 2500);
}

/** Профиль сказал, доступно ли оформление этому человеку. */
export function setHalloweenAccess(allowed) {
  halloweenAllowed = Boolean(allowed);
  applyHalloween();
}

export function saveHalloween(on) {
  setPref('halloween', on ? 'on' : 'off');
  applyHalloween();
}

/** Субботу и воскресенье без уроков прячем, если не включено «Показывать выходные». */
export function showWeekends() {
  return getPref('weekends') === 'show';
}

/** Подсветка Д/З на главной: красным — не сделано, зелёным — сделано. По умолчанию включена. */
export function showHomeworkStatus() {
  return getPref('hwStatus') !== 'hide';
}

/** Анимации в приложении. По умолчанию включены; выключенные — класс
 *  no-motion на <html> (его же ставит скрипт в <head> до отрисовки). */
export function showMotion() {
  return getPref('motion') !== 'off';
}

export function applyMotion() {
  document.documentElement.classList.toggle('no-motion', !showMotion());
}

export function saveMotion(on) {
  setPref('motion', on ? 'on' : 'off');
  applyMotion();
}

/** Конфетти у новой пятёрки. По умолчанию включено. */
export function showConfetti() {
  return getPref('confetti') !== 'hide';
}

export function saveConfetti(on) {
  setPref('confetti', on ? 'show' : 'hide');
}

/** Блок «Мой ответ» на экранах задания и урока (мобильное API). */
export function showAnswers() {
  return getPref('answers') !== 'hide';
}

export function saveAnswers(on) {
  setPref('answers', on ? 'show' : 'hide');
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
  icon.innerHTML = navIcon(icon.dataset.icon);
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
