// Общее для всех экранов: доступ к API, разметка, состояние.
'use strict';

import { ICONS } from './icons.js';

export const { base, token, pushEnabled, student, school, demo } = window.NETSCHOOL;

// Telegram отдаёт объект только внутри мини-приложения. Вне его
// (установленное на домашний экран) работаем по токену из ссылки.
export const tg = window.Telegram && window.Telegram.WebApp;
export const initData = (tg && tg.initData) || '';

/** Состояние приложения. Одно место вместо переменных врассыпную. */
export const state = {
  tab: 'day',
  date: null,
  scheduleWeek: 0,
  grades: null,
  homework: null,
  mail: null,
  profile: null,
  calc: null,
};

export const $ = (id) => document.getElementById(id);

export function esc(value) {
  const div = document.createElement('div');
  div.textContent = value == null ? '' : String(value);
  return div.innerHTML;
}

/** Экранирование плюс переносы строк и кликабельные ссылки. */
export function escRich(value) {
  return esc(value)
    .replace(/\n/g, '<br>')
    .replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
}

export function markClass(mark) {
  const digit = String(mark || '').match(/[2-5]/);
  return digit ? `mark m${digit[0]}` : 'mark';
}

export function avgClass(value) {
  const rounded = Math.round(Number(value) || 0);
  return rounded >= 5 ? 'g5' : rounded === 4 ? 'g4' : rounded === 3 ? 'g3' : 'g2';
}

export function skeleton(count = 5) {
  return `<div class="card">${'<div class="skeleton"></div>'.repeat(count)}</div>`;
}

export function banner(text, isError) {
  const el = $('banner');
  el.textContent = text || '';
  el.classList.toggle('error', Boolean(isError));
  el.hidden = !text;
}

/** Запрос к API с удостоверением личности и разбором ошибки. */
export async function api(path, options = {}) {
  if (demo) {
    // Демо: данные выдумывает браузер, сервер не участвует.
    const { demoApi } = await import('./demo.js');
    return demoApi(path, options);
  }
  // Для FormData браузер сам ставит Content-Type с границей частей.
  const isForm = typeof FormData !== 'undefined' && options.body instanceof FormData;
  const headers = { ...(isForm ? {} : { 'Content-Type': 'application/json' }), ...(options.headers || {}) };
  // initData подписана токеном бота — сервер доверяет ей больше, чем
  // токену из ссылки, поэтому шлём оба и приоритет отдаёт он.
  if (initData) headers['X-Telegram-Init-Data'] = initData;
  if (token) headers['X-Netschool-Token'] = token;

  const response = await fetch(base + path, { ...options, headers, cache: 'no-store' });

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error('Сервер ответил неожиданным образом');
  }
  if (!response.ok || !payload.ok) {
    const error = new Error(payload.error || `Ошибка ${response.status}`);
    error.reason = payload.reason;
    throw error;
  }
  return payload;
}

/** Ссылка на вложение: браузер качает её сам, заголовок туда не положить. */
export function attachmentUrl(id) {
  if (demo) return '#';
  const credential = initData
    ? `tgWebAppData=${encodeURIComponent(initData)}`
    : `token=${encodeURIComponent(token)}`;
  return `${base}/api/attachment/${id}?${credential}`;
}

export function attachmentsHtml(items) {
  if (!items || !items.length) return '';
  return items
    .map(
      (item) =>
        `<a class="attach" href="${attachmentUrl(item.id)}" target="_blank" rel="noopener">${ICONS.clip} ${esc(item.name)}</a>`
    )
    .join('');
}

export function errorCard(error, retryId) {
  const needsLogin = error.reason === 'login' || error.reason === 'auth';
  return `<div class="card error-card">
      <p>${esc(error.message)}</p>
      ${needsLogin ? '<p class="muted small" style="margin-top:6px">Откройте бота и выполните /login.</p>' : ''}
      ${retryId ? `<button class="btn" id="${retryId}">Повторить</button>` : ''}
    </div>`;
}

/* ─────────────────────── Оценки и пропуски ─────────────────────── */

/** Отметки посещаемости: в официальном приложении они пишутся текстом
 * в карточке урока («Пропуск по болезни»), а не рамкой, как оценка. */
export const ABSENCES = {
  'Б': 'Пропуск по болезни',
  'УП': 'Пропуск по уважительной причине',
  'НП': 'Пропуск без причины',
  'Н': 'Пропуск',
  'ОТ': 'Отсутствовал',
  'ОП': 'Опоздал',
  'ОСВ': 'Освобождён',
};

export function absenceText(mark) {
  return ABSENCES[String(mark || '').trim().toUpperCase()] || '';
}

export function isAbsence(mark) {
  return Boolean(absenceText(mark));
}

export function observedTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

/** Средневзвешенный балл по числовым оценкам. null — считать не из чего. */
export function weightedAverage(marks) {
  let total = 0;
  let weights = 0;
  for (const mark of marks) {
    const value = Number(mark.mark);
    if (!Number.isFinite(value) || value < 1 || value > 5) continue;
    const weight = Number(mark.weight) > 0 ? Number(mark.weight) : 1;
    total += value * weight;
    weights += weight;
  }
  return weights ? total / weights : null;
}

export function formatAverage(value) {
  return value == null ? '-' : value.toFixed(2).replace('.', ',');
}

/* ─────────────────────────── Даты ─────────────────────────── */

export const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
export const MONTHS = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

export function isoToday() {
  const now = new Date();
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function pad(n) {
  return n < 10 ? `0${n}` : String(n);
}

export function parseIso(iso) {
  const [y, m, d] = String(iso).split('-').map(Number);
  return new Date(y, m - 1, d);
}

export function shortDate(iso) {
  const date = parseIso(iso);
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}`;
}

export function longDate(iso) {
  const date = parseIso(iso);
  return `${WEEKDAYS[(date.getDay() + 6) % 7]}, ${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

export const WEEKDAYS_FULL = [
  'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье',
];
const WEEKDAYS_ACC = [
  'понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу', 'воскресенье',
];

export function weekdayIndex(iso) {
  return (parseIso(iso).getDay() + 6) % 7;
}

/** «30 сентября» */
export function dayMonth(iso) {
  const date = parseIso(iso);
  return `${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

/** «на среду, 30 сентября» — подзаголовок экранов задания и оценки. */
export function forDate(iso) {
  return `на ${WEEKDAYS_ACC[weekdayIndex(iso)]}, ${dayMonth(iso)}`;
}

/** «Среда, 30 сентября» */
export function fullDate(iso) {
  return `${WEEKDAYS_FULL[weekdayIndex(iso)]}, ${dayMonth(iso)}`;
}

export function addDays(iso, days) {
  const date = parseIso(iso);
  date.setDate(date.getDate() + days);
  return isoOf(date);
}

export function isoOf(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Понедельник недели, в которую попадает дата. */
export function mondayOf(iso) {
  return addDays(iso, -weekdayIndex(iso));
}

/** На сколько недель дата отстоит от текущей. */
export function weekOffsetOf(iso) {
  const diff = parseIso(mondayOf(iso)) - parseIso(mondayOf(isoToday()));
  return Math.round(diff / (7 * 24 * 3600 * 1000));
}
