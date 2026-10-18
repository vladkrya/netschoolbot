// Точка входа: вкладки как в официальном приложении, шапка, «Назад» Telegram.
'use strict';

import {
  $, api, assistantEnabled, forgetRevalidated, banner, base, demo, isReadOnly, keepTouch, loadAttachmentTicket, school, state, student, studentFirstName, syncFinished, syncStarted, tg, token,
} from './core.js';
import { initDay, loadDay } from './day.js';
import { initSchedule, loadSchedule } from './schedule.js';
import { initHomework, loadHomework } from './homework.js';
import { loadTotals } from './totals.js';
import { chatEvents } from './chat-live.js';
import { initMail, loadMail, openAnnouncements, openCompose, selectChats } from './mail.js';
import {
  applyAccent, applyDesign, applyHalloween, applyScheduleTab, halloweenAvailable, initFullscreen, isHalloween,
  isNewDesign, navIcon, setHalloweenAccess,
} from './appearance.js';
import { ICONS } from './icons.js';
import { dropInstallHints, initInstallPrompt } from './install.js';
import {
  applyStoredTheme, closeDrawer, initStudentSwitch, isDrawerOpen, loadProfile, loadSettings, offerPushOnFirstLaunch, openDrawer,
  syncTimezone,
} from './profile.js';
import { loadAssistantTab, openAssistant } from './assistant.js';
import { navTabs, renderNav } from './tabs.js';
import { closeSheet, initSheet, openSheet, sheetOpen } from './sheet.js';
import { openBugReport } from './bugreport.js';
import { clearScreens, handleBack, hasScreens, setFallbackBack } from './screens.js';
import { initSwipeBack } from './swipeback.js';
import { initTrackpad } from './trackpad.js';
import { initIosHaptics, notify, tap } from './haptics.js';
import { getPref, setPref, syncPrefs } from './prefs.js';

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
  if (sheetOpen()) {
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
    el.innerHTML = el.closest('.bottom-nav, .ai-fab') ? navIcon(el.dataset.icon) : ICONS[el.dataset.icon] || '';
  });
}

/** Контент начинается под шапкой, высота которой зависит от вкладки. */
function syncHeaderHeight() {
  const height = $('appHeader').getBoundingClientRect().height;
  document.documentElement.style.setProperty('--header-h', `${Math.round(height)}px`);
}

/** Свайп вниз от верха вкладки — обновить её с сервера, минуя кэш.
 *
 *  Кружок висит поверх страницы и не сдвигает её: контент остаётся на
 *  месте, нажатия и прокрутка проходят сквозь него. Заполняется по мере
 *  натяжения — целиком ровно тогда, когда отпускание обновит, — и крутится
 *  дальше с того же угла, пока идёт запрос. */
const PULL_ARM = 80;
const RING = 2 * Math.PI * 11;
// Насколько опустить кружок, чтобы его центр лёг на полосу загрузки под
// шапкой: индикатор стоит на 34px выше её края, кружок — 36px.
const MERGE_Y = 13;
// Сколько кружок едет к полосе и сжимается в точку.
const MERGE_MS = 220;

function bindPullToRefresh() {
  const indicator = $('pullIndicator');
  indicator.innerHTML = `<span class="pull-ring"><span class="pull-spin"><svg viewBox="0 0 30 30" aria-hidden="true">
      <circle class="pull-track" cx="15" cy="15" r="11"/><circle class="pull-arc" cx="15" cy="15" r="11"/></svg></span></span>
    <span class="pull-text" aria-live="polite"></span>`;
  const ring = indicator.querySelector('.pull-ring');
  const arc = indicator.querySelector('.pull-arc');
  const text = indicator.querySelector('.pull-text');
  arc.style.strokeDasharray = String(RING);
  let start = null;
  let pulled = 0;
  let busy = false;
  const paint = (px) => {
    const progress = Math.min(1, px / PULL_ARM);
    ring.style.transform = `translateY(${Math.min(px, PULL_ARM * 1.4) * 0.6}px) rotate(${progress * 270}deg)`;
    ring.style.opacity = String(Math.min(1, px / 40));
    arc.style.strokeDashoffset = String(RING * (1 - progress));
  };
  const hide = () => {
    ring.style.transition = '';
    ring.style.opacity = '0';
    ring.style.transform = '';
    indicator.classList.remove('show');
  };
  document.addEventListener('touchstart', (event) => {
    // В чате помощника жест вниз — прокрутка переписки, обновлять нечего.
    if (busy || hasScreens() || isDrawerOpen() || state.tab === 'assistant' || window.scrollY > 0 || event.touches.length !== 1) return;
    start = { x: event.touches[0].clientX, y: event.touches[0].clientY };
    pulled = 0;
    keepTouch(event.target, { move: onPullMove, end: onPullEnd, cancel: onPullCancel });
  }, { passive: true });
  const onPullMove = (event) => {
    if (!start) return;
    const touch = event.touches[0];
    const dx = touch.clientX - start.x;
    const dy = touch.clientY - start.y;
    // Вбок — это листание дней или недель, а не обновление.
    if (pulled < 10 && Math.abs(dx) > Math.abs(dy)) {
      if (Math.abs(dx) > 10) {
        start = null;
        hide();
      }
      return;
    }
    const before = pulled;
    pulled = Math.max(0, dy);
    // Отклик в момент, когда жест «взвёлся» — как у нативного pull-to-refresh.
    if (before <= PULL_ARM && pulled > PULL_ARM) tap('medium');
    if (pulled < 12) {
      if (indicator.classList.contains('show')) hide();
      return;
    }
    if (!indicator.classList.contains('show')) {
      indicator.classList.add('show');
      ring.style.transition = 'none';
    }
    paint(pulled);
  };
  document.addEventListener('touchmove', onPullMove, { passive: true });
  const onPullEnd = async () => {
    if (!start) return;
    start = null;
    if (pulled <= PULL_ARM) {
      hide();
      return;
    }
    // Данные остаются на экране, пока идёт запрос, и меняются на месте —
    // без скелетона и без повторной анимации появления. Индикатор один —
    // полоса загрузки под шапкой, и видно, откуда она взялась: кружок
    // замыкается, поднимается на линию полосы и сжимается в точку, а из
    // точки в обе стороны вырастает бегущая полоса (sync-grow в app.css).
    busy = true;
    const root = document.documentElement;
    arc.style.transition = 'stroke-dashoffset .12s var(--ease)';
    arc.style.strokeDashoffset = '0';
    // Поворот тот же, что в конце жеста (270°): иначе кружок раскручивался бы.
    ring.style.transition = `transform ${MERGE_MS}ms var(--ease)`;
    ring.style.transform = `translateY(${MERGE_Y}px) rotate(270deg) scale(.22)`;
    text.textContent = 'Обновляю…';
    state.refreshing = true;
    state.refreshFailed = false;
    const merged = new Promise((resolve) => setTimeout(() => {
      ring.style.transition = 'opacity .1s';
      ring.style.opacity = '0';
      root.classList.add('sync-grow');
      syncStarted();
      resolve();
    }, MERGE_MS));
    try {
      await quietly(() => TABS[state.tab](true));
    } finally {
      state.refreshing = false;
      await merged;
      syncFinished();
      setTimeout(() => root.classList.remove('sync-grow'), 350);
    }
    const failed = state.refreshFailed || document.querySelector('.panel.active .error-card');
    notify(failed ? 'error' : 'success');
    text.textContent = failed ? 'Не удалось обновить' : 'Обновлено';
    arc.style.transition = '';
    hide();
    setTimeout(() => { text.textContent = ''; }, 1000);
    busy = false;
  };
  document.addEventListener('touchend', onPullEnd, { passive: true });
  const onPullCancel = () => {
    if (!start) return;
    start = null;
    hide();
  };
  document.addEventListener('touchcancel', onPullCancel, { passive: true });
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

// Новое в дневнике по сокету (проверка оценок нашла) — какие вкладки это показывают.
const DIARY_TABS = {
  marks: ['day', 'totals'],
  homework: ['day', 'homework'],
  absences: ['day', 'totals'],
  schedule: ['day', 'schedule'],
};

function initLiveDiary() {
  chatEvents.addEventListener('change', ({ detail }) => {
    if (detail.t !== 'diary') return;
    forgetRevalidated();
    const tabs = new Set((detail.kinds || []).flatMap((kind) => DIARY_TABS[kind] || []));
    if (!tabs.has(state.tab) || $('screens').childElementCount || state.refreshing) return;
    quietly(() => TABS[state.tab](true));
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

// Вернулись в приложение, которое iPhone держал в памяти: показанное могло
// устареть, а сверка за этот запуск уже была. Через минуту отсутствия
// открытая вкладка заново сверяется со школьным сервером.
const RESUME_RECHECK_MS = 60000;
function initResume() {
  let hiddenAt = 0;
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      hiddenAt = Date.now();
      return;
    }
    if (!hiddenAt || Date.now() - hiddenAt < RESUME_RECHECK_MS) return;
    hiddenAt = 0;
    forgetRevalidated();
    if (demo || $('screens').childElementCount || state.refreshing) return;
    // Как обновление жестом, только без жеста: данные остаются на экране,
    // пока идёт запрос, сверху — полоса сверки.
    state.refreshing = true;
    syncStarted();
    quietly(() => TABS[state.tab](true)).finally(() => {
      state.refreshing = false;
      syncFinished();
    });
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
      // Анимации появления карточек, заглушённые на время обновления, со
      // снятием класса запустились бы заново — с прозрачности 0, и экран на
      // миг пустел. Доводим их сразу до конца; бесконечные (спиннер) не трогаем.
      void panel.offsetWidth;
      for (const animation of panel.getAnimations?.({ subtree: true }) || []) {
        // Только CSS: анимации из motion.js (счётчики, вспышки) идут своим ходом.
        if (typeof CSSAnimation === 'undefined' || !(animation instanceof CSSAnimation)) continue;
        if (animation.effect?.getTiming().iterations === Infinity) continue;
        try { animation.finish(); } catch { /* уже закончилась */ }
      }
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
    if (name === state.tab || (name === 'mail' && isReadOnly())) continue;
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
  initResume();
  initLiveDiary();
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
  // При просмотре дневника родным почты и чатов нет — и опрашивать их незачем.
  if (!isReadOnly()) initMail();
  // Помощник — кнопкой на «Дне»: вкладкой он не помещался в панель.
  $('aiFab').hidden = !assistantEnabled;
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

/** Один раз: дизайн по умолчанию сменился, старый — в настройках. */
/** Статистика для админа: какой дизайн и где открыто приложение. */
function reportVisit() {
  if (demo) return;
  const client = tg?.initData ? 'telegram'
    : window.matchMedia('(display-mode: standalone)').matches || navigator.standalone ? 'pwa' : 'web';
  api('/api/visit', {
    method: 'POST', body: JSON.stringify({ design: isNewDesign() ? 'new' : 'classic', client }),
  }).catch(() => {});
}

async function showChatLaunchNotice() {
  if (demo) return;
  // Не заменяем уже открытую панель (например, объявление о дизайне).
  while (!$('sheet').hidden) {
    await new Promise(resolve => window.addEventListener('netschool:sheetclosed', resolve, { once: true }));
  }
  try {
    const { data } = await api('/api/notices/chats', { method: 'POST' });
    if (!data.show) return;
    while (!$('sheet').hidden) {
      await new Promise(resolve => window.addEventListener('netschool:sheetclosed', resolve, { once: true }));
    }
    const body = openSheet('Появились чаты', `
      <p>Теперь в приложении можно общаться с другими участниками и написать админу.</p>
      <p>Откройте вкладку <b>Сообщения → Чаты</b>.</p>
      <div class="chat-actions">
        <button class="btn" id="chatLaunchOpen">Перейти в чаты</button>
        <button class="btn outline" id="chatLaunchClose">Понятно</button>
      </div>`);
    body.querySelector('#chatLaunchClose').addEventListener('click', closeSheet);
    body.querySelector('#chatLaunchOpen').addEventListener('click', () => {
      closeSheet();
      selectChats();
      switchTab('mail');
    });
  } catch { /* Без связи окно дождётся следующего открытия приложения. */ }
}

/** Разовое окно: всем включили хеллоуинское оформление — что появилось,
 *  где выключить и куда писать об ошибках. */
async function showHalloweenNotice() {
  // Уже выключил сам (профиль пришёл раньше) — рассказывать незачем.
  if (demo || (halloweenAvailable() && !isHalloween())) return;
  await sheetFree();
  let data;
  try {
    ({ data } = await api('/api/notices/halloween', { method: 'POST' }));
  } catch { return; /* Без связи покажем при следующем открытии. */ }
  if (!data.show) return;
  await sheetFree();
  const settings = isNewDesign() ? 'вкладке <b>Настройки</b>' : 'меню <b>☰</b>';
  const feedback = data.bug_report
    ? 'в настройках, внизу — <b>«Сообщить об ошибке или идее»</b>'
    : 'во вкладке <b>Сообщения → Чаты</b> — там можно написать админу';
  const body = openSheet('🎃 Хеллоуин в дневнике', `
    <p>До 31 октября приложение оделось к празднику. Что появилось:</p>
    <ul class="halloween-news">
      <li>🌙 ночная тема с луной, мерцающими звёздами и туманом внизу экрана;</li>
      <li>🕸️ паутина с пауком и летучие мыши в шапке — а в сам Хеллоуин их станет больше;</li>
      <li>🦇 иногда через весь экран пролетает большая летучая мышь;</li>
      <li>🧙 праздничные иконки вкладок: тыква, котёл, книга заклинаний, привидение, паучок;</li>
      <li>🎨 свои цвета: тыквенный, ведьмин, зелье, кровавая луна, призрачный, костяной.</li>
    </ul>
    <p>Не нравится? Выключить можно в ${settings} → <b>Оформление</b> → <b>🎃 Хеллоуин</b>. Ваша тема и цвет вернутся.</p>
    <p class="muted small">Нашли ошибку или есть идея — напишите ${feedback}.</p>
    <div class="chat-actions">
      <button class="btn" id="halloweenOk">Класс 🎃</button>
      <button class="btn outline" id="halloweenFeedback">${data.bug_report ? 'Сообщить об ошибке или идее' : 'Написать админу'}</button>
    </div>`);
  body.querySelector('#halloweenOk').addEventListener('click', () => {
    tap('light');
    closeSheet();
  });
  body.querySelector('#halloweenFeedback').addEventListener('click', () => {
    closeSheet();
    if (data.bug_report) {
      openBugReport('bug');
    } else {
      selectChats();
      switchTab('mail');
    }
  });
}

/** Ждать, пока закроется уже открытая панель (объявление, другой опрос). */
async function sheetFree() {
  while (!$('sheet').hidden) {
    await new Promise(resolve => window.addEventListener('netschool:sheetclosed', resolve, { once: true }));
  }
}

/** Разовый опрос «нравится ли приложение» — только тем, кто был до него.
 *  «Не сейчас» (и просто закрыть окно) — спросим при следующем открытии;
 *  ответ или «Не хочу» снимают опрос насовсем. */
async function showSurvey() {
  if (demo) return;
  await sheetFree();
  let data;
  try {
    ({ data } = await api('/api/notices/survey', { method: 'POST' }));
  } catch { return; /* Без связи спросим при следующем открытии. */ }
  if (!data.show) return;
  await sheetFree();
  const body = openSheet('Нравится приложение?', `
    <p>Ответьте на один вопрос — это займёт пару секунд и поможет сделать приложение лучше.</p>
    <div class="chat-actions">
      <button class="btn" id="surveyStart">Пройти сейчас</button>
      <button class="btn outline" id="surveyLater">Не сейчас</button>
      <button class="btn outline survey-decline" id="surveyDecline">Не хочу</button>
    </div>`);
  body.querySelector('#surveyLater').addEventListener('click', closeSheet);
  body.querySelector('#surveyDecline').addEventListener('click', () => {
    api('/api/survey/decline', { method: 'POST' }).catch(() => {});
    closeSheet();
  });
  body.querySelector('#surveyStart').addEventListener('click', () => {
    tap('light');
    surveyScores(body, data.bug_report);
  });
}

function surveyScores(body, bugReport) {
  const scores = Array.from({ length: 10 }, (_, i) => i + 1)
    .map(n => `<button class="survey-score" data-score="${n}">${n}</button>`).join('');
  body.innerHTML = `
    <p>Насколько вам нравится приложение — от 1 до 10?</p>
    <div class="survey-scores">${scores}</div>
    <div class="survey-ends"><span>совсем нет</span><span>очень</span></div>
    <div class="chat-actions"><button class="btn" id="surveySend" disabled>Отправить</button></div>`;
  let score = 0;
  const send = body.querySelector('#surveySend');
  body.querySelectorAll('.survey-score').forEach(button => button.addEventListener('click', () => {
    score = Number(button.dataset.score);
    body.querySelectorAll('.survey-score').forEach(other => other.classList.toggle('on', other === button));
    send.disabled = false;
    tap('light');
  }));
  send.addEventListener('click', async () => {
    send.disabled = true;
    try {
      await api('/api/survey', { method: 'POST', body: JSON.stringify({ score }) });
    } catch (error) {
      notify('error');
      banner(error.message, true);
      send.disabled = false;
      return;
    }
    notify('success');
    surveyThanks(body, bugReport);
  });
}

function surveyThanks(body, bugReport) {
  const where = bugReport
    ? 'в меню настроек, внизу — «Сообщить об ошибке или идее»'
    : 'во вкладке <b>Сообщения → Чаты</b> — там можно написать админу';
  body.innerHTML = `
    <p><b>Спасибо за оценку!</b></p>
    <p>Есть идея, что добавить, или заметили ошибку? Напишите нам — ${where}.</p>
    <div class="chat-actions">
      <button class="btn" id="surveyFeedback">${bugReport ? 'Написать об идее или ошибке' : 'Перейти в чаты'}</button>
      <button class="btn outline" id="surveyDone">Закрыть</button>
    </div>`;
  body.querySelector('#surveyDone').addEventListener('click', closeSheet);
  body.querySelector('#surveyFeedback').addEventListener('click', () => {
    closeSheet();
    if (bugReport) {
      openBugReport('idea');
    } else {
      selectChats();
      switchTab('mail');
    }
  });
}

async function start() {
  // Билет на вложения — заранее: внешний браузер открывается только прямо
  // из нажатия, ждать сети в этот момент нельзя.
  loadAttachmentTicket();
  applyStoredTheme();
  applyHalloween();
  applyAccent();
  applyDesign();
  // Telegram должен скрыть свой экран загрузки до ввода PIN.
  initTelegram();
  // Тема, дизайн и цвет, выбранные в Telegram, — и на сайте (и наоборот).
  // Запрос ушёл ещё из boot.js, раньше дневника: своё — вперёд «Сетевого города».
  (state.prefsSync || syncPrefs()).then((changed) => {
    if (changed) {
      applyStoredTheme();
      applyAccent();
      applyDesign();
      renderNav();
      window.dispatchEvent(new CustomEvent('netschool:design'));
      // Правки расписания могли прийти с другого устройства.
      window.dispatchEvent(new CustomEvent('netschool:edits'));
    }
    // Флаг «видел» уже пришёл с сервера — окошко не повторится на другом устройстве.
    dropInstallHints();
    if (!isReadOnly()) showChatLaunchNotice().then(showHalloweenNotice).then(showSurvey);
    reportVisit();
  });
  offerPushOnFirstLaunch();
  initInstallPrompt();
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
  initTrackpad();
  setFallbackBack(fallbackBack);

  // С PIN сервер не кладёт имя в страницу, а профиль без сети приходит
  // последним — до него в шапке стояло «Дневник». Берём имя прошлого входа.
  $('topName').textContent = studentFirstName(student) || storedName() || school || 'Дневник';
  // Профиль — с сервера бота, без «Сетевого города»: просим его раньше
  // дневника и не ждём первый экран. Раньше он шёл после первой вкладки, и
  // когда школьный сервер висел, вместе с ним висели имя, меню и настройки.
  const profileLoad = loadProfile().catch(() => null);
  // Первая вкладка панели — та, с которой открывается приложение.
  const firstTab = switchTab(navTabs()[0]);
  firstTab.then(() => setTimeout(prefetch, 600)).catch(() => {});

  // Профиль нужен только меню, но имя ученика в шапке берём из него,
  // если страница открыта без токена (по initData Telegram).
  try {
    const profile = await profileLoad;
    setHalloweenAccess(profile?.halloween);
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
