// Настройки, общие для миниаппа в Telegram и сайта: тема, цвет шапки, веса калькулятора
// и пороги оценок.
// Храним на сервере у пользователя, а в localStorage — копию, чтобы применить
// её до первой отрисовки, не дожидаясь сети.
'use strict';

import { api, demo } from './core.js';
import { pendingOp, send } from './outbox.js';

const KEYS = {
  theme: 'ns-theme', design: 'ns-design-v2', designNotice: 'ns-design-notice', weekends: 'ns-weekends', hwStatus: 'ns-hw-status', scheduleTab: 'ns-schedule-tab', answers: 'ns-answers', answerHint: 'ns-answer-hint', accent: 'ns-accent', accents: 'ns-accents', weights: 'ns-weights', thresholds: 'ns-thresholds', tabs: 'ns-tabs', pins: 'ns-pins', pwa: 'ns-pwa', lessonEdits: 'ns-lesson-edits', confetti: 'ns-confetti', motion: 'ns-motion',
  // Хеллоуин: явный выбор тумблером ('on'/'off') и свой цвет на праздник.
  halloween: 'ns-halloween-choice', halloweenAccent: 'ns-halloween-accent',
  // Ретро-сайт ('on'/'off'), retro.js.
  retro: 'ns-retro',
  // Сдвиг звонка для таймера урока, секунды строкой (timer.js).
  bellShift: 'ns-bell-shift',
  // Отсчёт до каникул на «Дне» ('show'/'hide'), countdown.js.
  countdown: 'ns-countdown',
  // Цели на четверть: {предмет: '5' | '4' | '3'} (totals.js).
  goals: 'ns-goals',
  // Какую версию «Что нового» уже открывали (whatsnew.js).
  whatsNew: 'ns-whats-new',
  // «Не выходить из аккаунта» для входа через Госуслуги (true/false).
  keepSignedIn: 'ns-keep-signed-in',
  // Свайп «назад»: где начинается ('anywhere'/'edge'/'off') и жест от правого
  // края влево ('on'/'off'), swipeback.js.
  swipeBack: 'ns-swipe-back', swipeRight: 'ns-swipe-right',
};
// Тема, дизайн и цвет лежат строкой (их читает скрипт в <head>), остальное — JSON.
const JSON_PREFS = new Set(['weights', 'thresholds', 'tabs', 'pins', 'accents', 'lessonEdits', 'goals', 'keepSignedIn']);

function readLocal(name) {
  try {
    const raw = localStorage.getItem(KEYS[name]);
    if (raw == null) return null;
    return JSON_PREFS.has(name) ? JSON.parse(raw) : raw;
  } catch {
    return null;
  }
}

function writeLocal(name, value) {
  try {
    localStorage.setItem(KEYS[name], JSON_PREFS.has(name) ? JSON.stringify(value) : value);
  } catch {
    /* хранилище недоступно — настройка продержится до перезагрузки */
  }
}

export function getPref(name) {
  return readLocal(name);
}

// Частые изменения (несколько крестиков подряд) уходят одним запросом.
let pending = {};
// Какими настройки были до изменений в pending: вернули как было без сети —
// в очереди (outbox.js) ничего не ждёт.
let pendingBase = {};
let timer = null;
// Что поменяли на устройстве, пока шёл запрос настроек с сервера: пришедший
// ответ старше, и его значение откатило бы переключение (дизайн «не
// включался», пока страницу не перезагрузишь).
let syncStarted = false;
const editedDuringSync = new Set();

export function setPref(name, value) {
  if (!(name in pendingBase)) pendingBase[name] = readLocal(name);
  writeLocal(name, value);
  if (syncStarted) editedDuringSync.add(name);
  if (demo) return;
  pending[name] = value;
  clearTimeout(timer);
  timer = setTimeout(flush, 400);
}

/** Отправить отложенные изменения сейчас: перед уходом со страницы. */
export async function flushPrefs() {
  clearTimeout(timer);
  if (Object.keys(pending).length) await flush();
}

async function flush() {
  const body = pending;
  const base = pendingBase;
  pending = {};
  pendingBase = {};
  try {
    // Без сети — в очередь (outbox.js): уйдёт, когда сеть появится, даже
    // если приложение до тех пор закроют.
    await send('prefs', '/api/prefs', { ...pendingOp('prefs')?.body, ...body }, base);
  } catch {
    /* сервер не принял — останется на этом устройстве, уйдёт со следующим изменением */
    pending = { ...body, ...pending };
    pendingBase = { ...base, ...pendingBase };
  }
}

/**
 * Забрать настройки с сервера. Сохранённое там главнее: так выбор в миниаппе
 * доезжает до сайта и обратно. Если на сервере ключа ещё нет, а на этом
 * устройстве он выбран — отправляем его наверх.
 * Возвращает true, если что-то поменялось и оформление нужно применить заново.
 */
export async function syncPrefs() {
  if (demo) return false;
  let server;
  syncStarted = true;
  try {
    const payload = await api('/api/prefs');
    // Сохранённая копия без сети старше того, что выбрано здесь.
    if (payload.offline) return false;
    server = payload.data;
  } catch {
    return false;
  } finally {
    syncStarted = false;
  }
  // Изменённое без сети ещё не дошло до сервера — его значение главнее.
  const waiting = pendingOp('prefs')?.body || {};
  let changed = false;
  for (const name of Object.keys(KEYS)) {
    if (editedDuringSync.has(name) || name in waiting) continue;
    const local = readLocal(name);
    if (name in server) {
      if (JSON.stringify(server[name]) !== JSON.stringify(local)) {
        writeLocal(name, server[name]);
        changed = true;
      }
    } else if (local != null) {
      pending[name] = local;
    }
  }
  if (Object.keys(pending).length) flush();
  return changed;
}
