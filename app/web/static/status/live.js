// Общий публичный канал статуса: только сигнал об изменении, без аккаунтов.
export function watchStatus(base, onChange, onConnection = () => {}) {
  let socket = null, retry = 0, pending = 0, failures = 0, stopped = false;
  const changed = () => {
    if (pending) return;
    pending = setTimeout(() => { pending = 0; if (!stopped && !document.hidden) onChange(); }, 1000);
  };
  const connect = () => {
    clearTimeout(retry);
    if (stopped || document.hidden || socket || typeof WebSocket === 'undefined') return;
    const url = new URL(`${base}/status/ws`, location.origin);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(url);
    socket = ws;
    ws.onopen = () => { failures = 0; onConnection(true); };
    ws.onmessage = ({ data }) => {
      try { if (JSON.parse(data).t === 'status') changed(); } catch { /* неверный кадр */ }
    };
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      if (socket === ws) socket = null;
      onConnection(false);
      if (!stopped && !document.hidden) retry = setTimeout(connect, Math.min(30000, 1000 * 2 ** Math.min(failures++, 5)));
    };
  };
  const visibility = () => {
    if (document.hidden) { clearTimeout(retry); socket?.close(); }
    else { connect(); changed(); }
  };
  const online = () => { connect(); changed(); };
  document.addEventListener('visibilitychange', visibility);
  window.addEventListener('online', online);
  connect();
  return () => {
    stopped = true;
    clearTimeout(retry); clearTimeout(pending);
    document.removeEventListener('visibilitychange', visibility);
    window.removeEventListener('online', online);
    socket?.close();
  };
}
