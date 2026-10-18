// Один WebSocket на приложение; HTTP остаётся источником сообщений.
// Кроме чатов по нему приходят новые оценки (diary), изменения с других
// устройств (sync, app/web/sync.py) и завершение этого сеанса (signed-out).
import { api, base, demo, tabId } from './core.js';
import { setMailBadge } from './tabs.js';

export const chatEvents = new EventTarget();
let socket = null;
let connecting = false;
let started = false;
let eligible = false;
let openRoom = null;
let retry = 0;
let failures = 0;
let refreshing = false;
let refreshAgain = false;

export function watchRoom(room) {
  openRoom = room;
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(room ? { t: 'open', room } : { t: 'close' }));
}

export function chatConnected() { return socket?.readyState === WebSocket.OPEN; }

let typedAt = 0;
/** «Печатает…» для остальных в комнате; чаще раза в 2,5 с не шлём. */
export function sendTyping(room) {
  const now = Date.now();
  if (!chatConnected() || now - typedAt < 2500) return;
  typedAt = now;
  socket.send(JSON.stringify({ t: 'typing', room }));
}

async function connect() {
  if (demo || !eligible || document.hidden || connecting || socket) return;
  connecting = true;
  try {
    const { data } = await api('/api/chat/ws-ticket', { method: 'POST', body: '{}' });
    const url = new URL(`${base}/api/chat/ws`, location.origin);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('ticket', data.ticket);
    url.searchParams.set('tab', tabId);
    const ws = new WebSocket(url);
    socket = ws;
    ws.onopen = () => {
      failures = 0;
      watchRoom(openRoom);
      chatEvents.dispatchEvent(new CustomEvent('change', { detail: { t: 'connected' } }));
      refreshChatUnread();
    };
    ws.onmessage = (frame) => {
      let event;
      try { event = JSON.parse(frame.data); } catch { return; }
      if (event.t === 'signed-out') { signedOutHere(); return; }
      chatEvents.dispatchEvent(new CustomEvent('change', { detail: event }));
      if (['msg', 'upd', 'unread'].includes(event.t)) refreshChatUnread();
    };
    ws.onclose = () => { socket = null; scheduleReconnect(); };
    ws.onerror = () => ws.close();
  } catch { scheduleReconnect(); }
  finally { connecting = false; }
}

/** Сеанс завершили с другого устройства: убрать сохранённое и выйти. */
async function signedOutHere() {
  eligible = false;
  try {
    const { clearOffline } = await import('./lock.js');
    await clearOffline();
  } catch { /* перезагрузка всё равно покажет вход */ }
  location.replace(`${base}/`);
}

function scheduleReconnect() {
  clearTimeout(retry);
  if (!eligible) return;
  retry = setTimeout(connect, Math.min(30000, 1000 * 2 ** Math.min(failures++, 5)));
}

export async function refreshChatUnread() {
  if (refreshing) { refreshAgain = true; return; }
  refreshing = true;
  try {
    const { data } = await api('/api/chat/unread');
    setMailBadge('chat', data.total || 0);
    eligible = Boolean(data.live);
    if (eligible) connect();
    else { clearTimeout(retry); socket?.close(); }
    if (!chatConnected()) chatEvents.dispatchEvent(new CustomEvent('change', { detail: { t: 'snapshot' } }));
  } catch { /* данные останутся до восстановления сети */ }
  finally {
    refreshing = false;
    if (refreshAgain) { refreshAgain = false; refreshChatUnread(); }
  }
}

export function initChatLive() {
  if (started) return;
  started = true;
  refreshChatUnread();
  setInterval(() => { if (!document.hidden) refreshChatUnread(); }, 30000);
  document.addEventListener('visibilitychange', () => {
    watchRoom(document.hidden ? null : document.querySelector('.chat-screen:last-child')?.dataset.room || null);
    if (!document.hidden) {
      refreshChatUnread();
      chatEvents.dispatchEvent(new CustomEvent('change', { detail: { t: 'connected' } }));
    }
  });
  window.addEventListener('online', () => { refreshChatUnread(); connect(); });
}
