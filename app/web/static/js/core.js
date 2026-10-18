// Общее для всех экранов: доступ к API, разметка, состояние.
'use strict';

import { ICONS } from './icons.js';

export const { base, token, pushEnabled, student, school, demo } = window.NETSCHOOL;
/** Есть ли ИИ-помощник: на сервере задан ключ, и это не демо. */
export const assistantEnabled = Boolean(window.NETSCHOOL.assistant);

/** СГО отдаёт ученика в порядке «Фамилия Имя Отчество». */
export function studentFirstName(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  return parts[1] || parts[0] || '';
}

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
  // Свои задачи ученика (notes.js).
  notes: null,
  mail: null,
  profile: null,
  calc: null,
  initialDiary: null,
  // Настройки с сервера бота (prefs.js), запрошенные из boot.js раньше дневника.
  prefsSync: null,
  initialDiaryFresh: null,
};

export const $ = (id) => document.getElementById(id);

/** Касание, начатое на элементе, который потом перерисовали (переписка
 *  чата, список уроков): его движения и отпускание браузер шлёт этому, уже
 *  убранному, элементу — до document они не всплывают, и жест застревал на
 *  середине. Ловим их на самом элементе, но только пока он откреплён:
 *  иначе событие пришло бы дважды. */
export function keepTouch(target, { move, end, cancel = end }) {
  if (!target?.addEventListener) return;
  const detached = (handler) => (event) => { if (!target.isConnected) handler(event); };
  const onMove = detached(move);
  const onEnd = detached(end);
  const onCancel = detached(cancel);
  const off = () => {
    target.removeEventListener('touchmove', onMove);
    target.removeEventListener('touchend', onEnd);
    target.removeEventListener('touchcancel', onCancel);
    target.removeEventListener('touchend', off);
    target.removeEventListener('touchcancel', off);
  };
  target.addEventListener('touchmove', onMove, { passive: false });
  target.addEventListener('touchend', onEnd);
  target.addEventListener('touchcancel', onCancel);
  target.addEventListener('touchend', off);
  target.addEventListener('touchcancel', off);
}

/** Движение выключено: системное «Уменьшить движение» или тумблер
 *  «Анимации» в настройках (класс no-motion на <html>, appearance.js). */
export const stillMotion = () => document.documentElement.classList.contains('no-motion')
  || matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Номер урока для показа. В день со своими уроками они нумеруются по
 *  порядку вместе со школьными (place, edits.js); сам number не трогаем —
 *  по нему правки находят школьный урок. */
export const lessonNo = (lesson) => lesson?.place ?? lesson?.number ?? '';

/** Текст для вставки в HTML — и в содержимое, и в значение атрибута.
 *  Кавычки тоже: иначе «МОУ "Школа № 24"» в data-атрибуте обрывался на
 *  кавычке, а чужой текст (имя вложения в письме) мог дописать свой атрибут. */
export function esc(value) {
  return (value == null ? '' : String(value))
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
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

/** Заглушка загрузки. При обновлении свайпом экран не очищается — видно
 *  только индикатор сверху, а данные меняются на месте. → заменено ли. */
export function showLoading(container, html) {
  if (state.refreshing && container.childElementCount) return false;
  container.innerHTML = html;
  return true;
}

/** Ошибка загрузки. При обновлении свайпом прежние данные остаются, а
 *  ошибка — плашкой; иначе карточка ошибки вместо содержимого. */
export function showFailure(container, error, html) {
  // «Сетевой город» объявлен недоступным (sgo.js) — карточка уже всё сказала,
  // показанные данные остаются без лишней плашки.
  if (error.sgoDown && container.childElementCount && !container.querySelector('.skeleton')) return;
  if (state.refreshing && container.childElementCount && !container.querySelector('.skeleton')) {
    state.refreshFailed = true;
    banner(error.message, true, 5000);
    return;
  }
  container.innerHTML = html;
}

export function skeleton(count = 5) {
  return `<div class="card">${'<div class="skeleton"></div>'.repeat(count)}</div>`;
}

let bannerTimer = 0;

/** Смахнуть плашку вверх или вбок; нажатие тоже закрывает. */
function bindBannerDismiss(el) {
  let x0 = 0, y0 = 0, dx = 0, dy = 0, active = false;
  el.addEventListener('touchstart', (event) => {
    const t = event.touches[0];
    x0 = t.clientX; y0 = t.clientY; dx = dy = 0; active = true;
    el.style.transition = 'none';
  }, { passive: true });
  el.addEventListener('touchmove', (event) => {
    if (!active) return;
    const t = event.touches[0];
    dx = t.clientX - x0;
    dy = Math.min(0, t.clientY - y0);
    el.style.transform = `translate(${dx}px, ${dy}px)`;
    el.style.opacity = String(Math.max(0, 1 - Math.max(Math.abs(dx), -dy) / 160));
  }, { passive: true });
  el.addEventListener('touchend', () => {
    if (!active) return;
    active = false;
    el.style.transition = '';
    if (Math.abs(dx) > 60 || dy < -30) {
      el.style.transform = dy < -30 && Math.abs(dx) < 60
        ? 'translateY(-150%)' : `translateX(${dx > 0 ? 120 : -120}%)`;
      el.style.opacity = '0';
      setTimeout(() => banner(''), 150);
    } else {
      el.style.transform = '';
      el.style.opacity = '';
    }
  });
  el.addEventListener('click', () => banner(''));
}

/** Сообщение под шапкой. Закрывается нажатием или смахиванием; hideAfter
 *  (мс) — само гаснет: для «Отправлено» и подобного, иначе оно висело бы на
 *  всех вкладках. */
export function banner(text, isError, hideAfter = 0) {
  const el = $('banner');
  if (!el.dataset.dismiss) {
    el.dataset.dismiss = '1';
    bindBannerDismiss(el);
  }
  clearTimeout(bannerTimer);
  // Закрыли плашку о сохранённых данных — повторные запросы её не возвращают.
  if (!text && notice) dismissedNotice = notice;
  notice = '';
  el.textContent = text || '';
  el.style.transform = '';
  el.style.opacity = '';
  el.classList.toggle('error', Boolean(isError));
  el.hidden = !text;
  if (text && hideAfter) bannerTimer = setTimeout(() => banner(''), hideAfter);
}

// Плашки о работе без связи: одна на состояние. Закрытая не всплывает при
// каждом следующем запросе, пока связь не вернётся.
let notice = '';
let dismissedNotice = '';
const NOTICE_HIDE_MS = 6000;

function showNotice(text) {
  if (notice === text || dismissedNotice === text) return;
  banner(text, false, NOTICE_HIDE_MS);
  notice = text;
}

function clearNotices() {
  dismissedNotice = '';
  if (notice) banner('');
  dismissedNotice = '';
}

let unlockToken = '';
export function setUnlock(value) { unlockToken = value; }
// Вход был без сети: на 423 пробуем войти на сервере и повторить запрос.
let lockedHandler = null;
export function onLocked(handler) { lockedHandler = handler; }

const REQUEST_TIMEOUT_MS = 75000;
// Кэш моложе минуты — его только что получили со школьного сервера,
// перепроверять незачем.
const REVALIDATE_AFTER_S = 60;
const OFFLINE_TEXT = 'Нет связи — показаны сохранённые данные';
const STALE_TEXT = 'Не удалось обновить дневник. Показаны сохранённые данные.';
// Школьный сервер не отвечает (техработы, сбой): ничего делать не нужно.
const APP_SERVER_TEXT = 'Не удалось подключиться к серверу приложения. Показаны сохранённые данные.';
const SERVER_TEXT = 'Не удалось подключиться к серверу «Сетевого города». Показаны сохранённые данные.';
// Пути, которые за этот запуск уже сверили со школьным сервером.
const revalidated = new Set();

/** Сервер сообщил о новом в дневнике: всё показанное снова сверить
 *  со школьным сервером при следующем открытии вкладки. */
export function forgetRevalidated() {
  revalidated.clear();
}

function staleBanner(show, reason, source) {
  // Свежий ответ пришёл — плашка о сохранённых данных больше не правда, и
  // карточка «долго не отвечает» тоже.
  if (!show) {
    clearNotices();
    if ($('sgoDown')?.dataset.slow) $('sgoDown').remove();
  }
  else if (reason === 'auth') loginFailedNotice();
  else if ($('sgoDown')) return;  // о простое школы уже сказано карточкой
  else showNotice(source === 'app' ? APP_SERVER_TEXT : reason === 'server' ? SERVER_TEXT : STALE_TEXT);
  if (show && reason === 'server' && source !== 'app') sgoHint(true);
}

// Дневник грузится дольше SGO_SLOW_MS — спрашиваем, не лежит ли сервер
// школы по странице статуса (sgo.js). Дольше SGO_STUCK_MS — говорим «долго не
// отвечает» в любом случае: сервер бота ждёт школу при обновлении до минуты,
// и всё это время человек видел только крутящийся кружок.
const SGO_SLOW_MS = 8000;
const SGO_STUCK_MS = 20000;

// Запросы, на которые сервер бота отвечает сам, без «Сетевого города»: их
// простой школьного сервера не касается — ни подсказок, ни обрыва.
const OWN_PATHS = [
  '/api/lock/', '/api/sgo/', '/api/account', '/api/profile', '/api/prefs', '/api/visit', '/api/notices/',
  '/api/push/', '/api/family', '/api/devices', '/api/chat', '/api/notes', '/api/history',
  '/api/attachment-ticket', '/api/settings/', '/api/bug-report', '/api/assistant', '/api/view',
];
const touchesSgo = (path) => !OWN_PATHS.some((prefix) => path.startsWith(prefix));

// Сказали человеку, что «Сетевой город» недоступен (sgo.js), — дальше его не
// ждём: идущие запросы к нему обрываются, а пару минут новые не просят
// обновления (сервер бота отдаёт сохранённое сразу) и ждут не дольше 10 с.
// Раньше после карточки «недоступен» загрузка всё равно шла ещё минуту.
const SGO_DOWN_MS = 120000;
const SGO_DOWN_WAIT_MS = 10000;
let sgoDownUntil = 0;
const sgoRequests = new Set();

export const sgoDown = () => Date.now() < sgoDownUntil;

export function markSgoDown() {
  sgoDownUntil = Date.now() + SGO_DOWN_MS;
  for (const controller of sgoRequests) {
    controller.sgoDown = true;
    controller.abort();
  }
  sgoRequests.clear();
}

const withoutRefresh = (path) => path.replace(/([?&])refresh=1(&|$)/, (_, lead, tail) => (tail ? lead : ''));

function sgoHint(failed = false, slow = false) {
  if (demo) return;
  import('./sgo.js').then((module) => module.checkServer({ failed, slow })).catch(() => {});
}

// «Сетевой город» не пускает с сохранённым паролем — скорее всего, его
// сменили. Сам вход не повторить: спрашиваем, войти заново или остаться
// с сохранёнными данными. «Продолжить так» — до закрытия приложения.
const LOGIN_FAILED_KEY = 'ns-login-failed-dismissed';

function loginFailedNotice() {
  if ($('loginFailed') || $('accountScreen')) return;
  try { if (sessionStorage.getItem(LOGIN_FAILED_KEY)) return; } catch { /* приватный режим */ }
  const el = document.createElement('div');
  el.id = 'loginFailed';
  el.className = 'banner error login-failed';
  el.setAttribute('role', 'alert');
  el.innerHTML = `<p>Не удалось войти в «Сетевой город». Возможно, сменился пароль или школа.</p>
    <div class="login-failed-actions">
      <button class="btn" data-sgo-relogin>Войти заново</button>
      <button class="btn outline" data-act="continue">Продолжить так</button>
    </div>`;
  el.querySelector('[data-act="continue"]').addEventListener('click', () => {
    try { sessionStorage.setItem(LOGIN_FAILED_KEY, '1'); } catch { /* приватный режим */ }
    el.remove();
  });
  document.body.append(el);
}

/** Войти в «Сетевой город» заново: кнопка в уведомлении и в карточке ошибки. */
async function relogin() {
  $('loginFailed')?.remove();
  const [{ showSgoLogin }, info] = await Promise.all([
    import('./account.js'), request('/api/account').then((r) => r.data).catch(() => ({})),
  ]);
  showSgoLogin(info);
}

document.addEventListener('click', (event) => {
  if (event.target.closest?.('[data-sgo-relogin]')) relogin();
});

/** NDJSON может разрезаться посередине строки и даже UTF-8 символа. */
async function readAssistantStream(response, onDelta) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', text = '', result = null;
  const timer = setTimeout(() => reader.cancel(), 100000);
  const line = (raw) => {
    if (!raw.trim()) return;
    const event = JSON.parse(raw);
    if (result) throw new Error('Данные после конца ответа');
    if (event.type === 'delta') {
      text += event.text;
      onDelta?.(text);
    } else if (event.type === 'reset') {
      text = '';
      onDelta?.(text);
    } else if ('ok' in event) result = event;
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) {
        line(buffer.slice(0, end));
        buffer = buffer.slice(end + 1);
      }
      if (done) break;
    }
    if (buffer.trim()) line(buffer);
    if (!result) throw new Error('Ответ прервался');
    return result;
  } finally {
    clearTimeout(timer);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

/** Запрос к API с удостоверением личности и разбором ошибки. */
async function request(path, options = {}) {
  // Для FormData браузер сам ставит Content-Type с границей частей.
  const isForm = typeof FormData !== 'undefined' && options.body instanceof FormData;
  const headers = { ...(isForm ? {} : { 'Content-Type': 'application/json' }), ...(options.headers || {}) };
  // initData подписана токеном бота — сервер доверяет ей больше, чем
  // токену из ссылки, поэтому шлём оба и приоритет отдаёт он.
  headers['X-Netschool-Client'] = initData ? 'telegram'
    : (window.matchMedia?.('(display-mode: standalone)').matches || navigator.standalone === true) ? 'pwa' : 'web';
  if (initData) headers['X-Telegram-Init-Data'] = initData;
  if (token) headers['X-Netschool-Token'] = token;
  if (unlockToken) headers['X-Netschool-Unlock'] = unlockToken;

  // Без предела запрос к зависшему школьному серверу крутил загрузку
  // бесконечно. Сервер сам отвечает не дольше минуты при обновлении.
  const controller = new AbortController();
  const watched = !options.method && touchesSgo(path);
  const down = watched && sgoDown();
  if (down) path = withoutRefresh(path);
  const timer = setTimeout(() => {
    if (down) controller.sgoDown = true;
    controller.abort();
  }, path.startsWith('/api/lock/') ? 10000 : down ? SGO_DOWN_WAIT_MS : REQUEST_TIMEOUT_MS);
  if (watched) sgoRequests.add(controller);
  const slow = watched && !down ? setTimeout(sgoHint, SGO_SLOW_MS) : 0;
  const stuck = watched && !down ? setTimeout(() => sgoHint(true, true), SGO_STUCK_MS) : 0;
  const { onFresh, onDelta, retried, ...init } = options;
  let response;
  try {
    response = await fetch(base + path, {
      ...init, headers, cache: 'no-store', signal: controller.signal,
    });
  } catch (error) {
    if (controller.sgoDown) {
      throw Object.assign(new Error('«Сетевой город» сейчас не отвечает — попробуйте позже.'),
        { reason: 'server', sgoDown: true });
    }
    const failure = new Error(error.name === 'AbortError'
      ? 'Сервер приложения не ответил вовремя. Проверьте интернет и повторите попытку.'
      : 'Не удалось подключиться к серверу приложения. Проверьте интернет и повторите попытку.');
    failure.reason = 'server';
    failure.source = 'app';
    throw failure;
  } finally {
    sgoRequests.delete(controller);
    clearTimeout(timer);
    clearTimeout(slow);
    clearTimeout(stuck);
  }

  let payload;
  try {
    payload = response.headers.get('Content-Type')?.includes('application/x-ndjson')
      ? await readAssistantStream(response, onDelta)
      : await response.json();
  } catch {
    throw Object.assign(new Error('Сервер приложения вернул некорректный ответ. Повторите попытку позже.'), { reason: 'server', source: 'app' });
  }
  if (!response.ok || !payload.ok) {
    if (payload.reason === 'locked' && lockedHandler && !retried && !path.startsWith('/api/lock/') &&
        await lockedHandler()) {
      return request(path, { ...options, retried: true });
    }
    const error = new Error(payload.error || `Ошибка ${response.status}`);
    error.reason = payload.reason;
    // Сохранённых данных нет — экран покажет ошибку, а уведомление даст войти.
    if (error.reason === 'auth') loginFailedNotice();
    if (error.reason === 'server' && !path.startsWith('/api/sgo/')) sgoHint(true);
    throw error;
  }
  if (response.headers.get('X-Netschool-Offline')) {
    // Ответ из копии service worker: сети нет совсем.
    showNotice(OFFLINE_TEXT);
    return { ...payload, offline: true, stale: false };
  }
  // Ответ пришёл по сети — связь вернулась.
  if (notice === OFFLINE_TEXT) banner('');
  if (dismissedNotice === OFFLINE_TEXT) dismissedNotice = '';
  return payload;
}

/** Файл с сервера (например, выгрузка оценок) с тем же удостоверением, что у API. */
export async function apiBlob(path) {
  const headers = {};
  headers['X-Netschool-Client'] = initData ? 'telegram'
    : (window.matchMedia?.('(display-mode: standalone)').matches || navigator.standalone === true) ? 'pwa' : 'web';
  if (initData) headers['X-Telegram-Init-Data'] = initData;
  if (token) headers['X-Netschool-Token'] = token;
  if (unlockToken) headers['X-Netschool-Unlock'] = unlockToken;
  let response;
  try {
    response = await fetch(base + path, { headers, cache: 'no-store' });
  } catch {
    throw new Error('Не удалось подключиться к серверу приложения. Проверьте интернет и повторите попытку.');
  }
  if (!response.ok) {
    let message = `Ошибка ${response.status}`;
    try { message = (await response.json()).error || message; } catch { /* не JSON */ }
    throw new Error(message);
  }
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename\*=UTF-8''([^;]+)/);
  return { data: await response.blob(), name: match ? decodeURIComponent(match[1]) : '' };
}

const withRefresh = (path) => `${path}${path.includes('?') ? '&' : '?'}refresh=1`;

/** Сверить показанные сохранённые данные со школьным сервером.
 *  Новое — отдаём в onFresh и сообщаем экранам событием netschool:fresh. */
let syncing = 0;

/** Тонкая полоса под шапкой, пока сверяем данные со школьным сервером. */
export function syncStarted() {
  syncing += 1;
  document.documentElement.classList.add('syncing');
}

export function syncFinished() {
  syncing = Math.max(0, syncing - 1);
  if (!syncing) document.documentElement.classList.remove('syncing');
}

async function revalidate(path, shown, onFresh) {
  revalidated.add(path);
  syncStarted();
  try {
    await revalidateNow(path, shown, onFresh);
  } finally {
    syncFinished();
  }
}

async function revalidateNow(path, shown, onFresh) {
  let fresh;
  try {
    fresh = await request(withRefresh(path));
  } catch (error) {
    revalidated.delete(path);
    staleBanner(true, error.reason, error.source);
    return;
  }
  // Сервер ещё ждёт школу (refreshing): обновление идёт у него в фоне и
  // скоро ляжет в кэш. Раньше на этом сдавались — новое появлялось только
  // после ручного обновления жестом. Переспрашиваем без refresh=1: свежий
  // кэш сервер отдаёт сразу, а новое обновление не запускает.
  for (let attempt = 0; fresh.stale && fresh.refreshing && attempt < 4; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 3000 * (attempt + 1)));
    try {
      fresh = await request(path);
    } catch {
      break;
    }
  }
  // Школьный сервер не ответил — на экране остаются сохранённые данные.
  if (fresh.stale || fresh.offline) {
    revalidated.delete(path);
    staleBanner(true, fresh.reason);
    // Не дождались свежего и без причины (сервер всё ещё ждал школу) — это
    // тоже «школа не ответила»: пусть sgo.js скажет, что с сервером.
    if (fresh.stale && fresh.reason !== 'server' && fresh.reason !== 'auth') sgoHint(true);
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

  // offline — копия из service worker: при запуске на iPhone сеть
  // просыпается дольше его ожидания, и он отдаёт сохранённое. Такую копию
  // тоже сверяем: refresh=1 он всегда отправляет в сеть.
  const outdated = payload.stale || payload.offline || (payload.age ?? 0) > REVALIDATE_AFTER_S;
  const canRevalidate = !options.method && !path.includes('refresh=1') && !revalidated.has(path) && !sgoDown();
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
  // Обновление жестом: сервер ждал школу минуту и так и не дождался
  // (refreshing) — для человека это та же неудача, а не «всё хорошо».
  if (payload.stale && (!payload.refreshing || path.includes('refresh=1'))) staleBanner(true, payload.reason || 'server');
  else if ('data' in payload && !payload.stale) staleBanner(false);
  return payload;
}

/** Открыт дневник ученика, пригласившего на просмотр (viewing.py): только
 * чтение. Класс ставит boot.js по /api/account ещё до загрузки экранов. */
export function isReadOnly() {
  return document.documentElement.classList.contains('view-mode');
}

/** Попытка что-то изменить при просмотре: подсказка вместо запроса. */
export function refuseReadOnly() {
  if (!isReadOnly()) return false;
  banner('Это просмотр дневника ученика — менять в нём ничего нельзя', true);
  return true;
}

// Билет на вложения (см. attachment_ticket в api.py): ссылка открывается во
// внешнем браузере, куда не передать ни initData, ни заголовок разблокировки.
let attachmentTicket = '';
export async function loadAttachmentTicket() {
  if (demo) return;
  try {
    attachmentTicket = (await api('/api/attachment-ticket')).data.ticket;
  } catch {
    /* без билета — прежняя ссылка */
  }
}

/** Билет есть или его удалось получить — внешний браузер откроет ссылку. */
export async function ensureAttachmentTicket() {
  if (!attachmentTicket) await loadAttachmentTicket();
  return Boolean(attachmentTicket);
}

/** Ссылка на вложение: браузер качает её сам, заголовок туда не положить. */
export function attachmentUrl(id, name = '') {
  // Имя нужно серверу для заголовка: без него файл сохранялся как
  // «attachment-123» без расширения и не открывался как docx или pdf.
  const filename = name ? `&name=${encodeURIComponent(name)}` : '';
  return fileUrl(`attachment/${id}`, filename);
}

/** Отчёты в Excel: та же страница ожидания и тот же билет, что у вложений. */
export function exportUrl(scope, report = 'grades') {
  return fileUrl(`export/${scope}`, `&report=${encodeURIComponent(report)}`);
}

function fileUrl(path, extra) {
  if (demo) return '#';
  const credential = attachmentTicket
    ? `ticket=${encodeURIComponent(attachmentTicket)}`
    : initData
      ? `tgWebAppData=${encodeURIComponent(initData)}`
      : `token=${encodeURIComponent(token)}`;
  // Страница ожидания, а не сам файл: она показывает загрузку и ошибку,
  // пока перегруженный сервер школы отдаёт вложение.
  const host = attachmentHost();
  // via=pwa: страница вложения подскажет нажать компас «Открыть в Safari».
  const via = host ? '&via=pwa' : '';
  return `${host}${base}/${path}?${credential}${extra}${via}`;
}

// В установленном на iPhone приложении (PWA) ссылка на свой домен
// открывается внутри него, а там pptx и docx не показать — только
// «Открыть в…». Ссылку на другой домен того же сервера iOS открывает во
// встроенном Safari: он предлагает просмотреть или сохранить файл, а
// компас переносит в обычный Safari. Билет на вложения к домену не привязан.
// Только iPhone и iPad: на Android Chrome и в установленном приложении
// сам скачивает файл и предлагает открыть, а во вкладке другого домена
// компаса нет — подсказка про него только запутала бы.
function attachmentHost() {
  const standalone = window.matchMedia?.('(display-mode: standalone)').matches || navigator.standalone === true;
  // iPad с iPadOS 13 представляется Mac-ом, но у Mac нет сенсорного экрана.
  const apple = /iPhone|iPad|iPod/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
  if (!standalone || !apple) return '';
  const hosts = window.NETSCHOOL.attachmentHosts || [];
  return hosts.find((host) => host !== location.origin) || '';
}

export function attachmentsHtml(items) {
  if (!items || !items.length) return '';
  return items
    .map(
      (item) =>
        `<a class="attach" href="${attachmentUrl(item.id, item.name)}" data-attach="${esc(item.id)}" data-name="${esc(item.name)}" target="_blank" rel="noopener">${ICONS.clip} ${esc(item.name)}</a>`
    )
    .join('');
}

export function errorCard(error, retryId) {
  const needsLogin = error.reason === 'login' || error.reason === 'auth';
  return `<div class="card error-card">
      <p>${esc(error.message)}</p>
      ${needsLogin ? '<button class="btn outline" data-sgo-relogin>Войти заново</button>' : ''}
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

/** «1 урок», «2 урока», «5 уроков». */
export function plural(count, one, few, many) {
  if (count % 100 >= 11 && count % 100 <= 14) return many;
  const last = count % 10;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
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

/** Крутилка на вложении, пока файл качается: школьный сервер отдаёт его
 *  секундами, и без неё было непонятно, нажалось или зависло. */
function attachBusy(link, busy) {
  link.classList.toggle('loading', busy);
  link.setAttribute('aria-busy', String(busy));
}

// Вложения открываются во внешнем браузере: там их можно посмотреть, сохранить
// и переслать. Внутри Telegram — через openLink, иначе он открыл бы файл в
// своём окне; в установленном приложении — обычной ссылкой в новой вкладке.
document.addEventListener('click', async event => {
  const link = event.target.closest('a.attach');
  if (!link) return;
  if (demo) {
    event.preventDefault();
    banner('В демо вложения не открываются', false, 2000);
    return;
  }
  if (link.classList.contains('loading')) {
    event.preventDefault();
    return;
  }
  attachBusy(link, true);
  const done = () => attachBusy(link, false);
  document.addEventListener('visibilitychange', done, { once: true });
  setTimeout(done, 8000);
  if (!attachmentTicket && (unlockToken || initData)) {
    // Без билета ссылка во внешнем браузере не откроется: сначала берём его.
    event.preventDefault();
    await loadAttachmentTicket();
    if (!attachmentTicket) {
      done();
      banner('Не удалось открыть вложение', true);
      return;
    }
    openExternal(attachmentUrl(link.dataset.attach, link.dataset.name));
    return;
  }
  // Билет мог прийти позже, чем нарисовали ссылку, — адрес берём свежий.
  if (link.dataset.attach) link.href = attachmentUrl(link.dataset.attach, link.dataset.name);
  if (initData && tg?.openLink) {
    event.preventDefault();
    openExternal(link.href);
  }
});

export function openExternal(url) {
  const absolute = new URL(url, location.href).href;
  if (initData && tg?.openLink) tg.openLink(absolute, { try_instant_view: false });
  else window.open(absolute, '_blank', 'noopener');
}
