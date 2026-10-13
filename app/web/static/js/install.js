// Подсказка «установить на экран Домой» для сайта, открытого в браузере
// телефона. На Android (Chrome, Edge, Samsung) — системное окно установки
// по кнопке, на iPhone такого окна нет: показываем, куда нажимать в Safari.
'use strict';

import { $, base, demo, initData } from './core.js';
import { ICONS } from './icons.js';
import { closeSheet, openSheet } from './sheet.js';

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

/** Можно ли вообще установить: телефон, браузер, ещё не установлено. */
export function canInstall() {
  return !initData && !demo && !installed() && browser() !== 'desktop';
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
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferredPrompt = event;
  });
  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    try { localStorage.setItem(SEEN_KEY, JSON.stringify({ count: MAX_SHOWS, at: Date.now() })); } catch { /* ничего */ }
  });
  if (!canInstall()) return;
  // Chrome присылает событие не сразу — даём ему пару секунд.
  setTimeout(async () => {
    await waitForSheet();
    if (installed() || !remember()) return;
    openInstallGuide();
  }, 3000);
}
