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

/** Ключ кэша без `refresh=1` и `update=`: принудительное обновление и
 *  перезапуск на новую версию — тот же экран. */
function cacheKey(request) {
  const url = new URL(request.url);
  url.searchParams.delete('refresh');
  url.searchParams.delete('update');
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
// Приложение достучалось до сервера мимо worker (проверка связи, core.js).
self.addEventListener('message', (event) => { if (event.data === 'online') markOnline(); });

// Последняя открытая страница приложения — под одним ключом, какой бы адрес
// ни открыли. Раньше копия бралась только по точному адресу: иконка «Домой»,
// ссылка с токеном или ?install=1 открывали другой адрес, и без сети
// показывалось «Сохранённых данных нет», хотя дневник на устройстве был.
const SHELL_KEY = new URL('./__app', self.registration.scope).href;
const isShell = (url) => url.pathname === new URL(self.registration.scope).pathname;

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

// Ответ не от приложения, а от nginx или Cloudflare: до приложения не
// достучались (перезапуск при выкладке, обрыв туннеля). Свои 5xx приложение
// отдаёт JSON-ом — они проходят как есть.
const DOWN_STATUSES = [502, 503, 504, 520, 521, 522, 523, 524];
const appDown = (response) => response.headers.get('X-Netschool-Down') === '1'
  || (DOWN_STATUSES.includes(response.status) && !(response.headers.get('Content-Type') || '').includes('application/json'));

const ICON_OFFLINE = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M2 8.8a15 15 0 0 1 20 0M5.5 12.3a10 10 0 0 1 13 0M9 15.8a5 5 0 0 1 6 0"/><path d="M12 19.5h.01"/><path d="m3 3 18 18"/></svg>';
const ICON_UPDATING = '<svg class="spin" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 4v5h-5"/></svg>';

// Страница, когда сохранённой копии нет (её стёрли после неверных PIN или
// дневник ещё не открывали): без сети — «Нет подключения», при перезапуске
// сервера — «Обновляем приложение» (как deploy/nginx/netschool-updating.html).
// Иначе Safari показывал голую ошибку «FetchEvent.respondWith received an
// error», а при выкладке — «502 Bad Gateway». Сервер ответил — перезагрузка.
const statusPage = (icon, title, text, button) => `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>${title} — Дневник</title>
<style>
:root{--bg:#f5f6f8;--text:#202733;--muted:#6b7280;--accent:#3b82f6}
@media (prefers-color-scheme:dark){:root{--bg:#000;--text:#f2f3f5;--muted:#9aa0aa;--accent:#5b9cff}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);
font:16px/1.45 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;text-align:center;padding:24px;box-sizing:border-box}
main{max-width:340px}.icon{width:56px;height:56px;margin:0 auto 16px;border-radius:18px;display:grid;place-items:center;
color:var(--accent);background:color-mix(in srgb,var(--accent) 14%,transparent)}
.spin{animation:spin 1.6s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.spin{animation:none}}
h1{font-size:23px;font-weight:650;letter-spacing:-.4px;margin:0 0 8px}p{margin:0 0 22px;color:var(--muted)}
button{border:0;border-radius:14px;padding:13px 26px;font:inherit;font-weight:600;color:#fff;background:var(--accent)}
</style></head><body><main>
<div class="icon">${icon}</div>
<h1>${title}</h1>
<p>${text}</p>
<button type="button" onclick="location.reload()">${button}</button>
</main><script>
addEventListener('online',()=>location.reload());
setInterval(()=>fetch(${JSON.stringify(new URL('./sw.js', self.registration.scope).pathname)},{method:'HEAD',cache:'no-store'})
  .then((r)=>{if(r.ok)location.reload()},()=>{}),5000);
</script></body></html>`;
const OFFLINE_PAGE = statusPage(ICON_OFFLINE, 'Нет подключения к интернету',
  'Сохранённых данных на этом устройстве нет. Чтобы открыть дневник, подключитесь к сети.', 'Повторить');
const UPDATING_PAGE = statusPage(ICON_UPDATING, 'Обновляем приложение',
  'Через минуту всё заработает. Страница откроется сама, как только сервер ответит.', 'Перезапустить');
const offlinePage = (down) => new Response(down ? UPDATING_PAGE : OFFLINE_PAGE, {
  status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store', 'X-Netschool-Offline': down ? 'down' : '1' },
});

async function networkFirst(request, event) {
  const cache = await caches.open(CACHE);
  const limit = new URL(request.url).searchParams.has('refresh') ? REFRESH_LIMIT_MS : NETWORK_LIMIT_MS;
  const navigate = request.mode === 'navigate';
  const network = limitedFetch(request, limit).then((response) => {
    // Приложение перезапускается — как без сети: сохранённое или «Обновляем».
    if (appDown(response)) throw Object.assign(new Error('Приложение перезапускается'), { response });
    markOnline();
    if (response.ok) {
      cache.put(cacheKey(request), response.clone());
      if (navigate && isShell(new URL(request.url))) cache.put(SHELL_KEY, response.clone());
    }
    return response;
  });
  const fromCache = async (error) => {
    const saved = await cache.match(cacheKey(request))
      || (navigate && isShell(new URL(request.url)) ? await cache.match(SHELL_KEY) : undefined);
    const down = Boolean(error?.response);
    if (!saved && navigate && error) return offlinePage(down);
    // Копии нет — отдаём ответ nginx как есть: приложение по нему скажет, что обновляется.
    if (!saved && down) return error.response;
    if (!saved) throw error;
    // Помечаем, чтобы приложение сказало «нет сети, показаны сохранённые данные»
    // или «приложение обновляется».
    const headers = new Headers(saved.headers);
    headers.set('X-Netschool-Offline', down ? 'down' : '1');
    return new Response(await saved.blob(), { status: saved.status, headers });
  };
  // Перезапуск на новую версию (update.js) ждёт сеть: через 3 секунды
  // отдавалась старая копия страницы, а с ней и старая версия приложения.
  const params = new URL(request.url).searchParams;
  const wait = params.has('refresh') || (navigate && params.has('update')) ? 0
    : navigate ? PAGE_TIMEOUT_MS : API_TIMEOUT_MS;
  if (!wait) return network.catch((error) => { markOffline(); return fromCache(error); });
  if (seemsOffline() && wait) {
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
      (response) => {
        // Перезапуск сервера — для приложения то же, что нет сети.
        if (appDown(response)) { markOffline(); return Response.error(); }
        markOnline();
        return response;
      },
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
