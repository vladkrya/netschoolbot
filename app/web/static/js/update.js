// Перезапуск на новую версию. Раньше открытое приложение после выкладки
// жило на старом коде, пока его не закроют. Теперь сервер помечает версию
// в каждом ответе (X-Netschool-Version, core.js), и, увидев новую, мы
// показываем отсчёт «3… 2… 1», перезагружаем страницу и возвращаем туда же:
// вкладка, день, открытые экраны (предмет с «что если», урок, письмо…), их
// прокрутка и поля. Начатый ввод, PIN и партию в игре не перебиваем.
'use strict';

import { state } from './core.js';
import { ICONS } from './icons.js';
import { fillFields, keptState, restoreKept, screenTrail, topScreen } from './screens.js';
import { openHomework, openLesson, openMark } from './details.js';
import { openSubjectForDate, openSubjectNamed } from './totals.js';
import { openAnnouncements, openCompose, openMessage } from './mail.js';
import { openDue } from './due.js';
import { openSearch } from './search.js';
import { openGame } from './games.js';

const RESUME_KEY = 'ns-resume';
// Метка в адресе перезапуска: по ней worker ждёт свежую страницу из сети,
// а не отдаёт через 3 секунды сохранённую копию (sw.js). На iPhone сеть
// после сна просыпается секундами, и перезапуск открывал ту же старую версию.
const UPDATE_PARAM = 'update';
// На какую версию уже перезапускались. Медленная сеть могла отдать старую
// страницу из копии worker — второй раз по кругу не перезапускаем.
const TRIED_KEY = 'ns-update-tried';
// Точка возврата нужна только сразу после перезапуска, а не через час.
const RESUME_TTL_MS = 60000;
const COUNTDOWN = 3;
const BUSY_CHECK_MS = 2000;

let pending = false;
let target = '';
let postponed = false;
let toast = null;
let waitTimer = 0;

/** Что-то печатают, открыт PIN или начатое письмо — перезапуск подождёт. */
function busy() {
  if (document.documentElement.classList.contains('app-locked')) return true;
  // Открытая шторка закрыла бы плашку с отсчётом — ждём, пока её закроют.
  if (document.getElementById('sheet')?.hidden === false) return true;
  // Партию не обрываем: перезапуск — когда выйдут из игры.
  if (document.querySelector('.game-screen')) return true;
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
      screens: screenTrail(), kept: keptState(),
    }));
  } catch { /* приватный режим — откроется как обычно */ }
}

function restart() {
  saveResume();
  const url = new URL(location.href);
  url.searchParams.set(UPDATE_PARAM, target);
  location.replace(url.href);
}

/** Метка перезапуска своё отработала — убираем из адреса. */
function dropUpdateParam() {
  const params = new URLSearchParams(location.search);
  if (!params.has(UPDATE_PARAM)) return;
  params.delete(UPDATE_PARAM);
  const query = params.toString();
  history.replaceState(history.state, '', `${location.pathname}${query ? `?${query}` : ''}${location.hash}`);
}

/** Куда вернуться после перезапуска на новую версию. Читается один раз.
 *  Состояние модулей (прикидки «что если») возвращается сразу. */
export function takeResume() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(RESUME_KEY) || 'null');
    sessionStorage.removeItem(RESUME_KEY);
    if (!saved || Date.now() - saved.at >= RESUME_TTL_MS) return null;
    restoreKept(saved.kept);
    return saved;
  } catch {
    return null;
  }
}

// Чем открыть экран заново — по пометке markScreen в его модуле.
const OPENERS = {
  lesson: openLesson,
  homework: openHomework,
  mark: openMark,
  subject: openSubjectNamed,
  'subject-date': openSubjectForDate,
  message: openMessage,
  announcements: openAnnouncements,
  compose: openCompose,
  due: openDue,
  search: openSearch,
  game: openGame,
};
const RENDER_WAIT_MS = 4000;

/** Экран дорисовался: нет заглушек загрузки. Не дольше RENDER_WAIT_MS. */
async function rendered(screen) {
  const body = screen.querySelector('.screen-body');
  const until = Date.now() + RENDER_WAIT_MS;
  while (Date.now() < until && (!body.childElementCount || body.querySelector('.skeleton'))) {
    await new Promise((resolve) => setTimeout(resolve, 80));
  }
  await new Promise((resolve) => requestAnimationFrame(resolve));
}

/** Открыть экраны, что были поверх вкладки, — по порядку, без анимации.
 *  Экран, который открыть нельзя, обрывает стопку: выше него — не к месту. */
export async function reopenScreens(trail = []) {
  if (!trail.length) return;
  const root = document.documentElement;
  root.classList.add('resuming');
  try {
    for (const item of trail) {
      const open = OPENERS[item.kind];
      if (!open) break;
      const before = topScreen();
      await open(...(Array.isArray(item.args) ? item.args : []));
      const screen = topScreen();
      if (!screen || screen === before) break;
      await rendered(screen);
      fillFields(screen, item.fields);
      screen.scrollTop = item.scroll || 0;
    }
  } catch {
    /* что открылось — то открылось */
  } finally {
    setTimeout(() => root.classList.remove('resuming'), 300);
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
  dropUpdateParam();
  document.addEventListener('netschool:newversion', ({ detail }) => {
    try {
      if (sessionStorage.getItem(TRIED_KEY) === detail.version) return;
      sessionStorage.setItem(TRIED_KEY, detail.version);
    } catch { /* приватный режим */ }
    pending = true;
    target = detail.version;
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
