// Service worker: push-уведомления и запасной показ без сети.
//
// Раньше здесь был офлайн-кэш «сначала кэш», и после обновления приложения
// у людей неделями висела старая версия. Теперь страница и данные всегда
// идут из сети, а сохранённая копия отдаётся только тогда, когда сети нет
// (метро, урезанный до белых списков мобильный интернет). Статика лежит по
// версионным путям /static/v<версия>/ — новая версия приложения означает
// новые адреса, поэтому её можно брать из кэша без риска устареть.
'use strict';

const CACHE = 'ns-offline-v2';
// Вложения и служебные запросы не храним: большие и бессмысленные без сети.
// Страницу ожидания вложения тоже: в её адресе билет, а без сети она не нужна.
const SKIP = [/\/api\/chat(?:\/|$)/, /\/api\/account(?:\/|$)/, /\/api\/sgo\//, /\/api\/lock\//, /\/(?:api\/)?attachment/, /\/api\/push\//, /\/api\/pwa-link/, /\/api\/caldav-/, /\/api\/export/, /\/api\/family/];

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil((async () => {
  for (const name of await caches.keys()) {
    if (name !== CACHE) await caches.delete(name);
  }
  await self.clients.claim();
})()));

/** Ключ кэша без `refresh=1`: принудительное обновление — тот же экран. */
function cacheKey(request) {
  const url = new URL(request.url);
  url.searchParams.delete('refresh');
  return url.toString();
}

// Сколько ждать сеть, прежде чем отдать сохранённую копию. На iPhone после
// сна сеть «просыпается» секунды, а мобильный интернет с белыми списками
// не обрывает запрос, а держит его — без предела приложение стояло на
// чёрном экране или с вечной загрузкой. Запрос с refresh=1 — сознательная
// сверка со школьным сервером, она может идти до минуты.
const PAGE_TIMEOUT_MS = 3000;
const API_TIMEOUT_MS = 6000;

// Сеть только что не ответила — следующие запросы не ждут её заново.
// Раньше каждый шаг запуска (страница, проверка защиты, дневник, профиль)
// по очереди выжидал свой таймаут, и без сети дневник открывался пустым
// по 15–20 секунд. Сеть всё равно пробуем в фоне: первый же ответ из неё
// снимает отметку.
const OFFLINE_HOLD_MS = 60000;
let offlineUntil = 0;
const seemsOffline = () => Date.now() < offlineUntil;
const markOffline = () => { offlineUntil = Date.now() + OFFLINE_HOLD_MS; };
const markOnline = () => { offlineUntil = 0; };

// Предел самому запросу в сеть. Он продолжается в фоне после того, как
// показана копия, а белые списки держат соединение минутами: зависшие
// запросы занимали соединения, и даже новые вставали за ними в очередь.
// refresh=1 — сверка со школой, сервер отвечает на неё не дольше минуты.
const NETWORK_LIMIT_MS = 20000;
const REFRESH_LIMIT_MS = 70000;

function limitedFetch(request, ms) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  // Отмена со стороны страницы тоже обрывает запрос.
  request.signal?.addEventListener('abort', () => controller.abort());
  return fetch(request, { signal: controller.signal }).finally(() => clearTimeout(timer));
}

async function networkFirst(request, event) {
  const cache = await caches.open(CACHE);
  const limit = new URL(request.url).searchParams.has('refresh') ? REFRESH_LIMIT_MS : NETWORK_LIMIT_MS;
  const network = limitedFetch(request, limit).then((response) => {
    markOnline();
    if (response.ok) cache.put(cacheKey(request), response.clone());
    return response;
  });
  const fromCache = async (error) => {
    const saved = await cache.match(cacheKey(request));
    if (!saved) throw error;
    // Помечаем, чтобы приложение сказало «нет сети, показаны сохранённые данные».
    const headers = new Headers(saved.headers);
    headers.set('X-Netschool-Offline', '1');
    return new Response(await saved.blob(), { status: saved.status, headers });
  };
  const navigate = request.mode === 'navigate';
  const wait = navigate ? PAGE_TIMEOUT_MS
    : new URL(request.url).searchParams.has('refresh') ? 0 : API_TIMEOUT_MS;
  if (!wait) return network.catch((error) => { markOffline(); return fromCache(error); });
  if (seemsOffline()) {
    event.waitUntil(network.catch(() => {}));
    try {
      return await fromCache(null);
    } catch {
      /* копии нет — ждём сеть как обычно */
    }
  }

  // Сеть или, если она медлит, сохранённая копия. Запрос из сети при этом
  // не бросаем: он обновит копию к следующему разу.
  event.waitUntil(network.catch(() => {}));
  let timer;
  const slow = new Promise((resolve) => { timer = setTimeout(resolve, wait); })
    .then(() => { markOffline(); return fromCache(null).catch(() => network); });
  try {
    return await Promise.race([network, slow]);
  } catch (error) {
    markOffline();
    return fromCache(error);
  } finally {
    clearTimeout(timer);
  }
}

const STATIC_VERSION = /\/static\/(v[^/]+)\//;
let seenVersion = null;

/** Новая версия статики — файлы прежних версий больше не понадобятся. */
async function dropOtherVersions(cache, version) {
  if (version === seenVersion) return;
  seenVersion = version;
  for (const request of await cache.keys()) {
    const match = new URL(request.url).pathname.match(STATIC_VERSION);
    if (match && match[1] !== version) await cache.delete(request);
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const saved = await cache.match(request);
  if (saved) return saved;
  const response = await limitedFetch(request, NETWORK_LIMIT_MS);
  if (response.ok) {
    await cache.put(request, response.clone());
    await dropOtherVersions(cache, new URL(request.url).pathname.match(STATIC_VERSION)[1]);
  }
  return response;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  // Запросы с X-Netschool-Unlock (включён PIN) раньше шли мимо: с PIN в
  // кэш не попадало ничего, и без сети после ввода PIN показывать было
  // нечего. Копию отдаём только без сети и только за экраном PIN.
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Проверка защиты не кэшируется, но без сети отвечаем отказом сразу:
  // приложение тогда открывает сохранённое (или PIN без сети), а не висит
  // на «Проверяем защиту…».
  if (url.pathname.endsWith('/api/lock/status')) {
    event.respondWith(seemsOffline() ? Response.error() : limitedFetch(request, NETWORK_LIMIT_MS).then(
      (response) => { markOnline(); return response; },
      (error) => { markOffline(); throw error; },
    ));
    return;
  }
  if (SKIP.some((re) => re.test(url.pathname))) return;
  if (STATIC_VERSION.test(url.pathname)) {
    event.respondWith(cacheFirst(request));
  } else if (request.mode === 'navigate' || url.pathname.includes('/api/')) {
    event.respondWith(networkFirst(request, event));
  }
});

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { title: 'Сетевой город', body: event.data ? event.data.text() : '' };
  }
  event.waitUntil(
    self.registration.showNotification(payload.title || 'Сетевой город', {
      body: payload.body || '',
      icon: '/static/icon-192.png',
      badge: '/static/icon-192.png',
      data: { url: payload.url || '/', kind: payload.kind || '' },
      // Свой tag у каждого: иначе новое уведомление заменяло бы прежнее.
      tag: `${payload.kind || 'note'}-${Date.now()}`,
      // Без общего tag: с ним каждое уведомление заменяло предыдущее, и из
      // «оценки + задания» на экране оставалось только последнее.
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      const existing = clients.find((client) => 'focus' in client);
      return existing ? existing.focus() : self.clients.openWindow(target);
    })
  );
});
