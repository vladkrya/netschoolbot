// Настройки, общие для миниаппа в Telegram и сайта: тема, цвет шапки, веса калькулятора
// и пороги оценок.
// Храним на сервере у пользователя, а в localStorage — копию, чтобы применить
// её до первой отрисовки, не дожидаясь сети.
'use strict';

import { api, demo } from './core.js';

const KEYS = {
  theme: 'ns-theme', design: 'ns-design-v2', designNotice: 'ns-design-notice', weekends: 'ns-weekends', hwStatus: 'ns-hw-status', scheduleTab: 'ns-schedule-tab', accent: 'ns-accent', weights: 'ns-weights', thresholds: 'ns-thresholds', tabs: 'ns-tabs', pins: 'ns-pins', pwa: 'ns-pwa',
};
// Тема, дизайн и цвет лежат строкой (их читает скрипт в <head>), остальное — JSON.
const JSON_PREFS = new Set(['weights', 'thresholds', 'tabs', 'pins']);

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
let timer = null;
// Что поменяли на устройстве, пока шёл запрос настроек с сервера: пришедший
// ответ старше, и его значение откатило бы переключение (дизайн «не
// включался», пока страницу не перезагрузишь).
let syncStarted = false;
const editedDuringSync = new Set();

export function setPref(name, value) {
  writeLocal(name, value);
  if (syncStarted) editedDuringSync.add(name);
  if (demo) return;
  pending[name] = value;
  clearTimeout(timer);
  timer = setTimeout(flush, 400);
}

async function flush() {
  const body = pending;
  pending = {};
  try {
    await api('/api/prefs', { method: 'POST', body: JSON.stringify(body) });
  } catch {
    /* без сети — останется на этом устройстве, уйдёт со следующим изменением */
    pending = { ...body, ...pending };
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
    ({ data: server } = await api('/api/prefs'));
  } catch {
    return false;
  } finally {
    syncStarted = false;
  }
  let changed = false;
  for (const name of Object.keys(KEYS)) {
    if (editedDuringSync.has(name)) continue;
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
