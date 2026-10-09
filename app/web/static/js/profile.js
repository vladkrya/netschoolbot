// Боковое меню (☰): ученик, оформление, уведомления, калькулятор —
// как в официальном приложении, где настройки живут в выдвижной панели.
'use strict';

import { $, api, banner, demo, esc, initData, pushEnabled, state, tg } from './core.js';
import {
  ACCENTS, currentAccent, isNewDesign, saveAccent, saveBells, saveDesign, saveWeekends, showBells, showWeekends, syncChromeColor,
} from './appearance.js';
import { ICONS } from './icons.js';
import { loadCalc } from './calc.js';
import { openAssistant } from './assistant.js';
import { loadTotals } from './totals.js';
import { pushScreen, syncBackButton } from './screens.js';
import { notify, select, tap } from './haptics.js';
import { getPref, setPref } from './prefs.js';
import { closeSheet, openSheet } from './sheet.js';
import { openBugReport } from './bugreport.js';
import { openLockSettings } from './lock.js';

export async function loadProfile(force = false) {
  if (state.profile && !force) return state.profile;
  const { data } = await api('/api/profile');
  state.profile = data;
  return data;
}

/** Часовой пояс берём у устройства: «сегодня» и время уведомлений
 *  считаются по нему. Сервер обновляется, только когда пояс сменился. */
export async function syncTimezone(profile) {
  if (demo || !profile?.timezone) return;
  let zone = '';
  try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch {
    return;
  }
  if (!zone || zone === profile.timezone) return;
  try {
    await api('/api/settings/timezone', { method: 'POST', body: JSON.stringify({ timezone: zone }) });
    profile.timezone = zone;
  } catch {
    /* неизвестный серверу пояс — остаётся прежний */
  }
}

function currentTheme() {
  const theme = getPref('theme');
  return ['light', 'dark'].includes(theme) ? theme : 'auto';
}

export function applyStoredTheme() {
  const theme = currentTheme();
  if (theme === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', theme);
  syncChromeColor();
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
  schedule: 'Замены и отмены уроков',
  absences: 'Новые пропуски',
};

// Отметки посещаемости есть только в мобильном API.
const MOBILE_ONLY = new Set(['absences']);

// Атрибут switch — системный переключатель: в Safari на iPhone он сам
// тактильно щёлкает от касания (vibrate там нет, см. haptics.js).
function toggle(key, label, on) {
  return `<label class="switch-row"><span>${label}</span>
      <input type="checkbox" switch class="switch" data-notify="${key}" ${on ? 'checked' : ''}></label>`;
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

async function openCalendar() {
  closeDrawer();
  const screen = pushScreen('Расписание в календаре');
  screen.innerHTML = '<div class="muted small">Загрузка…</div>';
  let password = '';
  const copy = async (value) => {
    try {
      await navigator.clipboard.writeText(value);
      notify('success');
      banner('Скопировано', false, 2500);
    } catch { prompt('Скопируйте значение', value); }
  };
  const render = (data) => {
    const field = (label, value, id) => `<label class="field-label">${label}
      <input class="field" readonly value="${esc(value)}" autocomplete="off" spellcheck="false"></label>
      <button class="btn outline" id="${id}">Скопировать ${label.toLowerCase()}</button>`;
    screen.innerHTML = `<div class="card">
      ${data.available === false ? '<p>CalDAV сейчас отключён на сервере. Можно использовать подписку по ссылке ниже.</p>' : `<p>Подключите календарный аккаунт один раз. Уроки, кабинеты и задания будут
      появляться в штатном календаре; замены и отмены будут обновляться автоматически.</p>
      <p class="muted small">Сервер проверяет расписание в «Сетевом городе» примерно каждые ${data.interval_minutes} минут.
      iPhone получает изменения при следующей синхронизации календаря, поэтому они могут появиться с задержкой.
      Держать бот или это приложение открытыми не нужно. Календарь доступен только для чтения.</p>
      <details><summary>Как часто обновляется календарь на iPhone?</summary>
        <p>Частоту загрузки задаёт iOS: Настройки → Приложения → Календарь →
        Учётные записи календаря → Загрузка данных. Можно выбрать, например, каждые 15 или 30 минут.
        В старых версиях iOS раздел «Календарь» находится прямо в настройках.</p>
        <p>Кнопка «Проверить расписание сейчас» запускает проверку школы на сервере сразу.
        После неё iPhone получит изменения при следующей синхронизации календаря.</p>
      </details>`}
      ${data.connected ? `<p>${data.last_sync_at ? 'Расписание опубликовано.' : 'Готовим расписание — оно появится после первой успешной проверки школы.'}</p>
        ${data.last_error ? `<p class="muted small">${esc(data.last_error)}</p>` : ''}
        ${field('Сервер', data.server, 'calServer')}
        ${field('Account URL', data.account_url, 'calAccountUrl')}
        ${field('Логин', data.login, 'calLogin')}
        ${password ? `${field('Пароль', password, 'calPassword')}<p class="muted small">Сохраните пароль: повторно он не показывается.</p>` : '<p class="muted small">Если пароль потерян, выпустите новый и обновите его в настройках календарного аккаунта.</p>'}
        <p><b>На iPhone:</b> Настройки → Приложения → Календарь → Учётные записи календаря →
        Добавить учётную запись → Другое → Учётная запись CalDAV.</p>
        <p>Введите сервер, логин и пароль выше. Используйте SSL. Путь аккаунта определяется автоматически. Если Calendar просит Account URL, скопируйте его выше. Описание можно выбрать любое.</p>
        <button class="btn outline" id="calStatus">Обновить статус</button>
        <button class="btn outline" id="calRefresh">Проверить расписание сейчас</button>` : ''}
      ${data.available === false ? '' : `<button class="btn" id="calCreate">${data.connected ? 'Выпустить новый пароль' : 'Создать доступ CalDAV'}</button>`}
      ${data.connected ? '<button class="btn outline" id="calRevoke">Отключить доступ</button>' : ''}
      <p class="muted small">Выход через /logout отзывает доступ. Отключение доступа останавливает
      синхронизацию; уже сохранённые события удаляются вместе с аккаунтом на телефоне.</p>
      </div>
      <details class="card"><summary>Подписка по ссылке .ics</summary>
        <p>Альтернативный способ: добавьте ссылку как подписной календарь. Простое открытие файла не создаёт подписку.</p>
        <button class="btn outline" id="calCopyIcs">Скопировать ссылку .ics</button>
      </details>`;
    if (data.connected) {
      $('calServer').onclick = () => copy(data.server);
      $('calAccountUrl').onclick = () => copy(data.account_url);
      $('calLogin').onclick = () => copy(data.login);
      if (password) $('calPassword').onclick = () => copy(password);
      $('calStatus').onclick = load;
      $('calRefresh').onclick = async () => {
        try {
          await api('/api/caldav-sync', { method: 'POST' });
          banner('Проверяем расписание. Через некоторое время обновите статус.');
        } catch (error) { banner(error.message, true); }
      };
      $('calRevoke').onclick = async () => {
        if (!confirm('Отключить доступ календарного аккаунта к расписанию?')) return;
        try {
          await api('/api/caldav-account', { method: 'DELETE' });
          password = '';
          await load();
        } catch (error) { banner(error.message, true); }
      };
    }
    if ($('calCreate')) $('calCreate').onclick = async () => {
      if (data.connected && !confirm('Старый пароль перестанет работать. Выпустить новый?')) return;
      const button = $('calCreate');
      button.disabled = true;
      try {
        const result = await api('/api/caldav-account', { method: 'POST' });
        password = result.data.password;
        render(result.data);
      } catch (error) { banner(error.message, true); button.disabled = false; }
    };
    $('calCopyIcs').onclick = async () => {
      try { const result = await api('/api/calendar-link'); await copy(result.data.url); }
      catch (error) { banner(error.message, true); }
    };
  };
  const load = async () => {
    try { const { data } = await api('/api/caldav-account'); render(data); }
    catch (error) { screen.innerHTML = `<div class="muted small">${esc(error.message)}</div>`; }
  };
  await load();
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

/** Где возможен web push: сайт в браузере, на iPhone — только с экрана «Домой». */
function pushAvailability() {
  if (initData) return 'telegram';
  if (!pushEnabled || !('serviceWorker' in navigator) || !('PushManager' in window)) return 'none';
  const iphone = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const installed = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  return iphone && !installed ? 'install' : 'ready';
}

function pushRow() {
  const where = pushAvailability();
  if (where === 'ready') {
    return `<label class="switch-row"><span>Пуш на этом устройстве</span>
        <input type="checkbox" switch class="switch" id="pushToggle" disabled></label>`;
  }
  if (where === 'install') {
    return `<div class="muted small">Пуш на iPhone: откройте дневник в Safari, «Поделиться» → «На экран Домой»
        и запустите приложение с новой иконки.</div>`;
  }
  return '';
}

function pushHowto() {
  // Внутри Telegram web push недоступен: уведомления браузера работают только
  // у сайта, открытого в самом браузере или установленного на экран «Домой».
  if (pushAvailability() !== 'telegram') return '';
  return `<section class="drawer-section">
      <div class="drawer-heading">Пуш-уведомления на телефон</div>
      <ol class="howto">
        <li>Нажмите «Открыть в браузере» — откроется ваша личная ссылка.</li>
        <li><b>iPhone:</b> в Safari «Поделиться» → «На экран Домой». <b>Android:</b> в Chrome ⋮ → «Установить приложение».</li>
        <li>Запустите дневник с иконки на экране и в меню ☰ включите «Пуш на этом устройстве».</li>
      </ol>
      <button class="btn outline" id="pwaOpen">Открыть в браузере</button>
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
      <label class="switch-row"><span>Пропуски по болезни</span><input type="checkbox" switch class="switch" id="demoAbsences"></label>
      <label class="switch-row"><span>Учимся по субботам</span><input type="checkbox" switch class="switch" id="demoSaturday"></label>
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
      ${data.assistant && !demo ? `<button class="drawer-link" id="drawerAssistant">${ICONS.sparkles} ИИ-помощник</button>` : ''}
      ${demo ? '' : `<button class="drawer-link" id="drawerLock">${ICONS.shield} Защита входа</button>`}
      ${demo ? '' : `<button class="drawer-link" id="drawerBug">${ICONS.chat} Сообщить об ошибке</button>`}
      <button class="drawer-link" id="drawerCalc">${ICONS.calc} Калькулятор баллов</button>
      <button class="drawer-link" id="drawerHistory">${ICONS.clock} История оценок</button>
      ${demo ? '' : `<button class="drawer-link" id="drawerCalendar">${ICONS.calendar} Расписание в календаре</button>`}
    </section>
    <section class="drawer-section">
      <div class="drawer-heading">Оформление</div>
      <label class="switch-row design-row"><span>Новый дизайн<span class="design-tag">beta</span></span>
        <input type="checkbox" switch class="switch" id="designNew" ${isNewDesign() ? 'checked' : ''}></label>
      <label class="switch-row"><span>Показывать выходные без уроков</span>
        <input type="checkbox" switch class="switch" id="weekendsShow" ${showWeekends() ? 'checked' : ''}></label>
      <label class="switch-row"><span>Звонки вместо расписания недели</span>
        <input type="checkbox" switch class="switch" id="bellsTab" ${showBells() ? 'checked' : ''}></label>
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
      <div class="drawer-heading">Уведомления</div>
      ${toggle('telegram', 'В Telegram', data.notifications.telegram !== false)}
      ${pushRow()}
      <div class="muted small" id="notifyNowhere" hidden>Сейчас уведомления никуда не приходят.</div>
      <div class="drawer-sub">О чём сообщать</div>
      ${Object.entries(NOTIFY_LABELS)
        .filter(([key]) => !MOBILE_ONLY.has(key) || data.api_mode === 'mobile')
        .map(([key, label]) => toggle(key, label, data.notifications[key]))
        .join('')}
      <div class="muted small">Проверка каждые ${data.interval_minutes} мин</div>
      ${statusLine(data.status)}
    </section>
    ${pushHowto()}
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
  $('weekendsShow').addEventListener('change', (event) => {
    select();
    saveWeekends(event.target.checked);
  });
  $('bellsTab').addEventListener('change', (event) => {
    select();
    saveBells(event.target.checked);
  });
  $('designNew').addEventListener('change', (event) => {
    select();
    saveDesign(event.target.checked);
  });
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
        syncNowhere(Boolean($('pushToggle')?.checked));
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
  $('drawerAssistant')?.addEventListener('click', () => { closeDrawer(); openAssistant(); });
  $('drawerHistory').addEventListener('click', () => { tap(); openHistory(); });
  $('drawerLock')?.addEventListener('click', () => { closeDrawer(); openLockSettings().catch(error => banner(error.message, true)); });
  $('drawerCalendar')?.addEventListener('click', () => { tap(); openCalendar(); });
  $('drawerBug')?.addEventListener('click', () => { tap(); closeDrawer(); openBugReport(); });
  bindPushToggle();
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

/** Стрелка у имени в шапке — если детей несколько: шторка со списком,
 *  нажатие переключает ребёнка (как чипы в меню ☰). */
export function initStudentSwitch(profile) {
  const students = profile?.students || [];
  if (students.length < 2) return;
  const title = $('topTitle');
  // Не disabled для одного ребёнка: выключенную кнопку iOS рисует бледной,
  // и имя в шапке выглядело размытым. Вместо этого — класс.
  title.classList.add('switchable');
  $('titleSwitch').hidden = false;
  title.addEventListener('click', () => {
    tap();
    const body = openSheet('Чей дневник', `<div class="student-list">${students.map((s) =>
      `<button class="student-option ${s.current ? 'on' : ''}" data-student="${s.id}">
        <span class="avatar small" style="background:${studentColor(s.name)}">${esc(initial(s.name))}</span>
        <span class="student-name">${esc(s.name)}</span>
        ${s.current ? '<span class="check">✓</span>' : ''}
      </button>`).join('')}</div>`);
    body.querySelectorAll('[data-student]').forEach((button) => button.addEventListener('click', () => {
      if (button.classList.contains('on')) {
        closeSheet();
        return;
      }
      select();
      switchStudent(button);
    }));
  });
}

const STUDENT_COLORS = ['#4a6fae', '#2e8b6e', '#6b4fa8', '#a8434f', '#c9702f', '#2a8fa3'];

function studentColor(name) {
  let hash = 0;
  for (const char of String(name)) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return STUDENT_COLORS[hash % STUDENT_COLORS.length];
}

function initial(name) {
  // «Иванов Пётр» — по имени, а не по фамилии.
  const parts = String(name).trim().split(/\s+/);
  return (parts[1] || parts[0] || '?')[0].toUpperCase();
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

async function workerRegistration() {
  const { base, token } = window.NETSCHOOL;
  return navigator.serviceWorker.register(
    `${base}/sw.js?token=${encodeURIComponent(token)}`,
    { scope: `${base}/` }
  );
}

async function currentSubscription() {
  try {
    return await (await workerRegistration()).pushManager.getSubscription();
  } catch {
    return null;
  }
}

/** Подсказать, если выключены оба канала. */
function syncNowhere(pushOn) {
  const hint = $('notifyNowhere');
  if (!hint) return;
  const telegramOn = state.profile?.notifications?.telegram !== false;
  hint.hidden = telegramOn || pushOn;
}

async function bindPushToggle() {
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

async function unsubscribePush() {
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
