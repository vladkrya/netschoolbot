// Сервер «Сетевого города» школы не отвечает: дневник грузится долго или
// школа не ответила. Говорим об этом прямо и предлагаем сообщить, когда
// заработает — в Telegram и push на все устройства (status/alerts.py).
// Здесь же — статус серверов внутри приложения.

import { $, api, banner, base, demo, esc } from './core.js';
import { tap } from './haptics.js';
import { pushScreen } from './screens.js';

const DISMISSED_KEY = 'ns-sgo-down-dismissed';
// «Долго грузится» спрашиваем не чаще раза в минуту. Настоящая неудача
// запроса к школе — всегда: раньше её глушила недавняя проверка «долго»,
// которая простоя ещё не видела, и карточка так и не появлялась.
const SLOW_EVERY_MS = 60000;
let slowCheckedAt = 0;
let checking = null;

const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });

function dismissed() {
  try { return Boolean(sessionStorage.getItem(DISMISSED_KEY)); } catch { return false; }
}

/** Спросить сервер бота, что со «Сетевым городом» школы, и показать
 *  карточку. failed — запрос к школе уже не удался: тогда карточка
 *  показывается, что бы ни говорили проверки, — дневник не загрузился. */
export async function checkServer({ failed = false, slow = false } = {}) {
  // Долгая загрузка кончилась неудачей — карточка остаётся и после неё.
  if (failed && !slow && $('sgoDown')?.dataset.slow) delete $('sgoDown').dataset.slow;
  if (demo || $('sgoDown') || dismissed()) return;
  if (!failed) {
    if (Date.now() - slowCheckedAt < SLOW_EVERY_MS) return;
    slowCheckedAt = Date.now();
  }
  if (checking) {
    const result = await checking.catch(() => null);
    if (failed && result && !$('sgoDown') && !dismissed()) show(result, slow);
    return;
  }
  checking = api('/api/sgo/server').then(({ data }) => data);
  try {
    const data = await checking;
    if (failed || data.state === 'down') show(data, slow && data.state !== 'down');
  } catch {
    /* бот тоже не ответил — об этом скажет обычная плашка */
  } finally {
    checking = null;
  }
}

function headline(data, slow) {
  const where = data.region ? ` (${esc(data.region)})` : '';
  if (data.state === 'down') {
    const minutes = data.down_since ? Math.round((Date.now() / 1000 - data.down_since) / 60) : 0;
    const since = minutes >= 10 ? ` — не отвечает с ${timeFmt.format(new Date(data.down_since * 1000))}` : '';
    return `<b>Сервер «Сетевого города»${where} сейчас недоступен</b>${esc(since)}.`;
  }
  return `<b>Сервер «Сетевого города»${where} ${slow ? 'долго не отвечает' : 'сейчас не отвечает'}.</b>`;
}

/** slow — запрос ещё идёт, просто долго: карточка уберётся сама, если
 *  данные всё же придут (core.js, staleBanner). */
function show(data, slow) {
  if ($('sgoDown')) return;
  const el = document.createElement('div');
  el.id = 'sgoDown';
  if (slow) el.dataset.slow = '1';
  el.className = 'banner login-failed sgo-down';
  el.setAttribute('role', 'alert');
  el.innerHTML = `<p>${headline(data, slow)} Это не у вас и не в приложении — попробуйте зайти позже.</p>
    <p class="sgo-down-state" hidden></p>
    <div class="login-failed-actions">
      <button class="btn" data-act="notify">Сообщить, когда заработает</button>
      <button class="btn outline" data-act="close">Закрыть</button>
    </div>
    <button class="sgo-down-link" data-act="status">Статус серверов</button>`;
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
      subscribed(Boolean(result.subscribed?.up));
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
  el.querySelector('[data-act="status"]').addEventListener('click', () => {
    el.remove();
    openStatus();
  });
  banner('');  // общая плашка «не удалось подключиться» — лишняя рядом
  document.body.append(el);
}

/** Статус серверов — экраном внутри приложения, со стрелкой «назад» в
 *  дневник. Страница та же, что /status, в облегчённом виде. */
export function openStatus() {
  const body = pushScreen('Статус серверов', '«Сетевой город» по регионам');
  body.classList.add('status-body');
  const theme = document.documentElement.dataset.theme || '';
  const frame = document.createElement('iframe');
  frame.className = 'status-frame';
  frame.title = 'Статус серверов «Сетевого города»';
  frame.src = `${base}/status?embed=1${theme ? `&theme=${encodeURIComponent(theme)}` : ''}`;
  body.append(frame);
}
