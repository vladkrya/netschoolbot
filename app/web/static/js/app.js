// Точка входа: вкладки как в официальном приложении, шапка, «Назад» Telegram.
'use strict';

import {
  $, assistantEnabled, banner, base, demo, loadAttachmentTicket, school, state, student, studentFirstName, syncFinished, syncStarted, tg, token,
} from './core.js';
import { initDay, loadDay } from './day.js';
import { initSchedule, loadSchedule } from './schedule.js';
import { initHomework, loadHomework } from './homework.js';
import { loadTotals } from './totals.js';
import { initMail, loadMail, openAnnouncements, openCompose } from './mail.js';
import { applyAccent, applyDesign, applyScheduleTab, initFullscreen, isNewDesign } from './appearance.js';
import { ICONS } from './icons.js';
import {
  applyStoredTheme, closeDrawer, initStudentSwitch, isDrawerOpen, loadProfile, loadSettings, openDrawer, syncTimezone,
} from './profile.js';
import { loadAssistantTab, openAssistant } from './assistant.js';
import { navTabs, renderNav } from './tabs.js';
import { closeSheet, initSheet } from './sheet.js';
import { clearScreens, handleBack, hasScreens, setFallbackBack } from './screens.js';
import { initSwipeBack } from './swipeback.js';
import { initIosHaptics, notify, tap } from './haptics.js';
import { syncPrefs } from './prefs.js';

const TABS = {
  day: loadDay,
  schedule: loadSchedule,
  assistant: loadAssistantTab,
  homework: loadHomework,
  totals: loadTotals,
  mail: loadMail,
  settings: loadSettings,
};

// Перелёт пружины cubic-bezier(.34, y1, .5, 1) — доля пути, а не пиксели: на
// прыжке через четыре вкладки линза улетала за край панели. y1 подобран так,
// чтобы перелёт на любом расстоянии был как у соседних вкладок, 0,066 ширины.
const GLIDE_Y1 = [1.45, 1.45, 1.315, 1.259, 1.227];

function moveLens(index) {
  const nav = $('bottomNav');
  const distance = Math.abs(index - Number(nav.style.getPropertyValue('--i') || 0));
  const y1 = GLIDE_Y1[Math.min(distance, GLIDE_Y1.length - 1)];
  nav.style.setProperty('--glide', `cubic-bezier(.34, ${y1}, .5, 1)`);
  nav.style.setProperty('--i', index);
}

/** Какие push-уведомления считаются просмотренными на вкладке: открыл
 *  «День» или «Оценки» — уведомления об оценках с экрана блокировки уходят. */
const SEEN_ON_TAB = {
  day: ['marks', 'homework', 'schedule', ''],
  totals: ['marks', ''],
  homework: ['homework'],
  schedule: ['schedule'],
  mail: ['mail'],
};

async function clearNotifications(tab = state.tab) {
  const kinds = SEEN_ON_TAB[tab];
  if (!kinds || !('serviceWorker' in navigator)) return;
  try {
    const registration = await navigator.serviceWorker.getRegistration();
    const shown = (await registration?.getNotifications()) || [];
    // '' — уведомления старого вида, без kind: их убирает «День».
    shown.filter((note) => kinds.includes(note.data?.kind || '')).forEach((note) => note.close());
  } catch {
    /* браузер без уведомлений — нечего убирать */
  }
}

// Вернулись в приложение из уведомления или с другого экрана — то же самое.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') clearNotifications();
});

function switchTab(name) {
  state.tab = name;
  clearNotifications(name);
  clearScreens();
  document.querySelectorAll('.panel').forEach((panel) => {
    panel.classList.toggle('active', panel.id === `panel-${name}`);
  });
  syncNav();
  // Полоса под шапкой (дата, поиск) — своя у каждой вкладки.
  document.querySelectorAll('.subbar').forEach((bar) => {
    bar.hidden = bar.dataset.for !== name;
  });
  syncHeaderHeight();
  window.scrollTo(0, 0);
  banner('');
  return TABS[name]();
}

/** Активный пункт и линза — под текущую вкладку. Вкладки нет в панели —
 *  линза прячется. */
function syncNav() {
  const items = [...document.querySelectorAll('.nav-item')];
  items.forEach((item) => item.classList.toggle('active', item.dataset.tab === state.tab));
  const index = items.findIndex((item) => item.dataset.tab === state.tab);
  $('bottomNav').classList.toggle('no-lens', index < 0);
  if (index >= 0) moveLens(index);
}

/** «Назад» вне стека экранов: закрыть шторку, меню, вернуться на первую вкладку. */
function fallbackBack(dryRun) {
  if (!$('sheet').hidden) {
    if (!dryRun) closeSheet();
    return true;
  }
  if (isDrawerOpen()) {
    if (!dryRun) closeDrawer();
    return true;
  }
  const home = navTabs()[0];
  if (state.tab !== home) {
    if (!dryRun) switchTab(home);
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
    // В чате помощника жест вниз — прокрутка переписки, обновлять нечего.
    if (hasScreens() || isDrawerOpen() || state.tab === 'assistant' || window.scrollY > 0) return;
    startY = event.touches[0].clientY;
    pulled = 0;
  }, { passive: true });
  document.addEventListener('touchmove', (event) => {
    if (startY == null) return;
    const before = pulled;
    pulled = event.touches[0].clientY - startY;
    // Отклик в момент, когда жест «взвёлся» — как у нативного pull-to-refresh.
    if (before <= 80 && pulled > 80) tap('medium');
    // Страницу трогаем, только когда что-то правда меняется: правка DOM
    // на каждое движение пальца мешала Safari засчитать касание тапом.
    const show = pulled > 30;
    if (indicator.classList.contains('show') !== show) indicator.classList.toggle('show', show);
    const text = pulled > 80 ? 'Отпустите, чтобы обновить' : 'Потяните, чтобы обновить';
    if (show && indicator.textContent !== text) indicator.textContent = text;
  }, { passive: true });
  document.addEventListener('touchend', async () => {
    if (startY == null) return;
    startY = null;
    if (pulled > 80) {
      // Данные остаются на экране, пока идёт запрос, и меняются на месте —
      // без скелетона и без повторной анимации появления.
      indicator.textContent = 'Обновляю…';
      indicator.classList.add('busy');
      state.refreshing = true;
      state.refreshFailed = false;
      syncStarted();
      try {
        await quietly(() => TABS[state.tab](true));
      } finally {
        state.refreshing = false;
        syncFinished();
        indicator.classList.remove('busy');
      }
      const failed = state.refreshFailed || document.querySelector('.panel.active .error-card');
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
  // Пункты меняются, когда человек настраивает панель, — берём каждый раз.
  let items = [...nav.querySelectorAll('.nav-item')];
  window.addEventListener('netschool:nav', () => { items = [...nav.querySelectorAll('.nav-item')]; });
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
    drag.v = (drag.v + (target - drag.pos) * 0.32) * 0.5;
    drag.pos += drag.v;
    const extra = Math.min(Math.abs(drag.v) * 0.012, 0.32);
    drag.sx += (BASE_SX + extra - drag.sx) * 0.35;
    const sy = BASE_SY - (drag.sx - BASE_SX) / 2;
    const inset = Math.max(0, (cell * (drag.sx - 1)) / 2 - 4);
    drag.pos = Math.max(inset, Math.min(inner - cell - inset, drag.pos));
    nav.style.setProperty('--x', `${drag.pos.toFixed(2)}px`);
    nav.style.setProperty('--sx', drag.sx.toFixed(3));
    nav.style.setProperty('--sy', sy.toFixed(3));

    // Щелчок — когда в новый пункт входит палец, а не капля: та вылетает из
    // прежней вкладки и пролетала бы мимо соседних, щёлкая на каждой.
    const index = indexAt(drag.x - left, cell);
    if (index !== drag.index) {
      drag.index = index;
      drag.moved = true;
      hover(index);
      tap('light');
    }
    frame = requestAnimationFrame(step);
  };

  // Касание текущей вкладки — лёгкий толчок, новой — щелчок выбора.
  const activate = (tab, silent) => {
    if (!silent) {
      // Ощутимый удар, а не еле слышный «щелчок выбора».
      tap(tab === state.tab ? 'light' : 'medium');
    }
    // Повторное касание тоже переключает: закрывает экраны поверх и листает вверх.
    switchTab(tab);
  };

  // Капля появляется, только когда палец повели или задержали: на быстрый
  // тап страница не меняется, и Safari честно доставляет клик подписи-
  // переключателю (иначе iOS не щёлкает). Тап переключает обычный click.
  const HOLD_MS = 180;
  const MOVE_PX = 6;
  let pending = null;
  let holdTimer = 0;

  const begin = () => {
    clearTimeout(holdTimer);
    if (!pending) return;
    // Палец и так «захвачен» элементом под ним; мышь захватываем, только
    // когда начали тянуть, — иначе клик ушёл бы панели, а не пункту.
    if (pending.mouse) {
      try {
        nav.setPointerCapture(pending.id);
      } catch {
        /* без захвата курсор за краем панели просто теряется */
      }
    }
    const { left, cell } = geometry();
    const current = Math.max(0, items.findIndex((item) => item.dataset.tab === state.tab));
    const under = indexAt(pending.x - left, cell);
    // Капля вырастает из линзы текущей вкладки и плывёт к пальцу.
    drag = {
      id: pending.id, x: pending.x, pos: current * cell, v: 0, sx: 1,
      index: under, moved: false,
    };
    pending = null;
    hover(under);
    nav.classList.add('dragging');
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(step);
  };

  nav.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || drag || pending) return;
    // На компьютере панель — колонка слева (wide.css): там капля не нужна.
    if (getComputedStyle(nav).flexDirection === 'column') return;
    pending = { id: event.pointerId, x: event.clientX, startX: event.clientX, mouse: event.pointerType === 'mouse' };
    holdTimer = setTimeout(begin, HOLD_MS);
  });

  nav.addEventListener('pointermove', (event) => {
    if (pending && event.pointerId === pending.id) {
      pending.x = event.clientX;
      if (Math.abs(event.clientX - pending.startX) > MOVE_PX) begin();
    } else if (drag && event.pointerId === drag.id) {
      drag.x = event.clientX;
    }
  });

  const release = (event, commit) => {
    if (pending && event.pointerId === pending.id) {
      // Быстрый тап: всё сделает click по пункту.
      clearTimeout(holdTimer);
      pending = null;
      return;
    }
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

  // Тап и клавиатура. Клик сразу после перетаскивания уже обработан выше.
  nav.addEventListener('click', (event) => {
    const item = event.target.closest('.nav-item');
    if (!item || performance.now() - lastRelease < 500) return;
    activate(item.dataset.tab, false);
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
    quietly(() => TABS[state.tab]());
  });
}

/** Перерисовать вкладку на месте: без скелетона и без повторного «въезда»
 *  карточек — только лёгкое проявление обновлённого. */
async function quietly(render) {
  document.body.classList.add('quiet-render');
  try {
    await render();
  } finally {
    requestAnimationFrame(() => {
      document.body.classList.remove('quiet-render');
      const panel = document.querySelector('.panel.active');
      if (!panel) return;
      panel.classList.remove('soft-update');
      void panel.offsetWidth;
      panel.classList.add('soft-update');
    });
  }
}

/** Все вкладки грузятся сразу после «Дня», а не при первом заходе: в
 *  «Итогах» и «Д/З» данные уже на месте. Рисуются они в скрытые панели —
 *  при переключении вкладка берёт готовое из state. По одной, чтобы не
 *  забить школьный сервер пачкой запросов. */
async function prefetch() {
  for (const name of ['day', 'schedule', 'homework', 'totals', 'mail']) {
    if (name === state.tab) continue;
    try {
      await TABS[name]();
    } catch {
      /* вкладка догрузится при заходе */
    }
  }
}

function initChrome() {
  initNav();
  initIosHaptics();
  initFresh();
  $('menuBtn').addEventListener('click', openDrawer);
  $('navMenuBtn').addEventListener('click', openDrawer);
  $('drawerShade').addEventListener('click', closeDrawer);
  // Панель пересобрали в настройках: подписи второй вкладки и линза.
  window.addEventListener('netschool:nav', () => {
    applyScheduleTab();
    syncNav();
  });
  $('bellBtn').addEventListener('click', openAnnouncements);
  $('composeBtn').addEventListener('click', () => openCompose());
  initMail();
  // Помощник — кнопкой на «Дне»: вкладкой он не помещался в панель.
  $('aiFab').hidden = !assistantEnabled || demo;
  $('aiFab').addEventListener('click', () => openAssistant());
  window.addEventListener('resize', syncHeaderHeight);
  new ResizeObserver(syncHeaderHeight).observe($('appHeader'));
  // «Перейти ко дню» из расписания.
  window.addEventListener('netschool:tab', (event) => switchTab(event.detail));
  window.addEventListener('netschool:design', onDesign);
  // Вошли без сети, сеть вернулась и сервер принял PIN — показываем свежее.
  window.addEventListener('netschool:unlocked', () => { if (state.tab) quietly(() => TABS[state.tab]()); });
}

/** Сменили дизайн: у нового своя панель и вкладка «Настройки» вместо ☰.
 *  Переключали из настроек — в них и остаёмся: во вкладке или в меню. */
function onDesign() {
  const fromSettings = isDrawerOpen() || state.tab === 'settings';
  renderNav();
  if (!state.tab) return;
  if (isNewDesign() && isDrawerOpen()) {
    closeDrawer();
    switchTab('settings');
  } else if (!navTabs().includes(state.tab)) {
    switchTab(navTabs()[0]);
    if (fromSettings) openDrawer();
  } else {
    quietly(() => TABS[state.tab]());
  }
}

const NAME_KEY = 'studentName';

function storedName() {
  try { return studentFirstName(localStorage.getItem(NAME_KEY) || ''); } catch { return ''; }
}

async function start() {
  // Билет на вложения — заранее: внешний браузер открывается только прямо
  // из нажатия, ждать сети в этот момент нельзя.
  loadAttachmentTicket();
  applyStoredTheme();
  applyAccent();
  applyDesign();
  // Telegram должен скрыть свой экран загрузки до ввода PIN.
  initTelegram();
  // Тема, дизайн и цвет, выбранные в Telegram, — и на сайте (и наоборот).
  syncPrefs().then((changed) => {
    if (!changed) return;
    applyStoredTheme();
    applyAccent();
    applyDesign();
    renderNav();
    window.dispatchEvent(new CustomEvent('netschool:design'));
  });
  renderNav();
  renderIcons();
  applyScheduleTab();
  initFullscreen();
  initSheet();
  initDay();
  initSchedule();
  initHomework();
  initChrome();
  bindPullToRefresh();
  initSwipeBack();
  setFallbackBack(fallbackBack);

  // С PIN сервер не кладёт имя в страницу, а профиль без сети приходит
  // последним — до него в шапке стояло «Дневник». Берём имя прошлого входа.
  $('topName').textContent = studentFirstName(student) || storedName() || school || 'Дневник';
  // Первая вкладка панели — та, с которой открывается приложение.
  await switchTab(navTabs()[0]);
  // Остальные запросы идут только после первого экрана, не конкурируют с ним.
  setTimeout(prefetch, 600);

  // Профиль нужен только меню, но имя ученика в шапке берём из него,
  // если страница открыта без токена (по initData Telegram).
  try {
    const profile = await loadProfile();
    if (profile?.name) {
      $('topName').textContent = studentFirstName(profile.name);
      try { localStorage.setItem(NAME_KEY, profile.name); } catch { /* приватный режим */ }
    }
    initStudentSwitch(profile);
    syncTimezone(profile);
  } catch {
    /* профиль не критичен для остальных экранов */
  }
}

/** Service worker вне Telegram: показ сохранённого без сети и push. */
function registerWorker() {
  if (tg?.initData || demo || !token || !('serviceWorker' in navigator)) return;
  navigator.serviceWorker
    .register(`${base}/sw.js?token=${encodeURIComponent(token)}`, { scope: `${base}/` })
    .catch(() => { /* без worker приложение работает как раньше, только без офлайна */ });
}

// Модули загрузились — счётчик перезагрузок из app.html больше не нужен.
try { sessionStorage.removeItem('bootRetries'); } catch { /* приватный режим */ }
start();
registerWorker();
