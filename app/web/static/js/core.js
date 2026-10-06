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
  totals: null,
  homework: null,
  homeworkPast: null,
  // Ключи выполненных заданий (Set): общие с /dz в боте.
  hwDone: null,
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

/** С какого среднего ставится оценка — обычное правило «от 4,5 — пятёрка». */
export const DEFAULT_THRESHOLDS = { 5: 4.5, 4: 3.5, 3: 2.5 };

/** Цвет среднего балла по оценке, которая из него выходит. */
export function avgClass(value, thresholds = DEFAULT_THRESHOLDS) {
  const avg = Number(value) || 0;
  return avg >= thresholds[5] ? 'g5' : avg >= thresholds[4] ? 'g4' : avg >= thresholds[3] ? 'g3' : 'g2';
}

export function skeleton(count = 5) {
  return `<div class="card">${'<div class="skeleton"></div>'.repeat(count)}</div>`;
}

let bannerTimer = 0;

/** Сообщение под шапкой. Закрывается нажатием; hideAfter (мс) — само гаснет:
 *  для «Отправлено» и подобного, иначе оно висело бы на всех вкладках. */
export function banner(text, isError, hideAfter = 0) {
  const el = $('banner');
  if (!el.dataset.dismiss) {
    el.dataset.dismiss = '1';
    el.addEventListener('click', () => banner(''));
  }
  clearTimeout(bannerTimer);
  el.textContent = text || '';
  el.classList.toggle('error', Boolean(isError));
  el.hidden = !text;
  if (text && hideAfter) bannerTimer = setTimeout(() => banner(''), hideAfter);
}

const REQUEST_TIMEOUT_MS = 75000;
// Кэш моложе минуты — его только что получили со школьного сервера,
// перепроверять незачем.
const REVALIDATE_AFTER_S = 60;
const STALE_TEXT = 'Сервер дневника сейчас недоступен. Показаны сохранённые данные.';
let staleShown = false;
// Пути, которые за этот запуск уже сверили со школьным сервером.
const revalidated = new Set();

function staleBanner(show) {
  if (show) {
    staleShown = true;
    banner(STALE_TEXT);
  } else if (staleShown) {
    // Свежий ответ пришёл — плашка о сохранённых данных больше не правда.
    staleShown = false;
    banner('');
  }
}

/** Запрос к API с удостоверением личности и разбором ошибки. */
async function request(path, options = {}) {
  // Для FormData браузер сам ставит Content-Type с границей частей.
  const isForm = typeof FormData !== 'undefined' && options.body instanceof FormData;
  const headers = { ...(isForm ? {} : { 'Content-Type': 'application/json' }), ...(options.headers || {}) };
  // initData подписана токеном бота — сервер доверяет ей больше, чем
  // токену из ссылки, поэтому шлём оба и приоритет отдаёт он.
  if (initData) headers['X-Telegram-Init-Data'] = initData;
  if (token) headers['X-Netschool-Token'] = token;

  // Без предела запрос к зависшему школьному серверу крутил загрузку
  // бесконечно. Сервер сам отвечает не дольше минуты при обновлении.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const { onFresh, ...init } = options;
  let response;
  try {
    response = await fetch(base + path, {
      ...init, headers, cache: 'no-store', signal: controller.signal,
    });
  } catch (error) {
    const failure = new Error(error.name === 'AbortError'
      ? 'Сервер школы долго не отвечает. Попробуйте обновить чуть позже.'
      : 'Нет связи с сервером. Проверьте интернет.');
    failure.reason = 'server';
    throw failure;
  } finally {
    clearTimeout(timer);
  }

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
  if (response.headers.get('X-Netschool-Offline')) {
    // Ответ из копии service worker: сети нет совсем.
    banner('Нет связи — показаны сохранённые данные');
    return { ...payload, offline: true, stale: false };
  }
  return payload;
}

const withRefresh = (path) => `${path}${path.includes('?') ? '&' : '?'}refresh=1`;

/** Сверить показанные сохранённые данные со школьным сервером.
 *  Новое — отдаём в onFresh и сообщаем экранам событием netschool:fresh. */
async function revalidate(path, shown, onFresh) {
  revalidated.add(path);
  let fresh;
  try {
    fresh = await request(withRefresh(path));
  } catch {
    revalidated.delete(path);
    staleBanner(true);
    return;
  }
  // Школьный сервер не ответил — на экране остаются сохранённые данные.
  if (fresh.stale) {
    revalidated.delete(path);
    staleBanner(true);
    return;
  }
  staleBanner(false);
  if (JSON.stringify(fresh.data) === JSON.stringify(shown.data)) return;
  onFresh(fresh);
  document.dispatchEvent(new CustomEvent('netschool:fresh', { detail: path }));
}

/** Запрос к API. Сохранённые сервером данные показываются сразу; если
 *  передан onFresh, устаревшие тут же сверяются со школьным сервером. */
export async function api(path, options = {}) {
  if (demo) {
    // Демо: данные выдумывает браузер, сервер не участвует.
    const { demoApi } = await import('./demo.js');
    return demoApi(path, options);
  }
  let payload = await request(path, options);

  const outdated = payload.stale || (payload.age ?? 0) > REVALIDATE_AFTER_S;
  const canRevalidate = !options.method && !path.includes('refresh=1') && !revalidated.has(path);
  if (outdated && canRevalidate) {
    if (options.onFresh) {
      revalidate(path, payload, options.onFresh);
      return payload;
    }
    // Экрану без перерисовки отдаём сразу свежее, если сервер успевает.
    if (payload.stale) {
      try {
        payload = await request(withRefresh(path));
      } catch {
        /* остаётся сохранённое */
      }
    }
  }
  if (payload.stale && !payload.refreshing) staleBanner(true);
  else if ('data' in payload && !payload.stale) staleBanner(false);
  return payload;
}

/** Ссылка на вложение: браузер качает её сам, заголовок туда не положить. */
export function attachmentUrl(id, name = '') {
  if (demo) return '#';
  const credential = initData
    ? `tgWebAppData=${encodeURIComponent(initData)}`
    : `token=${encodeURIComponent(token)}`;
  // Имя нужно серверу для заголовка: без него файл сохранялся как
  // «attachment-123» без расширения и не открывался как docx или pdf.
  const filename = name ? `&name=${encodeURIComponent(name)}` : '';
  return `${base}/api/attachment/${id}?${credential}${filename}`;
}

export function attachmentsHtml(items) {
  if (!items || !items.length) return '';
  return items
    .map(
      (item) =>
        `<a class="attach" href="${attachmentUrl(item.id, item.name)}" target="_blank" rel="noopener">${ICONS.clip} ${esc(item.name)}</a>`
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
