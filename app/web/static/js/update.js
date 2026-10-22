// Перезапуск на новую версию. Раньше открытое приложение после выкладки
// жило на старом коде, пока его не закроют. Теперь сервер помечает версию
// в каждом ответе (X-Netschool-Version, core.js), и, увидев новую, мы
// показываем отсчёт «3… 2… 1», перезагружаем страницу и возвращаем на ту же
// вкладку, тот же день и ту же прокрутку. Начатый ввод и PIN не перебиваем.
'use strict';

import { state } from './core.js';
import { ICONS } from './icons.js';

const RESUME_KEY = 'ns-resume';
// На какую версию уже перезапускались. Медленная сеть могла отдать старую
// страницу из копии worker — второй раз по кругу не перезапускаем.
const TRIED_KEY = 'ns-update-tried';
// Точка возврата нужна только сразу после перезапуска, а не через час.
const RESUME_TTL_MS = 60000;
const COUNTDOWN = 3;
const BUSY_CHECK_MS = 2000;

let pending = false;
let postponed = false;
let toast = null;
let waitTimer = 0;

/** Что-то печатают, открыт PIN или начатое письмо — перезапуск подождёт. */
function busy() {
  if (document.documentElement.classList.contains('app-locked')) return true;
  // Открытая шторка закрыла бы плашку с отсчётом — ждём, пока её закроют.
  if (document.getElementById('sheet')?.hidden === false) return true;
  const active = document.activeElement;
  if (active?.matches?.('textarea, select, [contenteditable="true"], input:not([type="checkbox"]):not([type="radio"]):not([type="button"]):not([type="submit"]):not([type="range"])')) return true;
  // Начатый текст в открытом окне (письмо, заметка) — даже без фокуса.
  return [...document.querySelectorAll('.screen textarea, .screen input[type="text"], #sheet textarea, #sheet input[type="text"]')]
    .some((field) => field.offsetParent && field.value.trim());
}

function saveResume() {
  try {
    sessionStorage.setItem(RESUME_KEY, JSON.stringify({
      tab: state.tab, date: state.date, scheduleWeek: state.scheduleWeek, scroll: window.scrollY, at: Date.now(),
    }));
  } catch { /* приватный режим — откроется как обычно */ }
}

function restart() {
  saveResume();
  location.reload();
}

/** Куда вернуться после перезапуска на новую версию. Читается один раз. */
export function takeResume() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(RESUME_KEY) || 'null');
    sessionStorage.removeItem(RESUME_KEY);
    return saved && Date.now() - saved.at < RESUME_TTL_MS ? saved : null;
  } catch {
    return null;
  }
}

function hideToast() {
  if (!toast) return;
  const old = toast;
  toast = null;
  old.classList.add('leaving');
  setTimeout(() => old.remove(), 300);
}

function countdown() {
  hideToast();
  toast = document.createElement('div');
  toast.className = 'update-toast';
  toast.setAttribute('role', 'status');
  toast.innerHTML = `<span class="update-toast-icon">${ICONS.updating}</span>`
    + '<span class="update-toast-text"><b>Вышло обновление</b><small></small></span>'
    + '<button type="button" class="update-toast-later">Позже</button>';
  document.body.append(toast);
  const own = toast;
  const label = toast.querySelector('small');
  let left = COUNTDOWN;
  const tick = () => {
    if (toast !== own) return;
    // Пока шёл отсчёт, начали печатать — ждём, а не перебиваем.
    if (busy()) { hideToast(); waitIdle(); return; }
    if (left <= 0) { restart(); return; }
    label.textContent = `Перезапуск через ${left}…`;
    left -= 1;
    setTimeout(tick, 1000);
  };
  toast.querySelector('button').addEventListener('click', () => {
    postponed = true;
    hideToast();
  });
  tick();
}

function waitIdle() {
  clearTimeout(waitTimer);
  if (!pending || postponed || toast) return;
  if (document.hidden) return;
  if (busy()) { waitTimer = setTimeout(waitIdle, BUSY_CHECK_MS); return; }
  countdown();
}

/** Подписка на «вышла новая версия» — из app.js при запуске. */
export function initUpdates() {
  document.addEventListener('netschool:newversion', ({ detail }) => {
    try {
      if (sessionStorage.getItem(TRIED_KEY) === detail.version) return;
      sessionStorage.setItem(TRIED_KEY, detail.version);
    } catch { /* приватный режим */ }
    pending = true;
    if (document.hidden && !busy()) restart();
    else waitIdle();
  });
  // Свёрнутое приложение перезапускаем тихо: к возвращению всё уже новое.
  // «Позже» действует до этого момента.
  document.addEventListener('visibilitychange', () => {
    if (!pending) return;
    if (document.hidden) {
      if (!busy()) restart();
      else postponed = false;
    } else {
      waitIdle();
    }
  });
}
