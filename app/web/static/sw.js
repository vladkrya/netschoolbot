// Service worker: push-уведомления и запасной показ без сети.
//
// Раньше здесь был офлайн-кэш «сначала кэш», и после обновления приложения
// у людей неделями висела старая версия. Теперь страница и данные всегда
// идут из сети, а сохранённая копия отдаётся только тогда, когда сети нет
// (метро, урезанный до белых списков мобильный интернет). Статика лежит по
// версионным путям /static/v<версия>/ — новая версия приложения означает
// новые адреса, поэтому её можно брать из кэша без риска устареть.
'use strict';

const CACHE = 'ns-offline-v1';
// Вложения и служебные запросы не храним: большие и бессмысленные без сети.
const SKIP = [/\/api\/attachment\//, /\/api\/push\//, /\/api\/pwa-link/, /\/api\/caldav-/];

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

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(cacheKey(request), response.clone());
    return response;
  } catch (error) {
    const saved = await cache.match(cacheKey(request));
    if (!saved) throw error;
    // Помечаем, чтобы приложение сказало «нет сети, показаны сохранённые данные».
    const headers = new Headers(saved.headers);
    headers.set('X-Netschool-Offline', '1');
    return new Response(await saved.blob(), { status: saved.status, headers });
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
  const response = await fetch(request);
  if (response.ok) {
    await cache.put(request, response.clone());
    await dropOtherVersions(cache, new URL(request.url).pathname.match(STATIC_VERSION)[1]);
  }
  return response;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || SKIP.some((re) => re.test(url.pathname))) return;
  if (STATIC_VERSION.test(url.pathname)) {
    event.respondWith(cacheFirst(request));
  } else if (request.mode === 'navigate' || url.pathname.includes('/api/')) {
    event.respondWith(networkFirst(request));
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
      data: { url: payload.url || '/' },
      tag: 'netschool',
      renotify: true,
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
