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
// и свободно плывёт за ним, растягиваясь от собственной скорости; на каждом
// новом пункте под каплей щелчок. Отпустил — открывается вкладка под пальцем.
//
// Капля движется своей анимацией (requestAnimationFrame), а не переходами
// CSS: те перезапускались на каждое движение пальца, и капля дёргалась.
function initNav() {
  const nav = $('bottomNav');
  const items = [...nav.querySelectorAll('.nav-item')];
  // Масштаб раздутой капли в покое; по ходу движения она вытягивается.
  const BASE_SX = 1.22;
  const BASE_SY = 1.18;
  let drag = null;
  let frame = 0;
  let lastRelease = 0;

  const geometry = () => {
    // Отступ 4px внутри рамки — как у .nav-glider в app.css.
    const left = nav.getBoundingClientRect().left + nav.clientLeft + 4;
    const inner = nav.clientWidth - 8;
    return { left, inner, cell: inner / items.length };
  };
  const indexAt = (pos, cell) => Math.max(0, Math.min(items.length - 1, Math.floor(pos / cell)));
  const hover = (index) => items.forEach((item, i) => item.classList.toggle('hover', i === index));

  // Один кадр: капля догоняет палец (пружина с затуханием), растягивается
  // по скорости и упирается в кромку панели с учётом своей ширины.
  const step = () => {
    if (!drag) return;
    const { left, inner, cell } = geometry();
    const target = drag.x - left - cell / 2;
    drag.v = (drag.v + (target - drag.pos) * 0.32) * 0.58;
    drag.pos += drag.v;
    const extra = Math.min(Math.abs(drag.v) * 0.012, 0.32);
    drag.sx += (BASE_SX + extra - drag.sx) * 0.35;
    const sy = BASE_SY - (drag.sx - BASE_SX) / 2;
    const inset = Math.max(0, (cell * (drag.sx - 1)) / 2 - 4);
    drag.pos = Math.max(inset, Math.min(inner - cell - inset, drag.pos));
    nav.style.setProperty('--x', `${drag.pos.toFixed(2)}px`);
    nav.style.setProperty('--sx', drag.sx.toFixed(3));
    nav.style.setProperty('--sy', sy.toFixed(3));

    const index = indexAt(drag.pos + cell / 2, cell);
    if (index !== drag.index) {
      drag.index = index;
      drag.moved = true;
      hover(index);
      select();
    }
    frame = requestAnimationFrame(step);
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
    const { cell } = geometry();
    const current = Math.max(0, items.findIndex((item) => item.dataset.tab === state.tab));
    // Капля вырастает из линзы текущей вкладки и плывёт к пальцу.
    drag = {
      id: event.pointerId, x: event.clientX, pos: current * cell, v: 0, sx: 1,
      index: current, moved: false,
    };
    hover(current);
    nav.classList.add('dragging');
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(step);
  });

  nav.addEventListener('pointermove', (event) => {
    if (drag && event.pointerId === drag.id) drag.x = event.clientX;
  });

  const release = (event, commit) => {
    if (!drag || event.pointerId !== drag.id) return;
    const { x, moved } = drag;
    drag = null;
    cancelAnimationFrame(frame);
    ['--x', '--sx', '--sy'].forEach((name) => nav.style.removeProperty(name));
    nav.classList.remove('dragging');
    hover(-1);
    lastRelease = performance.now();
    if (!commit) return;
    const { left, cell } = geometry();
    // Если вели пальцем, щелчки уже были на каждом пункте — лишний не нужен.
    activate(items[indexAt(x - left, cell)].dataset.tab, moved);
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

// Какие вкладки показывают данные с этого адреса API.
const FRESH_TABS = {
  '/api/diary': ['day', 'schedule'],
  '/api/homework': ['homework'],
  '/api/marks': ['totals'],
  '/api/mail': ['mail'],
};

// Сохранённое уже на экране, а со школьного сервера пришло новое —
// тихо перерисовываем вкладку, если она открыта и поверх нет экрана.
function initFresh() {
  document.addEventListener('netschool:fresh', ({ detail: path }) => {
    const tabs = FRESH_TABS[path.split('?')[0]] || [];
    if (!tabs.includes(state.tab) || $('screens').childElementCount) return;
    TABS[state.tab]();
  });
}

function initChrome() {
  initNav();
  initFresh();
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
