// Web push на этом устройстве: подписка, переключатель и первый запуск.
'use strict';

import { $, api, banner, demo, initData, pushEnabled, state } from './core.js';
import { notify, select } from './haptics.js';
import { canInstall, wantsInstall } from './install.js';

/** Где возможен web push: сайт в браузере, на iPhone — только с экрана «Домой». */
function pushAvailability() {
  if (initData) return 'telegram';
  if (!pushEnabled || !('serviceWorker' in navigator) || !('PushManager' in window)) return 'none';
  const iphone = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const installed = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  return iphone && !installed ? 'install' : 'ready';
}

export function pushRow() {
  const where = pushAvailability();
  if (where === 'install') {
    return `<div class="muted small">Пуш на iPhone работает в приложении с экрана «Домой».</div>
        <button class="btn outline" id="installGuide">Как установить</button>`;
  }
  const install = canInstall()
    ? '<button class="btn outline" id="installGuide">Установить на экран «Домой»</button>' : '';
  if (where === 'ready') {
    return `<label class="switch-row"><span>Пуш на этом устройстве</span>
        <input type="checkbox" switch class="switch" id="pushToggle" disabled></label>${install}`;
  }
  return install;
}

export function pushHowto() {
  // Внутри Telegram web push недоступен: уведомления браузера работают только
  // у сайта, открытого в самом браузере или установленного на экран «Домой».
  if (pushAvailability() !== 'telegram' || !wantsInstall()) return '';
  return `<section class="drawer-section" data-install-hint>
      <div class="drawer-heading">Приложение на экране «Домой»</div>
      <ol class="howto">
        <li>Нажмите «Открыть в браузере» — откроется ваша личная ссылка.</li>
        <li><b>iPhone:</b> в Safari «Поделиться» → «На экран Домой». <b>Android:</b> в Chrome ⋮ → «Установить приложение».</li>
        <li>Запустите дневник с иконки — он сам спросит про уведомления.</li>
      </ol>
      <button class="btn outline" id="pwaOpen">Открыть в браузере</button>
    </section>`;
}
/**
 * Тот же адрес worker, что в registerWorker (app.js): другой адрес ставил бы
 * worker заново при каждом вызове. Подписаться на push можно только у
 * активного worker — ждём его, иначе на первом запуске subscribe() падал
 * и переключатель «Пуш на этом устройстве» не включался.
 */
async function workerRegistration() {
  const { base } = window.NETSCHOOL;
  await navigator.serviceWorker.register(`${base}/sw.js`, { scope: `${base}/` });
  return navigator.serviceWorker.ready;
}

async function currentSubscription() {
  try {
    return await (await workerRegistration()).pushManager.getSubscription();
  } catch {
    return null;
  }
}

/**
 * Первый запуск с экрана «Домой»: сразу спросить разрешение на уведомления
 * и подписать устройство на пуш. iOS показывает запрос только в ответ на
 * нажатие — если без него не вышло, спросим при первом касании экрана.
 */
export function offerPushOnFirstLaunch() {
  const installed = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  if (demo || !installed || pushAvailability() !== 'ready') return;
  // Пуш включён по умолчанию: разрешение уже есть, а подписки нет (например,
  // приложение переустановили) — подписываемся, если сами не выключали.
  if (Notification.permission === 'granted') {
    if (!pushTurnedOff()) currentSubscription().then((found) => found || subscribePush()).catch(() => {});
    return;
  }
  if (Notification.permission !== 'default') return;
  try {
    if (localStorage.getItem('pushAsked')) return;
  } catch { /* без хранилища просто спросим */ }
  const ask = async () => {
    try {
      if ((await Notification.requestPermission()) === 'default') return false;
      try { localStorage.setItem('pushAsked', '1'); } catch { /* ничего */ }
      if (Notification.permission === 'granted') await subscribePush();
    } catch { /* откажет — включат в настройках */ }
    return true;
  };
  ask().then((answered) => {
    if (answered) return;
    const onTap = () => {
      document.removeEventListener('click', onTap, true);
      ask();
    };
    document.addEventListener('click', onTap, true);
  });
}

const PUSH_OFF_KEY = 'pushOff';

function pushTurnedOff() {
  try { return localStorage.getItem(PUSH_OFF_KEY) === '1'; } catch { return false; }
}

/** Подсказать, если выключены оба канала. */
export function syncNowhere(pushOn) {
  const hint = $('notifyNowhere');
  if (!hint) return;
  const telegramOn = state.profile?.notifications?.telegram !== false;
  hint.hidden = telegramOn || pushOn;
}

export async function bindPushToggle() {
  const input = $('pushToggle');
  if (!input) {
    syncNowhere(false);
    return;
  }
  const subscription = await currentSubscription();
  input.checked = Boolean(subscription) && Notification.permission === 'granted';
  input.disabled = false;
  syncNowhere(input.checked);
  input.addEventListener('change', async () => {
    select();
    input.disabled = true;
    try {
      if (input.checked) await subscribePush();
      else await unsubscribePush();
      try {
        if (input.checked) localStorage.removeItem(PUSH_OFF_KEY);
        else localStorage.setItem(PUSH_OFF_KEY, '1');
      } catch { /* ничего */ }
      notify('success');
    } catch (error) {
      notify('error');
      input.checked = !input.checked;
      banner(error.message, true);
    } finally {
      input.disabled = false;
      syncNowhere(input.checked);
    }
  });
}

async function subscribePush() {
  if ((await Notification.requestPermission()) !== 'granted') {
    throw new Error('Уведомления запрещены в настройках браузера');
  }
  const registration = await workerRegistration();
  const { data } = await api('/api/push/key');
  const subscription = await registration.pushManager.getSubscription()
    || await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(data.key),
    });
  await api('/api/push/subscribe', {
    method: 'POST',
    body: JSON.stringify(subscription.toJSON()),
  });
}
export async function unsubscribePush() {
  const subscription = await currentSubscription();
  if (!subscription) return;
  await api('/api/push/unsubscribe', {
    method: 'POST',
    body: JSON.stringify({ endpoint: subscription.endpoint }),
  });
  await subscription.unsubscribe();
}

function urlBase64ToUint8Array(value) {
  const padding = '='.repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((char) => char.charCodeAt(0)));
}
