// Один WebSocket на приложение; HTTP остаётся источником данных. По нему
// приходят новые оценки (diary), изменения с других устройств (sync,
// app/web/sync.py) и завершение этого сеанса (signed-out).
import { api, base, demo, tabId } from './core.js';

export const liveEvents = new EventTarget();
let socket = null;
let connecting = false;
let started = false;
let retry = 0;
let failures = 0;

function emit(detail) {
  liveEvents.dispatchEvent(new CustomEvent('change', { detail }));
}

async function connect() {
  if (demo || document.hidden || connecting || socket) return;
  connecting = true;
  try {
    const { data } = await api('/api/live/ws-ticket', { method: 'POST', body: '{}' });
    const url = new URL(`${base}/api/live/ws`, location.origin);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('ticket', data.ticket);
    url.searchParams.set('tab', tabId);
    const ws = new WebSocket(url);
    socket = ws;
    ws.onopen = () => {
      failures = 0;
      emit({ t: 'connected' });
    };
    ws.onmessage = (frame) => {
      let event;
      try { event = JSON.parse(frame.data); } catch { return; }
      if (event.t === 'signed-out') { signedOutHere(); return; }
      emit(event);
    };
    ws.onclose = () => { socket = null; scheduleReconnect(); };
    ws.onerror = () => ws.close();
  } catch { scheduleReconnect(); }
  finally { connecting = false; }
}

/** Сеанс завершили с другого устройства: убрать сохранённое и выйти. */
async function signedOutHere() {
  clearTimeout(retry);
  try {
    const { clearOffline } = await import('./lock.js');
    await clearOffline();
  } catch { /* перезагрузка всё равно покажет вход */ }
  location.replace(`${base}/`);
}

function scheduleReconnect() {
  clearTimeout(retry);
  retry = setTimeout(connect, Math.min(30000, 1000 * 2 ** Math.min(failures++, 5)));
}

export function initLive() {
  if (started) return;
  started = true;
  connect();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) connect(); });
  window.addEventListener('online', connect);
}
