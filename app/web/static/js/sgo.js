// Сервер «Сетевого города» школы не отвечает: дневник грузится долго или
// школа не ответила. Говорим об этом прямо и предлагаем сообщить, когда
// заработает — в Telegram и push на все устройства (status/alerts.py).
// Здесь же — статус серверов внутри приложения.

import { $, api, banner, base, demo, esc, markSgoDown, openExternal, sgoNoticeShown } from './core.js';
import { notify as haptic, tap } from './haptics.js';
import { onScreenRefresh, pushScreen } from './screens.js';
import { watchStatus } from '../status/live.js';

const DISMISSED_KEY = 'ns-sgo-down-dismissed';
// «Долго грузится» спрашиваем не чаще раза в минуту. Настоящая неудача
// запроса к школе — всегда: раньше её глушила недавняя проверка «долго»,
// которая простоя ещё не видела, и карточка так и не появлялась.
const SLOW_EVERY_MS = 60000;
let slowCheckedAt = 0;
let checking = null;
let recoveryGeneration = 0;
let stopLive = null;
let lastRecoveryCheck = 0;

function stopRecovery() {
  stopLive?.();
  stopLive = null;
}

function retryDiary() {
  return new Promise((done) => document.dispatchEvent(new CustomEvent('netschool:refresh', { detail: { done } })));
}

function watchRecovery() {
  if (stopLive) return;
  lastRecoveryCheck = 0;
  stopLive = watchStatus(base, async () => {
    if (!$('sgoDown') || Date.now() - lastRecoveryCheck < 30000) return;
    lastRecoveryCheck = Date.now();
    try {
      const { data } = await api('/api/sgo/server');
      if (data.ok && $('sgoDown')) await retryDiary();
    } catch { /* следующий круг мониторинга повторит попытку */ }
  });
}

document.addEventListener('netschool:sgo-up', () => {
  recoveryGeneration += 1;
  stopRecovery();
  slowCheckedAt = 0;
  $('sgoDown')?.remove();
  try { sessionStorage.removeItem(DISMISSED_KEY); } catch { /* приватный режим */ }
});

const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });
const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

function dismissed() {
  try { return Boolean(sessionStorage.getItem(DISMISSED_KEY)); } catch { return false; }
}

/** Спросить сервер бота, что со «Сетевым городом» школы, и показать
 *  карточку. failed — запрос к школе уже не удался: тогда карточка
 *  показывается, что бы ни говорили проверки, — дневник не загрузился. */
export async function checkServer({ failed = false, slow = false } = {}) {
  const generation = recoveryGeneration;
  // Долгая загрузка кончилась неудачей — карточка остаётся и после неё.
  if (failed && !slow && $('sgoDown')?.dataset.slow) delete $('sgoDown').dataset.slow;
  if (demo || $('sgoDown') || dismissed()) return;
  if (!failed) {
    if (Date.now() - slowCheckedAt < SLOW_EVERY_MS) return;
    slowCheckedAt = Date.now();
  }
  if (checking) {
    const result = await checking.catch(() => null);
    if (generation === recoveryGeneration && failed && result && !$('sgoDown') && !dismissed()) show(result, slow);
    return;
  }
  checking = api('/api/sgo/server').then(({ data }) => data);
  try {
    const data = await checking;
    if (generation !== recoveryGeneration) return;
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
  if (data.error === 'maintenance') {
    return `<b>На сервере «Сетевого города»${where} идут технические работы.</b> Дневник временно недоступен.`;
  }
  if (data.state === 'down') {
    const minutes = data.down_since ? Math.round((Date.now() / 1000 - data.down_since) / 60) : 0;
    const date = new Date(data.down_since * 1000);
    const since = minutes >= 10 ? ` Мониторинг фиксирует текущий сбой с ${(date.toDateString() === new Date().toDateString() ? timeFmt : dateFmt).format(date)}.` : '';
    return `<b>Сервер «Сетевого города»${where} сейчас недоступен.</b>${esc(since)} Возможны технические работы или сбой.`;
  }
  if (data.account_checks?.total >= 3 && data.account_checks.good / data.account_checks.total < 0.5) {
    return `<b>Проблемы с загрузкой дневников${where}.</b> Сайт может открываться, но войти и загрузить дневник сейчас почти ни у кого не получается.`;
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
  sgoNoticeShown();
  const el = document.createElement('div');
  el.id = 'sgoDown';
  el.className = 'sgo-down';
  if (slow) el.dataset.slow = '1';
  el.setAttribute('role', 'alert');
  // Уже попросил сообщить — второй раз не предлагаем: только говорим, что
  // сервер лежит и что сообщим, и даём закрыть.
  if (data.subscribed?.up) {
    el.innerHTML = `<p class="sgo-down-text">${headline(data, slow)} Мы сообщим, когда заработает.</p>
      <div class="sgo-down-actions">
        <button class="sgo-down-btn primary" data-act="close">Закрыть</button>
        <button class="sgo-down-btn" data-act="status">Статус</button>
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
    stopRecovery();
    el.classList.add('leaving');
    setTimeout(() => el.remove(), 250);
  };
  const retry = document.createElement('button');
  retry.className = 'sgo-down-btn';
  retry.textContent = 'Проверить снова';
  retry.addEventListener('click', async () => {
    retry.disabled = true;
    retry.textContent = 'Проверяем…';
    try { await retryDiary(); }
    finally { retry.disabled = false; retry.textContent = 'Проверить снова'; }
  });
  actions.append(retry);
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
    stopRecovery();
    dismiss();
    el.remove();
    openStatus();
  });
  banner('');  // общая плашка «не удалось подключиться» — лишняя рядом
  document.body.append(el);
  watchRecovery();
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
  // Свайп вниз — перезагрузить рамку; ждём её, но не дольше 15 с.
  onScreenRefresh(body, () => new Promise((resolve) => {
    frame.addEventListener('load', resolve, { once: true });
    setTimeout(resolve, 15000);
    try { frame.contentWindow.location.reload(); } catch { frame.src = frame.src; }
  }));
}

// Ссылки из рамки статуса (адрес региона): рамка сама открыть их не может.
window.addEventListener('message', (event) => {
  if (event.origin !== location.origin || event.data?.type !== 'status-open-link') return;
  const frame = [...document.querySelectorAll('.status-frame')].find((f) => f.contentWindow === event.source);
  if (!frame || !/^https?:\/\//.test(event.data.url || '')) return;
  openExternal(event.data.url);
});
