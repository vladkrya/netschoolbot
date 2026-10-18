// Сервер «Сетевого города» школы лежит: дневник грузится долго или школа не
// ответила. Говорим об этом прямо и предлагаем сообщить, когда заработает —
// в Telegram и push на все устройства (status/alerts.py на сервере).

import { $, api, banner, demo, esc } from './core.js';
import { tap } from './haptics.js';

const DISMISSED_KEY = 'ns-sgo-down-dismissed';
const CHECK_EVERY_MS = 60000;
let checkedAt = 0;
let checking = null;

const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });

function dismissed() {
  try { return Boolean(sessionStorage.getItem(DISMISSED_KEY)); } catch { return false; }
}

/** Спросить сервер бота, что со «Сетевым городом» школы, и при простое
 *  показать карточку. failed — запрос к школе уже не удался. */
export function checkServer({ failed = false } = {}) {
  if (demo || $('sgoDown') || dismissed() || checking) return checking;
  if (Date.now() - checkedAt < CHECK_EVERY_MS) return null;
  checkedAt = Date.now();
  checking = api('/api/sgo/server')
    .then(({ data }) => {
      if (data.state === 'down' || (failed && data.state === 'unstable')) show(data);
    })
    .catch(() => { /* бот тоже не ответил — об этом скажет обычная плашка */ })
    .finally(() => { checking = null; });
  return checking;
}

function since(data) {
  const at = data.down_since || data.checked_at;
  if (!at) return '';
  const minutes = Math.round((Date.now() / 1000 - at) / 60);
  if (data.down_since && minutes >= 10) return ` — не отвечает с ${timeFmt.format(new Date(at * 1000))}`;
  return '';
}

function show(data) {
  const el = document.createElement('div');
  el.id = 'sgoDown';
  el.className = 'banner login-failed sgo-down';
  el.setAttribute('role', 'alert');
  const where = data.region ? ` (${esc(data.region)})` : '';
  el.innerHTML = `<p><b>Сервер «Сетевого города»${where} сейчас недоступен</b>${esc(since(data))}.
      Это не у вас и не в приложении — попробуйте зайти позже.</p>
    <p class="sgo-down-state" hidden></p>
    <div class="login-failed-actions">
      <button class="btn" data-act="notify">Сообщить, когда заработает</button>
      <button class="btn outline" data-act="close">Закрыть</button>
    </div>`;
  const notifyButton = el.querySelector('[data-act="notify"]');
  const stateLine = el.querySelector('.sgo-down-state');
  const subscribed = (on) => {
    stateLine.hidden = !on;
    stateLine.textContent = on ? 'Готово — пришлём в Telegram и уведомлением на все устройства, как только сервер ответит.' : '';
    notifyButton.textContent = on ? 'Не сообщать' : 'Сообщить, когда заработает';
    notifyButton.dataset.on = on ? '1' : '';
  };
  subscribed(Boolean(data.subscribed?.up));
  notifyButton.addEventListener('click', async () => {
    tap();
    const on = !notifyButton.dataset.on;
    notifyButton.disabled = true;
    try {
      const { data: result } = await api('/api/sgo/notify', { method: 'POST', body: JSON.stringify({ kind: 'up', on }) });
      if (result.already_up) {
        stateLine.hidden = false;
        stateLine.textContent = 'Сервер уже снова отвечает — обновите экран.';
        notifyButton.hidden = true;
      } else {
        subscribed(Boolean(result.subscribed?.up));
      }
    } catch (error) {
      stateLine.hidden = false;
      stateLine.textContent = error.message;
    } finally {
      notifyButton.disabled = false;
    }
  });
  el.querySelector('[data-act="close"]').addEventListener('click', () => {
    try { sessionStorage.setItem(DISMISSED_KEY, '1'); } catch { /* приватный режим */ }
    el.remove();
  });
  banner('');  // общая плашка «не удалось подключиться» — лишняя рядом
  document.body.append(el);
}
