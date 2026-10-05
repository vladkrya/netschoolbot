// Боковое меню (☰): ученик, оформление, уведомления, калькулятор —
// как в официальном приложении, где настройки живут в выдвижной панели.
'use strict';

import { $, api, banner, demo, esc, initData, pushEnabled, state, tg } from './core.js';
import { ACCENTS, currentAccent, saveAccent } from './appearance.js';
import { ICONS } from './icons.js';
import { loadCalc } from './calc.js';
import { loadTotals } from './totals.js';
import { pushScreen, syncBackButton } from './screens.js';
import { notify, select, tap } from './haptics.js';
import { getPref, setPref } from './prefs.js';

export async function loadProfile(force = false) {
  if (state.profile && !force) return state.profile;
  const { data } = await api('/api/profile');
  state.profile = data;
  return data;
}

function currentTheme() {
  const theme = getPref('theme');
  return ['light', 'dark'].includes(theme) ? theme : 'auto';
}

export function applyStoredTheme() {
  const theme = currentTheme();
  if (theme === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', theme);
}

export function isDrawerOpen() {
  return $('drawer').classList.contains('open');
}

export function closeDrawer() {
  $('drawer').classList.remove('open');
  syncBackButton();
}

export async function openDrawer() {
  tap();
  $('drawer').classList.add('open');
  syncBackButton();
  const panel = $('drawerBody');
  if (!state.profile) {
    panel.innerHTML = '<div class="muted small">Загрузка…</div>';
    try {
      await loadProfile();
    } catch (error) {
      panel.innerHTML = `<div class="muted small">${esc(error.message)}</div>`;
      return;
    }
  }
  render();
}

const NOTIFY_LABELS = {
  grades: 'Новые оценки',
  changes: 'Изменения оценок',
  deletes: 'Удаления оценок',
  homework: 'Домашние задания',
  mail: 'Школьная почта',
  weekly_summary: 'Сводка по понедельникам',
  evening_summary: 'Вечером — сводка на завтра',
  absences: 'Новые пропуски',
};

// Отметки посещаемости есть только в мобильном API.
const MOBILE_ONLY = new Set(['absences']);

function toggle(key, label, on) {
  return `<label class="switch-row"><span>${label}</span>
      <input type="checkbox" class="switch" data-notify="${key}" ${on ? 'checked' : ''}></label>`;
}

const timeFormat = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
});

function when(iso) {
  return iso ? timeFormat.format(new Date(iso)) : '—';
}

/** Что с фоновой проверкой: когда прошла, чем кончилась, когда следующая. */
function statusLine(status) {
  if (!status) return '';
  const failing = status.last_error_at && (!status.last_ok_at || status.last_error_at > status.last_ok_at);
  const rows = [`Последняя успешная проверка: ${when(status.last_ok_at)}`];
  if (failing) rows.push(`Ошибка ${when(status.last_error_at)}: ${esc(status.last_error)}`);
  if (status.running && status.next_check_at) rows.push(`Следующая: ${when(status.next_check_at)}`);
  if (status.queued) rows.push(`Ждут отправки: ${status.queued}`);
  return `<div class="muted small sync-status ${failing ? 'failing' : ''}">${rows.join('<br>')}</div>`;
}

function timezoneSelect(data) {
  if (!data.timezones) return '';
  const options = data.timezones
    .map((tz) => `<option value="${tz.id}" ${tz.id === data.timezone ? 'selected' : ''}>${esc(tz.name)}</option>`)
    .join('');
  return `<label class="field-label">Часовой пояс
      <select class="field" id="timezoneSelect">${options}</select></label>`;
}

async function openCalendar() {
  closeDrawer();
  const screen = pushScreen('Расписание в календаре');
  screen.innerHTML = '<div class="muted small">Загрузка…</div>';
  try {
    const { data } = await api('/api/calendar-link');
    screen.innerHTML = `<div class="card">
        <p>Уроки с временем, кабинетом и заданиями появятся в календаре телефона
        и будут обновляться сами — примерно раз в три часа.</p>
        <button class="btn" id="calAdd">Добавить в календарь</button>
        <button class="btn outline" id="calCopy">Скопировать ссылку</button>
        <p class="muted small">Ссылка личная, как и ссылка на приложение. Если она утекла —
        /app_reset в боте выпустит новую.</p>
      </div>`;
    $('calAdd').addEventListener('click', () => {
      tap();
      if (tg?.openLink) tg.openLink(data.webcal);
      else window.location.href = data.webcal;
    });
    $('calCopy').addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(data.url);
        notify('success');
        banner('Ссылка скопирована — вставьте её в «Добавить календарь по ссылке»');
      } catch {
        prompt('Скопируйте ссылку', data.url);
      }
    });
  } catch (error) {
    screen.innerHTML = `<div class="muted small">${esc(error.message)}</div>`;
  }
}

const HISTORY_ICONS = { new: '🆕', changed: '✏️', deleted: '🗑' };

async function openHistory() {
  closeDrawer();
  const screen = pushScreen('История оценок');
  screen.innerHTML = '<div class="muted small">Загрузка…</div>';
  try {
    const { data } = await api('/api/history');
    if (!data.items.length) {
      screen.innerHTML = '<div class="empty muted">Пока пусто: изменения появятся после следующих проверок.</div>';
      return;
    }
    screen.innerHTML = `<div class="history-list">${data.items.map((item) => {
      const value = item.kind === 'changed'
        ? `${esc(item.old)} → <b>${esc(item.new)}</b>`
        : item.kind === 'deleted' ? `<s>${esc(item.mark)}</s>` : `<b>${esc(item.mark)}</b>`;
      const date = item.date.split('-').reverse().slice(0, 2).join('.');
      return `<div class="history-item">
          <div class="history-head"><span>${HISTORY_ICONS[item.kind] || ''} ${esc(item.subject)}</span><span class="history-mark">${value}</span></div>
          <div class="muted small">${esc(item.title)} · за ${date} · замечено ${when(item.seen)}</div>
        </div>`;
    }).join('')}</div>`;
  } catch (error) {
    screen.innerHTML = `<div class="muted small">${esc(error.message)}</div>`;
  }
}

const SCHEMES = [
  ['auto', 'Как в школе'],
  ['quarters', 'Четверти'],
  ['trimesters', 'Триместры'],
  ['halves', 'Полугодия'],
];
const SCHEME_PERIODS = {
  quarters: ['1 четверть', '2 четверть', '3 четверть', '4 четверть'],
  trimesters: ['1 триместр', '2 триместр', '3 триместр'],
  halves: ['1 полугодие', '2 полугодие'],
};
const SCHEME_STARTS = {
  quarters: ['09-01', '11-01', '01-09', '04-01'],
  trimesters: ['09-01', '12-01', '03-01'],
  halves: ['09-01', '01-09'],
};

/** «MM-DD» ↔ дата поля ввода: год любой, нужен только месяц и день. */
const toInput = (md) => {
  const [month] = md.split('-').map(Number);
  return `${month >= 9 ? 2026 : 2027}-${md}`;
};
const fromInput = (value) => value.slice(5);

function periodsSection(data) {
  const setting = data.periods || { scheme: 'auto' };
  const scheme = setting.scheme || 'auto';
  const options = SCHEMES.map(([key, label]) =>
    `<option value="${key}" ${key === scheme ? 'selected' : ''}>${label}</option>`).join('');
  const hint = scheme === 'auto'
    ? (data.api_mode === 'mobile'
      ? 'Границы берутся из «Сетевого города».'
      : 'Веб-API не отдаёт границы периодов: используются обычные четверти. Выберите схему, если у вашей школы иначе.')
    : '';
  const starts = setting.starts || SCHEME_STARTS[scheme] || [];
  const dates = scheme === 'auto' ? '' : SCHEME_PERIODS[scheme]
    .map((label, i) => `<label class="field-label period-start">${label} — с
        <input class="field" type="date" data-period-start="${i}" value="${toInput(starts[i])}"></label>`)
    .join('');
  return `<section class="drawer-section">
      <div class="drawer-heading">Учебные периоды</div>
      <select class="field" id="periodScheme">${options}</select>
      ${hint ? `<div class="muted small">${hint}</div>` : ''}
      ${dates}
    </section>`;
}

async function savePeriods(setting) {
  try {
    await api('/api/prefs', { method: 'POST', body: JSON.stringify({ periods: setting }) });
    state.profile.periods = setting;
    // «Итоги» и калькулятор посчитаны по старым границам.
    state.grades = null;
    state.calc = null;
    notify('success');
    if (state.tab === 'totals') loadTotals(true);
  } catch (error) {
    notify('error');
    banner(error.message, true);
  }
  render();
}

function pushSection() {
  // Внутри Telegram web push недоступен: уведомления браузера работают только
  // у сайта, открытого в самом браузере или установленного на экран «Домой».
  if (initData) {
    return `<section class="drawer-section">
      <div class="drawer-heading">Уведомления в браузере</div>
      <ol class="howto">
        <li>Нажмите «Открыть в браузере» — откроется ваша личная ссылка.</li>
        <li><b>iPhone:</b> в Safari «Поделиться» → «На экран Домой». <b>Android:</b> в Chrome ⋮ → «Установить приложение».</li>
        <li>Запустите дневник с иконки на экране и в меню ☰ нажмите «Включить уведомления».</li>
      </ol>
      <button class="btn outline" id="pwaOpen">Открыть в браузере</button>
    </section>`;
  }
  if (!pushEnabled) return '';
  const iphone = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const installed = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  if (iphone && !installed) return `<section class="drawer-section">
      <div class="drawer-heading">Уведомления в браузере</div>
      <p class="muted small">Чтобы включить уведомления, откройте дневник в Safari, нажмите «Поделиться» → «На экран Домой» и запустите приложение с новой иконки.</p>
    </section>`;
  return `<section class="drawer-section">
      <div class="drawer-heading">Уведомления в браузере</div>
      <button class="btn outline" id="pushBtn">Включить уведомления</button>
      <p class="muted small">На iPhone работают только в приложении, установленном на экран «Домой».</p>
    </section>`;
}

function demoSection() {
  if (!demo) return '';
  return `<section class="drawer-section demo-box">
      <div class="drawer-heading">${ICONS.sparkles} Настройки демо</div>
      <label class="field-label">Ученики (через запятую)
        <input class="field" id="demoChildren" value=""></label>
      <label class="field-label">Школа
        <input class="field" id="demoSchool" value=""></label>
      <label class="field-label">Уроков в день: <b id="demoLessonsValue"></b>
        <input type="range" id="demoLessons" min="3" max="7"></label>
      <label class="field-label">Как часто ставят оценки: <b id="demoDensityValue"></b>
        <input type="range" id="demoDensity" min="0" max="90" step="5"></label>
      <label class="switch-row"><span>Пропуски по болезни</span><input type="checkbox" class="switch" id="demoAbsences"></label>
      <label class="switch-row"><span>Учимся по субботам</span><input type="checkbox" class="switch" id="demoSaturday"></label>
      <button class="btn" id="demoApply">Применить</button>
      <button class="btn outline" id="demoReset">Сбросить демо</button>
    </section>`;
}

function render() {
  const data = state.profile;
  const theme = currentTheme();
  const accent = currentAccent();
  const radio = (value, label) =>
    `<label class="drawer-option"><input type="radio" name="theme" value="${value}" ${theme === value ? 'checked' : ''}><span>${label}</span></label>`;

  $('drawerBody').innerHTML = `
    ${demo ? '<div class="demo-badge">Демо-режим · данные выдуманные</div>' : ''}
    <section class="drawer-section">
      <div class="drawer-heading">Аккаунт ученика</div>
      <div class="drawer-name">${esc(data.name)}</div>
      <div class="muted small">${esc(data.school)}</div>
      ${data.students.length > 1 ? `<div class="drawer-children">${data.students
        .map((s) => `<button class="chip ${s.current ? 'active' : ''}" data-student="${s.id}">${esc(s.name)}</button>`)
        .join('')}</div>` : ''}
    </section>
    <section class="drawer-section">
      <div class="drawer-heading">Дополнительно</div>
      <button class="drawer-link" id="drawerCalc">${ICONS.calc} Калькулятор баллов</button>
      <button class="drawer-link" id="drawerHistory">${ICONS.clock} История оценок</button>
      ${demo ? '' : `<button class="drawer-link" id="drawerCalendar">${ICONS.calendar} Расписание в календаре</button>`}
    </section>
    <section class="drawer-section">
      <div class="drawer-heading">Оформление</div>
      ${radio('light', 'Светлая тема')}
      ${radio('auto', 'Как в системе')}
      ${radio('dark', 'Тёмная тема')}
      <div class="drawer-sub">${ICONS.palette} Цвет меню</div>
      <div class="swatches">
        ${ACCENTS.map(([color, name]) => `<button class="swatch ${color === accent ? 'on' : ''}" data-accent="${color}" style="background:${color}" aria-label="${name}" title="${name}"></button>`).join('')}
        <label class="swatch custom ${ACCENTS.some(([c]) => c === accent) ? '' : 'on'}" title="Свой цвет" style="background:${accent}">
          <input type="color" id="accentCustom" value="${accent}">＋</label>
      </div>
    </section>
    <section class="drawer-section">
      <div class="drawer-heading">Уведомления в Telegram</div>
      ${Object.entries(NOTIFY_LABELS)
        .filter(([key]) => !MOBILE_ONLY.has(key) || data.api_mode === 'mobile')
        .map(([key, label]) => toggle(key, label, data.notifications[key]))
        .join('')}
      <div class="muted small">Проверка каждые ${data.interval_minutes} мин</div>
      ${statusLine(data.status)}
      ${timezoneSelect(data)}
    </section>
    ${demo ? '' : periodsSection(data)}
    ${pushSection()}
    ${demoSection()}
    <div class="drawer-foot muted small">Дневник «Сетевого города»</div>`;

  bindDrawer();
}

function bindDrawer() {
  const body = $('drawerBody');
  body.querySelectorAll('input[name="theme"]').forEach((input) =>
    input.addEventListener('change', () => {
      select();
      setPref('theme', input.value);
      applyStoredTheme();
    })
  );
  body.querySelectorAll('[data-accent]').forEach((button) =>
    button.addEventListener('click', () => {
      select();
      saveAccent(button.dataset.accent);
      render();
    })
  );
  $('accentCustom').addEventListener('change', (event) => {
    saveAccent(event.target.value);
    select();
    render();
  });
  body.querySelectorAll('[data-notify]').forEach((input) =>
    input.addEventListener('change', async () => {
      select();
      input.disabled = true;
      try {
        const { data } = await api('/api/settings/notifications', {
          method: 'POST',
          body: JSON.stringify({ key: input.dataset.notify, value: input.checked }),
        });
        state.profile.notifications = data;
      } catch (error) {
        notify('error');
        input.checked = !input.checked;
        banner(error.message, true);
      } finally {
        input.disabled = false;
      }
    })
  );
  body.querySelectorAll('[data-student]').forEach((button) =>
    button.addEventListener('click', () => switchStudent(button))
  );
  $('drawerCalc').addEventListener('click', () => {
    closeDrawer();
    const screen = pushScreen('Калькулятор баллов');
    screen.innerHTML = '<div id="calcContent"></div>';
    loadCalc();
  });
  $('drawerHistory').addEventListener('click', () => { tap(); openHistory(); });
  $('drawerCalendar')?.addEventListener('click', () => { tap(); openCalendar(); });
  $('timezoneSelect')?.addEventListener('change', async (event) => {
    select();
    const previous = state.profile.timezone;
    try {
      await api('/api/settings/timezone', {
        method: 'POST',
        body: JSON.stringify({ timezone: event.target.value }),
      });
      state.profile.timezone = event.target.value;
      notify('success');
    } catch (error) {
      notify('error');
      event.target.value = previous;
      banner(error.message, true);
    }
  });
  $('periodScheme')?.addEventListener('change', (event) => {
    select();
    savePeriods({ scheme: event.target.value });
  });
  body.querySelectorAll('[data-period-start]').forEach((input) =>
    input.addEventListener('change', () => {
      const values = [...body.querySelectorAll('[data-period-start]')].map((el) => fromInput(el.value));
      if (values.some((v) => !/^\d{2}-\d{2}$/.test(v))) return;
      savePeriods({ scheme: state.profile.periods.scheme, starts: values });
    })
  );
  $('pushBtn')?.addEventListener('click', (event) => enablePush(event.currentTarget));
  $('pwaOpen')?.addEventListener('click', openInBrowser);
  if (demo) bindDemo();
}

async function openInBrowser() {
  tap();
  try {
    const { data } = await api('/api/pwa-link');
    // openLink открывает внешний браузер, а не встроенный в Telegram.
    if (tg?.openLink) tg.openLink(data.url, { try_instant_view: false });
    else window.open(data.url, '_blank');
  } catch (error) {
    notify('error');
    banner(error.message, true);
  }
}

async function bindDemo() {
  const { demoConfig, resetDemoConfig, saveDemoConfig } = await import('./demo.js');
  const cfg = demoConfig();
  $('demoChildren').value = cfg.children.join(', ');
  $('demoSchool').value = cfg.school;
  $('demoLessons').value = cfg.lessons;
  $('demoDensity').value = cfg.density;
  $('demoAbsences').checked = cfg.absences;
  $('demoSaturday').checked = cfg.saturday;
  const labels = () => {
    $('demoLessonsValue').textContent = $('demoLessons').value;
    $('demoDensityValue').textContent = `${$('demoDensity').value}%`;
  };
  labels();
  $('demoLessons').addEventListener('input', () => { select(); labels(); });
  $('demoDensity').addEventListener('input', labels);
  $('demoApply').addEventListener('click', () => {
    const children = $('demoChildren').value.split(',').map((x) => x.trim()).filter(Boolean);
    saveDemoConfig({
      children: children.length ? children : ['Пятёркин Отличник'],
      student: children[0] || 'Пятёркин Отличник',
      child: 0,
      school: $('demoSchool').value.trim() || 'Демо-школа',
      lessons: Number($('demoLessons').value),
      density: Number($('demoDensity').value),
      absences: $('demoAbsences').checked,
      saturday: $('demoSaturday').checked,
    });
    notify('success');
    location.reload();
  });
  $('demoReset').addEventListener('click', () => {
    resetDemoConfig();
    notify('success');
    location.reload();
  });
}

async function switchStudent(button) {
  button.disabled = true;
  try {
    await api('/api/student', {
      method: 'POST',
      body: JSON.stringify({ id: Number(button.dataset.student) }),
    });
    notify('success');
    // Дневник другого ребёнка — совсем другие данные, сбрасываем всё.
    location.reload();
  } catch (error) {
    notify('error');
    banner(error.message, true);
    button.disabled = false;
  }
}

async function enablePush(button) {
  button.disabled = true;
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      throw new Error('Браузер не поддерживает уведомления');
    }
    if ((await Notification.requestPermission()) !== 'granted') {
      throw new Error('Разрешение не выдано');
    }
    const { base, token } = window.NETSCHOOL;
    const registration = await navigator.serviceWorker.register(
      `${base}/sw.js?token=${encodeURIComponent(token)}`,
      { scope: `${base}/` }
    );
    const { data } = await api('/api/push/key');
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(data.key),
    });
    await api('/api/push/subscribe', {
      method: 'POST',
      body: JSON.stringify(subscription.toJSON()),
    });
    button.textContent = 'Уведомления включены ✓';
    notify('success');
  } catch (error) {
    notify('error');
    banner(error.message, true);
    button.disabled = false;
  }
}

function urlBase64ToUint8Array(value) {
  const padding = '='.repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((char) => char.charCodeAt(0)));
}
