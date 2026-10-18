// Сервер «Сетевого города» школы не отвечает: дневник грузится долго или
// школа не ответила. Говорим об этом прямо и предлагаем сообщить, когда
// заработает — в Telegram и push на все устройства (status/alerts.py).
// Здесь же — статус серверов внутри приложения.

import { $, api, banner, base, demo, esc, markSgoDown } from './core.js';
import { notify as haptic, tap } from './haptics.js';
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
    if (!failed && data.state !== 'down') return;
    show(data, slow && data.state !== 'down');
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
  // Дневник не загрузился, а проверка страницы входа прошла: сервер жив, но
  // журнал отдаёт медленно. Раньше здесь было «долго не отвечает», а в
  // статусе тот же сервер — «отвечает», и одно противоречило другому.
  if (data.state === 'unstable') return `<b>Сервер «Сетевого города»${where} работает с перебоями</b> — дневник сейчас грузится очень долго.`;
  if (slow && data.ok) return `<b>Сервер «Сетевого города»${where} отвечает очень медленно</b> — дневник грузится дольше обычного.`;
  return `<b>Сервер «Сетевого города»${where} ${slow ? 'долго не отвечает' : 'сейчас не отвечает'}.</b>`;
}

function dismiss() {
  try { sessionStorage.setItem(DISMISSED_KEY, '1'); } catch { /* приватный режим */ }
}

/** Карточка «недоступен»: полупрозрачная, сверху, с маленькими кнопками.
 *  Показали — дальше школу не ждём (markSgoDown): загрузка не крутится
 *  после того, как человеку уже сказали, что ждать бесполезно. */
function show(data, slow) {
  if ($('sgoDown')) return;
  markSgoDown();
  const el = document.createElement('div');
  el.id = 'sgoDown';
  el.className = 'sgo-down';
  el.setAttribute('role', 'alert');
  // Уже попросил сообщить — второй раз не предлагаем: только говорим, что
  // сервер лежит и что сообщим, и даём закрыть.
  if (data.subscribed?.up) {
    el.innerHTML = `<p class="sgo-down-text">${headline(data, slow)} Мы сообщим, когда заработает.</p>
      <div class="sgo-down-actions">
        <button class="sgo-down-btn primary" data-act="close">Закрыть</button>
      </div>`;
  } else {
    el.innerHTML = `<button class="sgo-down-x" data-act="close" aria-label="Закрыть">×</button>
      <p class="sgo-down-text">${headline(data, slow)} Попробуйте зайти позже.</p>
      <div class="sgo-down-actions">
        <button class="sgo-down-btn primary" data-act="notify">Сообщить, когда заработает</button>
        <button class="sgo-down-btn" data-act="status">Статус</button>
      </div>`;
  }
  const text = el.querySelector('.sgo-down-text');
  const actions = el.querySelector('.sgo-down-actions');
  const close = () => {
    el.classList.add('leaving');
    setTimeout(() => el.remove(), 250);
  };
  const notify = el.querySelector('[data-act="notify"]');
  const label = notify?.textContent;
  notify?.addEventListener('click', async () => {
    tap();
    // После await event.currentTarget уже null — раньше на ошибке это падало,
    // и кнопка оставалась тёмной без единого слова.
    notify.disabled = true;
    notify.textContent = 'Подписываем…';
    try {
      await api('/api/sgo/notify', { method: 'POST', body: JSON.stringify({ kind: 'up', on: true }) });
      haptic('success');
      // Подписались — карточка больше не нужна и до конца запуска не вернётся.
      dismiss();
      actions.remove();
      text.innerHTML = '✅ <b>Готово.</b> Сообщим в Telegram и уведомлением, когда дневник снова загрузится.';
      setTimeout(close, 3500);
    } catch (error) {
      haptic('error');
      notify.disabled = false;
      notify.textContent = label;
      text.textContent = `Не удалось подписаться: ${error.message}`;
    }
  });
  el.querySelector('[data-act="close"]').addEventListener('click', () => {
    dismiss();
    close();
  });
  el.querySelector('[data-act="status"]')?.addEventListener('click', () => {
    dismiss();
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
