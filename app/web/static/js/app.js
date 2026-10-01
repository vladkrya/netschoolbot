// Точка входа: вкладки как в официальном приложении, шапка, «Назад» Telegram.
'use strict';

import { $, banner, school, state, student, tg } from './core.js';
import { initDay, loadDay } from './day.js';
import { initSchedule, loadSchedule } from './schedule.js';
import { initHomework, loadHomework } from './homework.js';
import { loadTotals } from './totals.js';
import { loadMail, openAnnouncements, openCompose } from './mail.js';
import { applyAccent, initFullscreen } from './appearance.js';
import { ICONS } from './icons.js';
import { applyStoredTheme, closeDrawer, isDrawerOpen, loadProfile, openDrawer } from './profile.js';
import { closeSheet, initSheet } from './sheet.js';
import { clearScreens, handleBack, hasScreens, setFallbackBack } from './screens.js';
import { notify, select, tap } from './haptics.js';

const TABS = {
  day: loadDay,
  schedule: loadSchedule,
  homework: loadHomework,
  totals: loadTotals,
  mail: loadMail,
};

function switchTab(name) {
  state.tab = name;
  clearScreens();
  document.querySelectorAll('.panel').forEach((panel) => {
    panel.classList.toggle('active', panel.id === `panel-${name}`);
  });
  document.querySelectorAll('.nav-item').forEach((item, index) => {
    const active = item.dataset.tab === name;
    item.classList.toggle('active', active);
    if (active) $('bottomNav').style.setProperty('--i', index);
  });
  // Полоса под шапкой (дата, поиск) — своя у каждой вкладки.
  document.querySelectorAll('.subbar').forEach((bar) => {
    bar.hidden = bar.dataset.for !== name;
  });
  syncHeaderHeight();
  window.scrollTo(0, 0);
  banner('');
  TABS[name]();
}

/** «Назад» вне стека экранов: закрыть шторку, меню, вернуться на «День». */
function fallbackBack(dryRun) {
  if (!$('sheet').hidden) {
    if (!dryRun) closeSheet();
    return true;
  }
  if (isDrawerOpen()) {
    if (!dryRun) closeDrawer();
    return true;
  }
  if (state.tab !== 'day') {
    if (!dryRun) switchTab('day');
    return true;
  }
  return false;
}

function initTelegram() {
  if (!tg) return;
  tg.ready();
  tg.expand();
  tg.BackButton.onClick(handleBack);
}

/** Иконки подставляются из одного набора: в разметке только data-icon. */
function renderIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((el) => {
    el.innerHTML = ICONS[el.dataset.icon] || '';
  });
}

/** Контент начинается под шапкой, высота которой зависит от вкладки. */
function syncHeaderHeight() {
  const height = $('appHeader').getBoundingClientRect().height;
  document.documentElement.style.setProperty('--header-h', `${Math.round(height)}px`);
}

/** Свайп вниз от верха вкладки — обновить её с сервера, минуя кэш. */
function bindPullToRefresh() {
  const indicator = $('pullIndicator');
  let startY = null;
  let pulled = 0;
  document.addEventListener('touchstart', (event) => {
    if (hasScreens() || isDrawerOpen() || window.scrollY > 0) return;
    startY = event.touches[0].clientY;
    pulled = 0;
  }, { passive: true });
  document.addEventListener('touchmove', (event) => {
    if (startY == null) return;
    const before = pulled;
    pulled = event.touches[0].clientY - startY;
    // Отклик в момент, когда жест «взвёлся» — как у нативного pull-to-refresh.
    if (before <= 80 && pulled > 80) tap('medium');
    indicator.classList.toggle('show', pulled > 30);
    indicator.textContent = pulled > 80 ? 'Отпустите, чтобы обновить' : 'Потяните, чтобы обновить';
  }, { passive: true });
  document.addEventListener('touchend', async () => {
    if (startY == null) return;
    startY = null;
    if (pulled > 80) {
      indicator.textContent = 'Обновляю…';
      await TABS[state.tab](true);
      const failed = document.querySelector('.panel.active .error-card');
      notify(failed ? 'error' : 'success');
    }
    indicator.classList.remove('show');
  }, { passive: true });
}

// Панель вкладок как в iOS 26: палец на панели — линза раздувается в каплю
// и едет за ним, растягиваясь от скорости; на каждом новом пункте щелчок.
// Отпустил — открывается вкладка под пальцем.
function initNav() {
  const nav = $('bottomNav');
  const items = [...nav.querySelectorAll('.nav-item')];
  let drag = null;
  let relax = 0;
  let lastRelease = 0;

  const geometry = () => {
    // Отступ 4px внутри рамки — как у .nav-glider в app.css.
    const left = nav.getBoundingClientRect().left + nav.clientLeft + 4;
    const inner = nav.clientWidth - 8;
    return { left, inner, cell: inner / items.length };
  };
  const indexAt = (x) => {
    const { left, cell } = geometry();
    return Math.max(0, Math.min(items.length - 1, Math.floor((x - left) / cell)));
  };
  // Капля шире ячейки (см. --sx в app.css), поэтому у краёв её центр
  // держим дальше от края: раздутая, она доходит ровно до кромки панели.
  const BASE_SX = 1.22;
  let scaleX = BASE_SX;
  let lastX = 0;
  const follow = (x) => {
    lastX = x;
    const { left, inner, cell } = geometry();
    const inset = Math.max(0, (cell * (scaleX - 1)) / 2 - 4);
    const pos = Math.max(inset, Math.min(inner - cell - inset, x - left - cell / 2));
    nav.style.setProperty('--x', `${pos}px`);
  };
  const hover = (index) => items.forEach((item, i) => item.classList.toggle('hover', i === index));
  const stretch = (speed) => {
    const extra = Math.min(speed * 0.18, 0.3);
    scaleX = BASE_SX + extra;
    nav.style.setProperty('--sx', scaleX.toFixed(3));
    nav.style.setProperty('--sy', (1.18 - extra / 2).toFixed(3));
    clearTimeout(relax);
    relax = setTimeout(() => {
      scaleX = BASE_SX;
      nav.style.removeProperty('--sx');
      nav.style.removeProperty('--sy');
      if (drag) follow(lastX);
    }, 90);
  };

  // Касание текущей вкладки — лёгкий толчок, новой — щелчок выбора.
  const activate = (tab, silent) => {
    if (!silent) {
      if (tab === state.tab) tap('soft');
      else select();
    }
    // Повторное касание тоже переключает: закрывает экраны поверх и листает вверх.
    switchTab(tab);
  };

  nav.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || drag) return;
    try {
      nav.setPointerCapture(event.pointerId);
    } catch {
      /* без захвата палец за краем панели просто теряется */
    }
    const index = indexAt(event.clientX);
    drag = { id: event.pointerId, index, moved: false, x: event.clientX, t: event.timeStamp };
    follow(event.clientX);
    hover(index);
    nav.classList.add('dragging');
  });

  nav.addEventListener('pointermove', (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const dt = Math.max(1, event.timeStamp - drag.t);
    stretch(Math.abs(event.clientX - drag.x) / dt);
    drag.x = event.clientX;
    drag.t = event.timeStamp;
    follow(event.clientX);
    const index = indexAt(event.clientX);
    if (index !== drag.index) {
      drag.index = index;
      drag.moved = true;
      hover(index);
      select();
    }
  });

  const release = (event, commit) => {
    if (!drag || event.pointerId !== drag.id) return;
    const { index, moved } = drag;
    drag = null;
    clearTimeout(relax);
    scaleX = BASE_SX;
    ['--sx', '--sy'].forEach((name) => nav.style.removeProperty(name));
    nav.classList.remove('dragging');
    hover(-1);
    lastRelease = performance.now();
    // Если вели пальцем, щелчки уже были на каждом пункте — лишний не нужен.
    if (commit) activate(items[index].dataset.tab, moved);
  };
  nav.addEventListener('pointerup', (event) => release(event, true));
  nav.addEventListener('pointercancel', (event) => release(event, false));
  nav.addEventListener('lostpointercapture', (event) => release(event, false));

  // Клик после касания уже обработан выше; сюда доходит клавиатура.
  items.forEach((item) => {
    item.addEventListener('click', () => {
      if (performance.now() - lastRelease < 500) return;
      activate(item.dataset.tab, false);
    });
  });
}

function initChrome() {
  initNav();
  $('menuBtn').addEventListener('click', openDrawer);
  $('drawerShade').addEventListener('click', closeDrawer);
  $('bellBtn').addEventListener('click', openAnnouncements);
  $('composeBtn').addEventListener('click', openCompose);
  window.addEventListener('resize', syncHeaderHeight);
  new ResizeObserver(syncHeaderHeight).observe($('appHeader'));
  // «Перейти ко дню» из расписания.
  window.addEventListener('netschool:tab', (event) => switchTab(event.detail));
}

async function start() {
  applyStoredTheme();
  applyAccent();
  renderIcons();
  initTelegram();
  initFullscreen();
  initSheet();
  initDay();
  initSchedule();
  initHomework();
  initChrome();
  bindPullToRefresh();
  setFallbackBack(fallbackBack);

  $('topTitle').textContent = student || school || 'Дневник';
  switchTab('day');

  // Профиль нужен только меню, но имя ученика в шапке берём из него,
  // если страница открыта без токена (по initData Telegram).
  try {
    const profile = await loadProfile();
    if (profile?.name) $('topTitle').textContent = profile.name;
  } catch {
    /* профиль не критичен для остальных экранов */
  }
}

// Модули загрузились — счётчик перезагрузок из app.html больше не нужен.
try { sessionStorage.removeItem('bootRetries'); } catch { /* приватный режим */ }
start();
