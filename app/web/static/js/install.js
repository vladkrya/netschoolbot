// Подсказка «установить на экран Домой» для сайта, открытого в браузере
// телефона. На Android (Chrome, Edge, Samsung) — системное окно установки
// по кнопке, на iPhone такого окна нет: показываем, куда нажимать в Safari.
'use strict';

import { $, api, banner, base, demo, initData, tg } from './core.js';
import { ICONS } from './icons.js';
import { closeSheet, openSheet } from './sheet.js';
import { getPref, setPref } from './prefs.js';
import { notify, tap } from './haptics.js';

const SEEN_KEY = 'installNudge';
// Закрыли подсказку — напомним не раньше чем через неделю, всего три раза.
const PAUSE = 7 * 24 * 3600 * 1000;
const MAX_SHOWS = 3;

let deferredPrompt = null;

const ua = navigator.userAgent;
const isIos = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
const isAndroid = /Android/.test(ua);

function installed() {
  return window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
}

/** Где открыт сайт: от этого зависят шаги установки. */
function browser() {
  if (isIos) {
    if (/CriOS|FxiOS|EdgiOS|YaBrowser|OPiOS/.test(ua)) return 'ios-other';
    // Встроенные браузеры приложений (Instagram, VK и т. п.) установку не умеют.
    if (!/Safari\//.test(ua) || /Instagram|FBAN|FBAV|VKClient|Line\//.test(ua)) return 'ios-inapp';
    return 'ios-safari';
  }
  if (isAndroid) {
    if (/; wv\)|Instagram|FBAN|FBAV|VKClient/.test(ua)) return 'android-inapp';
    if (/SamsungBrowser/.test(ua)) return 'android-samsung';
    return 'android';
  }
  return 'desktop';
}

/** Safari 26 убрал кнопку «Поделиться» в меню «•••». */
function safariVersion() {
  return Number((ua.match(/Version\/(\d+)/) || [])[1]) || 0;
}

const chip = (icon) => `<span class="install-chip">${icon}</span>`;

function steps() {
  const add = chip(ICONS.addSquare);
  switch (browser()) {
    case 'ios-safari':
      return safariVersion() >= 26
        ? [
          `Нажмите ${chip(ICONS.dots)} справа от адресной строки`,
          `Выберите ${chip(ICONS.share)} «Поделиться»`,
          `Пролистайте вниз (или «Ещё») и нажмите ${add} «На экран „Домой“»`,
          'Оставьте включённым «Открыть как веб-приложение» и нажмите «Добавить»',
        ]
        : [
          `Нажмите ${chip(ICONS.share)} «Поделиться» внизу экрана`,
          `Пролистайте вниз и выберите ${add} «На экран „Домой“»`,
          'Нажмите «Добавить» в правом верхнем углу',
        ];
    case 'ios-other':
      return [
        `Нажмите ${chip(ICONS.share)} «Поделиться» в адресной строке или в меню`,
        `Выберите ${add} «На экран „Домой“»`,
        'Нажмите «Добавить»',
      ];
    case 'android-samsung':
      return [
        `Нажмите ${chip(ICONS.menu)} внизу экрана`,
        `Выберите ${chip(ICONS.plus)} «Добавить страницу на» → «Главный экран»`,
        'Подтвердите «Добавить»',
      ];
    case 'android':
      return [
        `Нажмите ${chip(ICONS.kebab)} справа от адресной строки`,
        `Выберите ${chip(ICONS.install)} «Установить приложение» или «Добавить на главный экран»`,
        'Подтвердите «Установить»',
      ];
    default:
      return [];
  }
}

function sheetBody() {
  const kind = browser();
  const head = `<div class="install-head">
      <img class="install-icon" src="${base}/static/apple-touch-icon.png" alt="" width="64" height="64">
      <p>Дневник откроется с иконки, как обычное приложение: во весь экран, быстрее и с пуш-уведомлениями.</p>
    </div>`;
  if (kind === 'ios-inapp' || kind === 'android-inapp') {
    const where = kind === 'ios-inapp' ? 'Safari' : 'Chrome';
    return `${head}<p class="install-note">Здесь установить не получится. Откройте эту страницу в ${where}
      (меню ⋯ → «Открыть в ${where === 'Safari' ? 'Safari' : 'браузере'}») — там появится подсказка.</p>
      <button class="btn outline" id="installLater">Понятно</button>`;
  }
  const button = deferredPrompt
    ? '<button class="btn" id="installNow">Установить</button>'
    : '';
  const list = deferredPrompt ? '' : `<ol class="install-steps">${steps().map((step, index) => `
      <li><span class="install-num">${index + 1}</span><span>${step}</span></li>`).join('')}</ol>`;
  const arrow = kind === 'ios-safari' && safariVersion() < 26
    ? `<div class="install-arrow" aria-hidden="true">${ICONS.down}</div>` : '';
  return `${head}${list}${button}
    <button class="btn outline" id="installLater">${deferredPrompt ? 'Не сейчас' : 'Понятно'}</button>${arrow}`;
}

/** Открыть инструкцию (в том числе из настроек). */
export function openInstallGuide() {
  const body = openSheet('Установить на экран «Домой»', sheetBody());
  body.querySelector('#installLater')?.addEventListener('click', closeSheet);
  body.querySelector('#installNow')?.addEventListener('click', async () => {
    const prompt = deferredPrompt;
    deferredPrompt = null;
    closeSheet();
    try {
      await prompt.prompt();
      await prompt.userChoice;
    } catch { /* окно уже показывали */ }
  });
}

/** Приложение с экрана «Домой» уже открывали — на этом или другом устройстве. */
function appInstalled() {
  return installed() || getPref('pwa') === 'installed';
}

/** Телефон, где приложения на экране «Домой» ещё нет. */
export function wantsInstall() {
  if (demo || appInstalled()) return false;
  return initData ? ['ios', 'android'].includes(tg?.platform) : browser() !== 'desktop';
}

/** Можно ли установить прямо отсюда: браузер телефона, а не Telegram. */
export function canInstall() {
  return !initData && wantsInstall();
}

/** Личная ссылка во внешнем браузере: устанавливать приложение — оттуда. */
export async function openInBrowser() {
  tap();
  try {
    const { data } = await api('/api/pwa-link');
    // Метка: в браузере после PIN сразу показать, как установить.
    const url = `${data.url}${data.url.includes('?') ? '&' : '?'}install=1`;
    // openLink открывает внешний браузер, а не встроенный в Telegram.
    if (tg?.openLink) tg.openLink(url, { try_instant_view: false });
    else window.open(url, '_blank');
  } catch (error) {
    notify('error');
    banner(error.message, true);
  }
}

/** Из Telegram на экран «Домой» не добавить — сначала в браузер. */
function openTelegramGuide() {
  const iphone = tg?.platform === 'ios';
  const body = openSheet('Установить на экран «Домой»', `
    <div class="install-head">
      <img class="install-icon" src="${base}/static/apple-touch-icon.png" alt="" width="64" height="64">
      <p>Дневник откроется с иконки, как обычное приложение: во весь экран, быстрее и с пуш-уведомлениями.</p>
    </div>
    <ol class="install-steps">
      <li><span class="install-num">1</span><span>Нажмите «Открыть в ${iphone ? 'Safari' : 'браузере'}» — откроется ваша личная ссылка</span></li>
      <li><span class="install-num">2</span><span>Там появится подсказка, куда нажать, чтобы добавить иконку</span></li>
      <li><span class="install-num">3</span><span>Запустите дневник с иконки — уведомления включатся сами</span></li>
    </ol>
    <button class="btn" id="installBrowser">Открыть в ${iphone ? 'Safari' : 'браузере'}</button>`);
  body.querySelector('#installBrowser').addEventListener('click', () => {
    closeSheet();
    openInBrowser();
  });
}

/** Инструкция, подходящая к месту: Telegram или браузер. */
export function openInstall() {
  if (initData) openTelegramGuide();
  else openInstallGuide();
}

// Карточку на главном экране закрыли — вернётся через три дня.
const CARD_KEY = 'installCardHidden';
const CARD_PAUSE = 3 * 24 * 3600 * 1000;

function cardHidden() {
  try {
    return Date.now() - Number(localStorage.getItem(CARD_KEY) || 0) < CARD_PAUSE;
  } catch {
    return false;
  }
}

/** Карточка «Добавить на экран Домой» над уроками дня. */
export function installCardHtml() {
  if (!wantsInstall() || cardHidden()) return '';
  return `<div class="install-card" data-install-card data-install-hint role="button" tabindex="0">
      <img class="install-card-icon" src="${base}/static/apple-touch-icon.png" alt="" width="40" height="40">
      <div class="install-card-text">
        <div class="install-card-title">Добавьте дневник на экран «Домой»</div>
        <div class="install-card-sub">Быстрее и с пуш-уведомлениями</div>
      </div>
      <button class="install-card-close" type="button" data-install-hide aria-label="Скрыть">${ICONS.close}</button>
    </div>`;
}

/** Флаг «установлено» пришёл с сервера после отрисовки — убрать подсказки. */
export function dropInstallHints() {
  if (wantsInstall()) return;
  document.querySelectorAll('[data-install-hint]').forEach((element) => element.remove());
}

export function bindInstallCard(container) {
  const card = container.querySelector('[data-install-card]');
  if (!card) return;
  card.addEventListener('click', (event) => {
    event.stopPropagation();
    if (event.target.closest('[data-install-hide]')) {
      tap();
      try { localStorage.setItem(CARD_KEY, String(Date.now())); } catch { /* ничего */ }
      card.remove();
      return;
    }
    tap();
    openInstall();
  });
}

function remember() {
  try {
    const seen = JSON.parse(localStorage.getItem(SEEN_KEY) || '{}');
    if ((seen.count || 0) >= MAX_SHOWS || Date.now() - (seen.at || 0) < PAUSE) return false;
    localStorage.setItem(SEEN_KEY, JSON.stringify({ count: (seen.count || 0) + 1, at: Date.now() }));
    return true;
  } catch {
    return false;
  }
}

async function waitForSheet() {
  // Под экраном PIN окно было бы невидимым — ждём, пока приложение откроют.
  const root = document.documentElement;
  if (root.classList.contains('app-locked')) {
    await new Promise((resolve) => {
      const observer = new MutationObserver(() => {
        if (root.classList.contains('app-locked')) return;
        observer.disconnect();
        resolve();
      });
      observer.observe(root, { attributes: true, attributeFilter: ['class'] });
    });
  }
  while (!$('sheet').hidden) {
    await new Promise(resolve => window.addEventListener('netschool:sheetclosed', resolve, { once: true }));
  }
}

/**
 * Ловим системное предложение установки (Chrome на Android) и сами
 * показываем подсказку — после первого экрана и других окошек.
 */
export function initInstallPrompt() {
  // Запуск с иконки: запомним для аккаунта — в Telegram и на других
  // устройствах больше не предлагать.
  if (installed() && !demo && getPref('pwa') !== 'installed') setPref('pwa', 'installed');
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferredPrompt = event;
  });
  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    try { localStorage.setItem(SEEN_KEY, JSON.stringify({ count: MAX_SHOWS, at: Date.now() })); } catch { /* ничего */ }
  });
  // Пришли по кнопке «Открыть в Safari» из Telegram — инструкция сразу,
  // без паузы и без счётчика показов. Метку из адреса убираем: иначе она
  // попала бы в иконку на экране «Домой».
  const params = new URLSearchParams(location.search);
  const asked = params.get('install') === '1';
  if (asked) {
    params.delete('install');
    const query = params.toString();
    history.replaceState(history.state, '', `${location.pathname}${query ? `?${query}` : ''}${location.hash}`);
  }
  if (!canInstall()) return;
  if (asked) {
    waitForSheet().then(() => { if (!installed()) openInstallGuide(); });
    return;
  }
  // Chrome присылает событие не сразу — даём ему пару секунд.
  setTimeout(async () => {
    await waitForSheet();
    if (installed() || !remember()) return;
    openInstallGuide();
  }, 3000);
}
