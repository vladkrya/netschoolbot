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
  document.querySelectorAll('.nav-item').forEach((item) => {
    item.classList.toggle('active', item.dataset.tab === name);
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

function initChrome() {
  document.querySelectorAll('.nav-item').forEach((item) => {
    item.addEventListener('click', () => {
      select();
      switchTab(item.dataset.tab);
    });
  });
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

start();
