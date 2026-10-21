// Настройки: ученик, оформление, уведомления, калькулятор. В классическом
// дизайне — выдвижное меню ☰, как в официальном приложении; в новом —
// вкладка «Настройки» с ещё одним разделом: какие вкладки в нижней панели.
'use strict';

import { $, api, banner, demo, esc, initData, state } from './core.js';
import {
  ACCENTS, applyMotion, currentAccent, forgetAccent, HALLOWEEN_ACCENTS, halloweenAccent, halloweenCountdown, isHalloween, isNewDesign, motionChosen, motionLevel, rememberAccent, saveAccent, saveAnswers, saveBells, saveConfetti, savedAccents, saveDesign, saveHalloween, saveHalloweenAccent, saveHomeworkStatus, saveMotion, saveWeekends, showAnswers, showBells, showConfetti, showHomeworkStatus, showWeekends, syncChromeColor, weakDevice,
} from './appearance.js';
import { ICONS } from './icons.js';
import { smoothRepaint } from './motion.js';
import { loadCalc } from './calc.js';
import { openAssistant } from './assistant.js';
import { availableTabs, MAX_TABS, navTabs, resetTabs, saveTabs, TAB_NAMES } from './tabs.js';
import { pushScreen, syncBackButton } from './screens.js';
import { notify, select, tap } from './haptics.js';
import { flushPrefs, getPref, setPref } from './prefs.js';
import { openRetro, RETRO_AUTHOR_URL } from './retro.js';
import { closeSheet, openSheet } from './sheet.js';
import { openBugReport } from './bugreport.js';
import { openLockSettings } from './lock.js';
import { openInBrowser, openInstallGuide } from './install.js';
import { pendingOps, send } from './outbox.js';
import { BELL_SHIFTS, bellShift } from './timer.js';
import { openCalendar } from './caldav.js';
import { openFamily } from './family.js';
import { openHistory } from './history.js';
import { bindPushToggle, pushHowto, pushRow, syncNowhere, unsubscribePush } from './push.js';
import { openExport } from './reports.js';
import { switchStudent } from './students.js';
import { countdownShown, saveCountdown } from './countdown.js';
import { openWhatsNew, whatsNewRow } from './whatsnew.js';
import { openDue } from './due.js';
import { openSearch } from './search.js';
import { openChildren } from './children.js';
import { openScheduleImage } from './schedimage.js';

export async function loadProfile(force = false) {
  if (state.profile && !force) return state.profile;
  const { data } = await api('/api/profile');
  // Переключённое без сети ещё не дошло до сервера (outbox.js).
  for (const { path, body } of pendingOps('settings:')) {
    if (data.notifications) Object.assign(data.notifications, localSettings(path, body));
  }
  state.profile = data;
  return data;
}

/** Что сервер записал бы в notifications по запросу настройки уведомлений. */
function localSettings(path, body) {
  if (path.endsWith('/notifications')) return { [body.key]: body.value };
  if (path.endsWith('/reminder')) return { reminder_hour: body.hour };
  if (path.endsWith('/marks-filter')) return { marks_filter: body.values.length >= 4 ? [] : [...body.values].sort() };
  return {};
}

/** Настройка уведомлений: сразу или, без сети, в очередь. → notifications.
 *  was — прежнее значение: вернули как было — отправлять нечего. */
async function saveSettings(id, path, body, was) {
  const { queued, data } = await send(`settings:${id}`, path, body, was);
  return queued ? { ...state.profile.notifications, ...localSettings(path, body) } : data;
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
  // «Анимации» могли выключить на другом устройстве — приходит с настройками.
  applyMotion();
  syncChromeColor();
}

/** Куда рисовать настройки: вкладка в новом дизайне, меню ☰ в классическом. */
const settingsRoot = () => $(isNewDesign() ? 'settingsContent' : 'drawerBody');

export function isDrawerOpen() {
  return $('drawer').classList.contains('open');
}

export function closeDrawer() {
  if (!isDrawerOpen()) return;
  $('drawer').classList.remove('open');
  syncBackButton();
}

export async function openDrawer() {
  tap();
  $('drawer').classList.add('open');
  syncBackButton();
  await loadSettings();
}

/** Настройки: вкладка «Настройки» или меню ☰. Профиль нужен для имени,
 *  детей и уведомлений. */
export async function loadSettings(force = false) {
  const panel = settingsRoot();
  if (!state.profile || force) {
    if (!state.profile) panel.innerHTML = '<div class="muted small">Загрузка…</div>';
    try {
      await loadProfile(force);
    } catch (error) {
      panel.innerHTML = `<div class="muted small">${esc(error.message)}</div>`;
      return;
    }
  }
  render();
}

/** Нижняя панель: включённые вкладки по порядку, за ними выключенные. */
function tabRows() {
  const on = navTabs();
  const off = availableTabs().filter((tab) => !on.includes(tab));
  const row = (tab, enabled, index) => `<div class="tab-row ${enabled ? '' : 'off'}" data-tab-row="${tab}">
      <input type="checkbox" switch class="switch" data-tab-on="${tab}" ${enabled ? 'checked' : ''} ${tab === 'settings' ? 'disabled' : ''} aria-label="${TAB_NAMES[tab]}">
      <span class="tab-row-name">${TAB_NAMES[tab]}</span>
      ${enabled ? `<button class="tab-move" data-tab-move="${tab}" data-dir="-1" aria-label="Выше" ${index === 0 ? 'disabled' : ''}>${ICONS.up}</button>
      <button class="tab-move down" data-tab-move="${tab}" data-dir="1" aria-label="Ниже" ${index === on.length - 1 ? 'disabled' : ''}>${ICONS.up}</button>` : ''}
    </div>`;
  return on.map((tab, index) => row(tab, true, index)).join('') + off.map((tab) => row(tab, false)).join('');
}

function tabsSection() {
  return `<section class="drawer-section">
      <div class="drawer-heading">Нижняя панель</div>
      ${more('tabs', 'Какие вкладки и в каком порядке', `
      <div class="tab-list" id="tabList">${tabRows()}</div>
      <div class="muted small">Первая открывается при входе. <button class="link" id="tabsReset">Как было</button></div>`)}
    </section>`;
}

// Редкие настройки свёрнуты, чтобы экран не был стеной переключателей.
// Раскрытое остаётся раскрытым при перерисовке (смена цвета, вкладок).
const openMore = new Set();

function more(key, title, body) {
  return `<details class="drawer-more" data-more="${key}"${openMore.has(key) ? ' open' : ''}>
      <summary>${title}</summary>${body}</details>`;
}

function bindTabs() {
  const list = $('tabList');
  if (!list) return;
  // Строка, которую двигали или включали, мягко подсвечивается — видно,
  // куда она переехала.
  const apply = (tabs, touched) => {
    select();
    saveTabs(tabs);
    list.innerHTML = tabRows();
    list.querySelector(`[data-tab-row="${touched}"]`)?.classList.add('moved');
  };
  // Строки перерисовываются, поэтому слушаем сам список.
  list.addEventListener('change', (event) => {
    const input = event.target.closest('[data-tab-on]');
    if (!input) return;
    const tab = input.dataset.tabOn;
    const tabs = navTabs();
    if (input.checked) {
      if (tabs.length >= MAX_TABS) {
        input.checked = false;
        notify('error');
        banner(`Помещается до ${MAX_TABS} вкладок — сначала уберите какую-нибудь`, true, 3000);
        return;
      }
      // Новая вкладка встаёт перед «Настройками».
      const at = tabs.indexOf('settings');
      tabs.splice(at < 0 ? tabs.length : at, 0, tab);
    } else {
      if (tabs.length <= 2) {
        input.checked = true;
        notify('error');
        return;
      }
      tabs.splice(tabs.indexOf(tab), 1);
    }
    apply(tabs, tab);
  });
  list.addEventListener('click', (event) => {
    const button = event.target.closest('[data-tab-move]');
    if (!button) return;
    const tab = button.dataset.tabMove;
    const tabs = navTabs();
    const from = tabs.indexOf(tab);
    const to = from + Number(button.dataset.dir);
    if (to < 0 || to >= tabs.length) return;
    [tabs[from], tabs[to]] = [tabs[to], tabs[from]];
    apply(tabs, tab);
  });
  $('tabsReset').addEventListener('click', () => {
    tap();
    resetTabs();
    list.innerHTML = tabRows();
  });
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
  homework_reminder: 'Напоминать о несделанном Д/З',
  test_reminder: 'Контрольные — за день-два',
  grade_risk: 'Итоговая оценка под угрозой',
  monthly_summary: 'Итоги месяца и четверти',
  weekly_review: 'Разбор недели от помощника',
  vacation_quiet: 'Тихий режим на каникулы',
  all_children: 'Следить за всеми детьми',
};

// Подписи-пояснения под переключателями.
const NOTIFY_NOTES = {
  homework_reminder: 'накануне учебного дня — что ещё не отмечено',
  grade_risk: 'если из-за новой оценки итоговая стала ниже',
  all_children: 'уведомления подписываются именем',
  weekly_review: 'в сводке по понедельникам: где просел и что подтянуть',
  vacation_quiet: 'в каникулы и летом уведомления молчат',
};

// Отметки посещаемости есть только в мобильном API.
const MOBILE_ONLY = new Set(['absences']);

/** «Сохранять файлы заданий»: включено у всех, здесь можно выключить. */
function keepFilesSection(keep) {
  if (!keep?.available) return '';
  return `<section class="drawer-section" id="keepFilesSection">
      <div class="drawer-heading">Файлы заданий</div>
      <label class="switch-row"><span>Сохранять файлы заданий<span class="muted small switch-note">файлы к Д/З за 2 недели назад и вперёд скачиваются заранее и открываются, даже когда «Сетевой город» не работает</span></span>
        <input type="checkbox" switch class="switch" id="keepFiles" ${keep.on ? 'checked' : ''}></label>
    </section>`;
}

/** «Не выходить из аккаунта» — вход через Госуслуги держится на мобильном токене. */
function keepSignedInSection(keep) {
  if (!keep?.available) return '';
  const note = keep.mobile ? 'вход уже мобильный и продлевается сам'
    : 'бот переведёт вход на мобильный — он продлевается сам, без нового входа через Госуслуги';
  return `<section class="drawer-section">
      <div class="drawer-heading">Госуслуги</div>
      <label class="switch-row"><span>Не выходить из аккаунта<span class="muted small switch-note">${note}</span></span>
        <input type="checkbox" switch class="switch" id="keepSignedIn" ${keep.on ? 'checked' : ''}></label>
    </section>`;
}

/** «Решения заданий всему классу» — тумблер владельца (web/solver.py). */
function solverClassSection(solverClass) {
  if (!solverClass) return '';
  return `<section class="drawer-section">
      <div class="drawer-heading">Решения</div>
      <label class="switch-row"><span>Решения заданий всему классу<span class="muted small switch-note">видно только вам: кнопка «Решение» появится у одноклассников, которые пользуются ботом</span></span>
        <input type="checkbox" switch class="switch" id="solverClass" ${solverClass.on ? 'checked' : ''}></label>
    </section>`;
}

// Атрибут switch — системный переключатель: в Safari на iPhone он сам
// тактильно щёлкает от касания (vibrate там нет, см. haptics.js).
function toggle(key, label, on) {
  const note = NOTIFY_NOTES[key] ? `<span class="muted small switch-note">${NOTIFY_NOTES[key]}</span>` : '';
  return `<label class="switch-row"><span>${label}${note}</span>
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
  const rows = [`Последняя успешная проверка вашего дневника: ${when(status.last_ok_at)}`];
  if (failing) rows.push(`Ошибка ${when(status.last_error_at)}: ${esc(status.last_error)}`);
  if (status.running && status.next_check_at) rows.push(`Следующая: ${when(status.next_check_at)}`);
  if (status.queued) rows.push(`Ждут отправки: ${status.queued}`);
  return `<div class="muted small sync-status ${failing ? 'failing' : ''}">${rows.join('<br>')}</div>`;
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

// Класс ученика: отдельным запросом — профиль не ждёт ответа школы.
const classLabels = new Map();

async function loadStudentClass(studentId) {
  if (demo || classLabels.has(studentId)) return;
  classLabels.set(studentId, '');
  try {
    const { data } = await api('/api/student-class');
    classLabels.set(studentId, data.label || '');
  } catch {
    classLabels.delete(studentId);
    return;
  }
  const line = $('studentClass');
  const current = state.profile?.students?.find((s) => s.current)?.id ?? null;
  if (line && current === studentId && classLabels.get(studentId)) {
    line.textContent = classLabels.get(studentId);
    line.hidden = false;
  }
}

function render() {
  const data = state.profile;
  const currentStudent = data.students.find((s) => s.current)?.id ?? null;
  const studentClass = classLabels.get(currentStudent) || '';
  const theme = currentTheme();
  // В Хеллоуин — свои цвета и свой выбранный: отмеченный кружок совпадает с экраном.
  const spooky = isHalloween();
  const palette = spooky ? HALLOWEEN_ACCENTS : ACCENTS;
  const accent = spooky ? halloweenAccent() : currentAccent();
  // Дневник ученика по приглашению: его уведомления и почту не трогаем.
  const readonly = Boolean(data.readonly);
  const views = data.views || [];
  const saved = spooky ? [] : savedAccents().filter((color) => !ACCENTS.some(([c]) => c === color));
  // Свой цвет, ещё не сохранённый: его можно оставить кружком рядом с готовыми.
  const customAccent = !palette.some(([c]) => c === accent) && !saved.includes(accent);
  const radio = (value, label) =>
    `<label class="drawer-option"><input type="radio" name="theme" value="${value}" ${theme === value ? 'checked' : ''}><span>${label}</span></label>`;
  // Новый дизайн: тема — капсулой с линзой, как переключатель периода в «Оценках».
  const themes = [['light', 'Светлая'], ['auto', 'Авто'], ['dark', 'Тёмная']];
  const themeIndex = Math.max(0, themes.findIndex(([value]) => value === theme));
  const themeSegment = `<div class="drawer-sub">Тема</div>
      <div class="seg-glass theme-seg" role="radiogroup" aria-label="Тема" style="--n:3;--i:${themeIndex}">
        <span class="seg-lens" aria-hidden="true"></span>
        ${themes.map(([value, label]) => `<button type="button" role="radio" class="${value === theme ? 'on' : ''}" aria-checked="${value === theme}" data-theme-set="${value}">${label}</button>`).join('')}
      </div>`;

  // Настройки живут в одном месте: копия в другом (вкладка или меню ☰)
  // осталась бы с теми же id, и обработчики вешались бы на неё — после
  // смены дизайна переключатели переставали работать до перезагрузки.
  $(isNewDesign() ? 'drawerBody' : 'settingsContent').replaceChildren();
  settingsRoot().innerHTML = `
    ${demo ? '<div class="demo-badge">Демо-режим · данные выдуманные</div>' : ''}
    ${pushHowto()}
    <section class="drawer-section">
      <div class="drawer-heading">Аккаунт ученика</div>
      ${readonly ? '<div class="view-badge">Просмотр дневника по приглашению · только чтение</div>' : ''}
      <div class="drawer-name">${esc(data.name)}</div>
      <div class="muted small" id="studentClass"${studentClass ? '' : ' hidden'}>${esc(studentClass)}</div>
      <div class="muted small">${esc(data.school)}</div>
      ${views.length > 1 ? `<div class="drawer-sub">Чей дневник открыть</div><div class="view-switch">${views
        .map((v) => `<button class="chip ${v.current ? 'active' : ''}" data-view="${v.id ?? ''}" aria-pressed="${v.current}">${esc(v.name)}</button>`)
        .join('')}</div>` : ''}
      ${!readonly && data.students.length > 1 ? `<div class="drawer-children">${data.students
        .map((s) => `<button class="chip ${s.current ? 'active' : ''}" data-student="${s.id}">${esc(s.name)}</button>`)
        .join('')}</div>` : ''}
      ${demo || (initData && readonly) ? '' : `<div class="account-links">
        <button class="drawer-link drawer-logout" id="drawerLogout">${ICONS.logout} Выйти из аккаунта</button>
      </div>`}
    </section>
    <section class="drawer-section">
      <div class="drawer-heading">Возможности</div>
      ${demo ? '' : `<div class="drawer-sub">Вход и защита</div>
      <div class="tool-grid tool-grid-account">
      ${toolTile('drawerAccount', 'mail', 'Вход по почте')}
      ${toolTile('drawerLock', 'shield', 'PIN-код')}
      ${toolTile('drawerDevices', 'install', 'Устройства')}
      </div>
      <div class="drawer-sub">Полезное</div>`}
      <div class="tool-grid">
      ${data.assistant && !demo && !readonly && !navTabs().includes('assistant') ? toolTile('drawerAssistant', 'sparkles', 'ИИ-помощник') : ''}
      ${navTabs().includes('games') ? '' : toolTile('drawerGames', 'game', 'Игры')}
      ${toolTile('drawerDue', 'pin', 'Должен сдать')}
      ${toolTile('drawerSearch', 'search', 'Поиск за год')}
      ${!readonly && data.students.length > 1 ? toolTile('drawerChildren', 'users', 'Все дети') : ''}
      ${toolTile('drawerShareWeek', 'share', 'Расписание картинкой')}
      ${toolTile('drawerCalc', 'calc', 'Калькулятор баллов')}
      ${toolTile('drawerHistory', 'clock', 'История оценок')}
      ${toolTile('drawerStatus', 'pulse', 'Статус серверов')}
      ${demo ? '' : toolTile('drawerExport', 'table', 'Отчёты в Excel')}
      ${demo ? '' : toolTile('drawerFamily', 'users', 'Родные')}
      ${demo || readonly ? '' : toolTile('drawerCalendar', 'calendar', 'Расписание в календарь')}
      ${data.api_mode === 'mobile' && !demo && !readonly ? `${toolTile('drawerEvents', 'trophy', 'Олимпиады')}
      ${toolTile('drawerReferences', 'doc', 'Заказать справку', referencesAllowed !== true)}` : ''}
      </div>
    </section>
    <section class="drawer-section">
      <div class="drawer-heading">Оформление</div>
      ${data.halloween ? `<label class="switch-row halloween-row"><span>🎃 Хеллоуин<span class="muted small switch-note">${halloweenCountdown()}</span></span>
        <input type="checkbox" switch class="switch" id="halloweenOn" ${isHalloween() ? 'checked' : ''}></label>` : ''}
      ${spooky ? '<div class="muted small halloween-note">В Хеллоуин тема всегда ночная. Светлая и свой цвет вернутся, когда выключите праздник.</div>'
        : isNewDesign() ? themeSegment : `${radio('light', 'Светлая тема')}
      ${radio('auto', 'Как в системе')}
      ${radio('dark', 'Тёмная тема')}`}
      <div class="drawer-sub">${ICONS.palette} ${spooky ? 'Цвет на Хеллоуин' : isNewDesign() ? 'Цвет акцента' : 'Цвет меню'}</div>
      <div class="swatches">
        ${palette.map(([color, name]) => `<button class="swatch ${color === accent ? 'on' : ''}" data-accent="${color}" style="background:${color}" aria-label="${name}" title="${name}"></button>`).join('')}
        ${saved.map((color) => `<button class="swatch saved ${color === accent ? 'on' : ''}" data-accent="${color}" style="background:${color}" aria-label="Сохранённый цвет ${color}" title="Сохранённый цвет"></button>`).join('')}
        <label class="swatch custom ${customAccent ? 'on' : ''}" title="Свой цвет" style="background:${accent}">
          <input type="color" id="accentCustom" value="${accent}">＋</label>
      </div>
      ${spooky ? '' : customAccent ? '<button class="link accent-action" id="accentRemember">Сохранить этот цвет</button>'
        : saved.includes(accent) ? '<button class="link accent-action" id="accentForget">Убрать из сохранённых</button>' : ''}
      ${more('look', 'Ещё настройки вида', `
      <label class="switch-row design-row"><span>Старый дизайн</span>
        <input type="checkbox" switch class="switch" id="designOld" ${isNewDesign() ? '' : 'checked'}></label>
      <label class="switch-row retro-row"><span>📟 Ретро-сайт<span class="muted small switch-note">дневник страничкой из 90-х: звёзды, котики, окна Windows 98</span></span>
        <input type="checkbox" switch class="switch" id="retroSite"></label>
      <div class="muted small retro-credit">Разработано: <a href="${RETRO_AUTHOR_URL}" target="_blank" rel="noopener">faustyu1</a> (netschool-retro). Спасибо!</div>
      <label class="switch-row"><span>Подсвечивать Д/З на главной<span class="muted small switch-note">красным — не сделано, зелёным — сделано</span></span>
        <input type="checkbox" switch class="switch" id="hwStatusShow" ${showHomeworkStatus() ? 'checked' : ''}></label>
      ${data.api_mode === 'mobile' ? `<label class="switch-row"><span>Отправка ответа на задание<span class="muted small switch-note">блок «Мой ответ» на экране задания</span></span>
        <input type="checkbox" switch class="switch" id="answersShow" ${showAnswers() ? 'checked' : ''}></label>` : ''}
      ${motionRow()}
      <label class="switch-row"><span>Конфетти за пятёрку<span class="muted small switch-note">когда приходит новая 5</span></span>
        <input type="checkbox" switch class="switch" id="confettiShow" ${showConfetti() ? 'checked' : ''}></label>
      <label class="switch-row"><span>Сдвиг звонка<span class="muted small switch-note">таймер урока: если звонок звенит позже или раньше ровной минуты</span></span>
        <select class="field reminder-hour" id="bellShift">${BELL_SHIFTS.map((value) =>
          `<option value="${value}" ${value === bellShift() ? 'selected' : ''}>${value ? `${value > 0 ? '+' : '−'}${Math.abs(value)} с` : 'точно'}</option>`).join('')}</select></label>
      <label class="switch-row"><span>Отсчёт до каникул и экзаменов<span class="muted small switch-note">карточка на «Дне»</span></span>
        <input type="checkbox" switch class="switch" id="countdownShow" ${countdownShown() ? 'checked' : ''}></label>
      <label class="switch-row"><span>Показывать выходные без уроков</span>
        <input type="checkbox" switch class="switch" id="weekendsShow" ${showWeekends() ? 'checked' : ''}></label>
      ${isNewDesign() ? '' : `<label class="switch-row"><span>Звонки вместо расписания недели</span>
        <input type="checkbox" switch class="switch" id="bellsTab" ${showBells() ? 'checked' : ''}></label>`}`)}
    </section>
    ${isNewDesign() ? tabsSection() : ''}
    ${readonly ? '' : keepFilesSection(data.keep_attachments)}
    ${readonly ? '' : solverClassSection(data.solver_class)}
    ${readonly ? '' : keepSignedInSection(data.keep_signed_in)}
    ${readonly ? `<section class="drawer-section">
      <div class="drawer-heading">Уведомления</div>
      <div class="muted small">Уведомления об этом ученике приходят, если он разрешил их в приглашении. Отказаться — «Родные».</div>
    </section>` : `<section class="drawer-section">
      <div class="drawer-heading">Уведомления</div>
      ${toggle('telegram', 'В Telegram', data.notifications.telegram !== false)}
      ${pushRow()}
      <div class="muted small" id="notifyNowhere" hidden>Сейчас уведомления никуда не приходят.</div>
      ${more('notify', 'О чём сообщать', `
      ${Object.entries(NOTIFY_LABELS)
        .filter(([key]) => !MOBILE_ONLY.has(key) || data.api_mode === 'mobile')
        .filter(([key]) => key !== 'all_children' || data.students.length > 1)
        .filter(([key]) => key !== 'weekly_review' || data.assistant)
        .map(([key, label]) => toggle(key, label, data.notifications[key]))
        .join('')}
      <label class="switch-row"><span>Обновления «Сетевого города»<span class="muted small switch-note">новая версия на сервере вашего региона — в Telegram и push</span></span>
        <input type="checkbox" switch class="switch" id="sgoUpdates" ${data.sgo_updates ? 'checked' : ''}></label>
      <label class="switch-row"><span>Время напоминания</span>
        <select class="field reminder-hour" id="reminderHour">${[15, 16, 17, 18, 19, 20, 21].map((hour) =>
          `<option value="${hour}" ${hour === (data.notifications.reminder_hour ?? 18) ? 'selected' : ''}>${hour}:00</option>`).join('')}</select></label>
      <div class="drawer-sub">Какие новые оценки присылать</div>
      <div class="chips marks-filter" id="marksFilter">${[5, 4, 3, 2].map((value) => {
        const chosen = data.notifications.marks_filter || [];
        const on = !chosen.length || chosen.includes(value);
        return `<button class="chip ${on ? 'active' : ''}" data-mark-value="${value}" aria-pressed="${on}">${value}</button>`;
      }).join('')}</div>
      <div class="muted small">Изменения и удаления оценок приходят всегда.</div>`)}
      <div class="muted small">Проверка каждые ${data.interval_minutes} мин</div>
      ${statusLine(data.status)}
    </section>`}
    ${demoSection()}
    ${whatsNewRow()}
    <div class="drawer-foot muted small">Дневник «Сетевого города»${window.NETSCHOOL.appVersion ? ` · v. ${esc(window.NETSCHOOL.appVersion)}` : ''}
      ${demo || !data.bug_report ? '' : `<button class="link small" id="drawerBug">Сообщить об ошибке или идее</button>`}</div>`;

  bindDrawer();
  loadStudentClass(currentStudent);
  checkReferences();
}

// Что остаётся на каждом уровне «Анимаций» — подпись под ползунком.
const MOTION_NOTES = {
  0: 'всё без движения',
  25: 'только переходы между экранами и шторки',
  50: 'для старых телефонов: без размытия, счётчиков и конфетти',
  75: 'без украшений: волн от касаний и фоновых анимаций',
  100: 'все анимации',
};

/** «Анимации» — ползунок в процентах, шагами по 25. */
function motionRow() {
  const level = motionLevel();
  return `<div class="motion-row">
        <label class="motion-head" for="motionLevel"><span>Анимации</span><b id="motionValue">${level}%</b></label>
        <input type="range" class="motion-range" id="motionLevel" min="0" max="100" step="25" value="${level}" style="--p:${level}%">
        <div class="motion-ticks" aria-hidden="true"><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></div>
        <div class="muted small" id="motionNote">${motionNote(level)}</div>
      </div>`;
}

function motionNote(level) {
  const auto = !motionChosen() && weakDevice() ? ' · подобрано под этот телефон' : '';
  return MOTION_NOTES[level] + auto;
}

/** Ползунок двигают — число и подпись меняются сразу, сохраняется по отпусканию. */
function showMotionLevel(level) {
  $('motionValue').textContent = `${level}%`;
  $('motionNote').textContent = MOTION_NOTES[level];
  $('motionLevel').style.setProperty('--p', `${level}%`);
}

/** Плитка инструмента: значок над подписью — восемь пунктов помещаются в
 *  три ряда, а не в восемь строк. */
function toolTile(id, icon, label, hidden = false) {
  return `<button class="tool-tile" id="${id}"${hidden ? ' hidden' : ''}><span class="tool-ico">${ICONS[icon]}</span><span>${label}</span></button>`;
}

// Заказ справок школьный сервер разрешает не всем учётным записям (ученику
// обычно отказывает). Кнопка показывается, только когда он ответил списком
// справок; ответ помнится до перезапуска.
let referencesAllowed = null;
let referencesCheck = null;
function checkReferences() {
  if (!$('drawerReferences') || referencesAllowed !== null) return;
  referencesCheck ||= api('/api/references/types')
    .then(({ data }) => { referencesAllowed = Boolean(data.available && data.items?.length); })
    .catch(() => { referencesCheck = null; });
  referencesCheck.then(() => {
    const button = $('drawerReferences');
    if (button) button.hidden = referencesAllowed !== true;
  });
}

function bindDrawer() {
  const body = settingsRoot();
  body.querySelectorAll('[data-more]').forEach((details) => details.addEventListener('toggle', () => {
    if (details.open) openMore.add(details.dataset.more);
    else openMore.delete(details.dataset.more);
  }));
  body.querySelectorAll('input[name="theme"]').forEach((input) =>
    input.addEventListener('change', () => {
      select();
      setPref('theme', input.value);
      smoothRepaint(applyStoredTheme);
    })
  );
  const themeSeg = body.querySelector('.theme-seg');
  themeSeg?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-theme-set]');
    if (!button || button.classList.contains('on')) return;
    select();
    const buttons = [...themeSeg.querySelectorAll('[data-theme-set]')];
    buttons.forEach((item) => {
      item.classList.toggle('on', item === button);
      item.setAttribute('aria-checked', String(item === button));
    });
    themeSeg.style.setProperty('--i', buttons.indexOf(button));
    setPref('theme', button.dataset.themeSet);
    smoothRepaint(applyStoredTheme);
  });
  $('hwStatusShow').addEventListener('change', (event) => {
    select();
    smoothRepaint(() => saveHomeworkStatus(event.target.checked));
  });
  $('answersShow')?.addEventListener('change', (event) => {
    select();
    saveAnswers(event.target.checked);
  });
  const motion = $('motionLevel');
  motion.addEventListener('input', () => {
    select();
    showMotionLevel(Number(motion.value));
  });
  motion.addEventListener('change', () => saveMotion(Number(motion.value)));
  $('confettiShow').addEventListener('change', (event) => {
    select();
    saveConfetti(event.target.checked);
  });
  $('bellShift').addEventListener('change', (event) => {
    select();
    setPref('bellShift', event.target.value);
  });
  $('countdownShow').addEventListener('change', (event) => {
    select();
    smoothRepaint(() => saveCountdown(event.target.checked));
  });
  $('keepSignedIn')?.addEventListener('change', (event) => {
    select();
    setPref('keepSignedIn', event.target.checked);
    state.profile.keep_signed_in = { ...state.profile.keep_signed_in, on: event.target.checked };
  });
  $('drawerWhatsNew').addEventListener('click', () => { closeDrawer(); openWhatsNew(); });
  $('drawerDue').addEventListener('click', () => { closeDrawer(); openDue(); });
  $('drawerSearch').addEventListener('click', () => { closeDrawer(); openSearch(); });
  $('drawerChildren')?.addEventListener('click', () => { closeDrawer(); openChildren(); });
  $('drawerShareWeek').addEventListener('click', () => { closeDrawer(); openScheduleImage(); });
  $('weekendsShow').addEventListener('change', (event) => {
    select();
    smoothRepaint(() => saveWeekends(event.target.checked));
  });
  $('bellsTab')?.addEventListener('change', (event) => {
    select();
    smoothRepaint(() => saveBells(event.target.checked));
  });
  $('halloweenOn')?.addEventListener('change', (event) => {
    select();
    smoothRepaint(() => {
      saveHalloween(event.target.checked);
      render();
    });
  });
  $('retroSite').addEventListener('change', async (event) => {
    if (!event.target.checked) return;
    select();
    setPref('retro', 'on');
    await flushPrefs().catch(() => {});
    openRetro();
  });
  $('designOld').addEventListener('change', (event) => {
    select();
    smoothRepaint(() => saveDesign(!event.target.checked));
  });
  body.querySelectorAll('[data-accent]').forEach((button) =>
    button.addEventListener('click', () => {
      select();
      smoothRepaint(() => {
        (isHalloween() ? saveHalloweenAccent : saveAccent)(button.dataset.accent);
        render();
      });
    })
  );
  $('accentCustom').addEventListener('change', (event) => {
    select();
    smoothRepaint(() => {
      (isHalloween() ? saveHalloweenAccent : saveAccent)(event.target.value);
      render();
    });
  });
  $('accentRemember')?.addEventListener('click', () => {
    select();
    rememberAccent(currentAccent());
    notify('success');
    render();
  });
  $('accentForget')?.addEventListener('click', () => {
    select();
    forgetAccent(currentAccent());
    render();
  });
  body.querySelectorAll('[data-notify]').forEach((input) =>
    input.addEventListener('change', async () => {
      select();
      input.disabled = true;
      try {
        state.profile.notifications = await saveSettings(`notify:${input.dataset.notify}`, '/api/settings/notifications',
          { key: input.dataset.notify, value: input.checked }, { value: !input.checked });
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
  for (const [id, key] of [['keepFiles', 'on']]) {
    $(id)?.addEventListener('change', async (event) => {
      select();
      const input = event.target;
      input.disabled = true;
      try {
        const { data } = await api('/api/settings/keep-attachments', {
          method: 'POST', body: JSON.stringify({ [key]: input.checked }),
        });
        state.profile.keep_attachments = data;
        input.checked = Boolean(data[key]);
      } catch (error) {
        notify('error');
        input.checked = !input.checked;
        banner(error.message, true);
      } finally {
        input.disabled = false;
      }
    });
  }
  $('solverClass')?.addEventListener('change', async (event) => {
    select();
    const input = event.target;
    input.disabled = true;
    try {
      const { data } = await api('/api/settings/solver-class', {
        method: 'POST', body: JSON.stringify({ on: input.checked }),
      });
      state.profile.solver_class = data;
      input.checked = Boolean(data.on);
    } catch (error) {
      notify('error');
      input.checked = !input.checked;
      banner(error.message, true);
    } finally {
      input.disabled = false;
    }
  });
  $('sgoUpdates')?.addEventListener('change', async (event) => {
    select();
    const input = event.target;
    input.disabled = true;
    try {
      const { data } = await api('/api/sgo/notify', {
        method: 'POST', body: JSON.stringify({ kind: 'updates', on: input.checked }),
      });
      state.profile.sgo_updates = Boolean(data.subscribed?.updates);
      input.checked = state.profile.sgo_updates;
    } catch (error) {
      notify('error');
      input.checked = !input.checked;
      banner(error.message, true);
    } finally {
      input.disabled = false;
    }
  });
  $('reminderHour')?.addEventListener('change', async (event) => {
    select();
    try {
      state.profile.notifications = await saveSettings('reminder', '/api/settings/reminder', { hour: Number(event.target.value) },
        { hour: state.profile.notifications.reminder_hour ?? 18 });
    } catch (error) {
      notify('error');
      banner(error.message, true);
    }
  });
  $('marksFilter')?.addEventListener('click', async (event) => {
    const chip = event.target.closest('[data-mark-value]');
    if (!chip) return;
    const chips = [...$('marksFilter').querySelectorAll('[data-mark-value]')];
    chip.classList.toggle('active');
    const values = chips.filter((c) => c.classList.contains('active')).map((c) => Number(c.dataset.markValue));
    if (!values.length) {
      chip.classList.add('active');
      notify('error');
      banner('Хотя бы одна оценка должна остаться', true, 2500);
      return;
    }
    select();
    chips.forEach((c) => c.setAttribute('aria-pressed', String(c.classList.contains('active'))));
    try {
      // Прежний выбор в том же порядке, что у кнопок: пустой список — все оценки.
      const before = state.profile.notifications.marks_filter || [];
      state.profile.notifications = await saveSettings('marks-filter', '/api/settings/marks-filter', { values },
        { values: chips.map((c) => Number(c.dataset.markValue)).filter((v) => !before.length || before.includes(v)) });
    } catch (error) {
      chip.classList.toggle('active');
      notify('error');
      banner(error.message, true);
    }
  });
  $('drawerExport')?.addEventListener('click', () => { closeDrawer(); openExport(); });
  $('drawerFamily')?.addEventListener('click', () => { closeDrawer(); openFamily(); });
  body.querySelectorAll('[data-view]').forEach((chip) => chip.addEventListener('click', async () => {
    if (chip.classList.contains('active')) return;
    tap();
    chip.disabled = true;
    try {
      await api('/api/view', { method: 'POST', body: JSON.stringify({ id: chip.dataset.view ? Number(chip.dataset.view) : null }) });
      notify('success');
      // Другой дневник — совсем другие данные, как при смене ребёнка.
      location.reload();
    } catch (error) {
      notify('error');
      banner(error.message, true);
      chip.disabled = false;
    }
  }));
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
  $('drawerGames')?.addEventListener('click', () => {
    closeDrawer();
    window.dispatchEvent(new CustomEvent('netschool:tab', { detail: 'games' }));
  });
  $('drawerHistory').addEventListener('click', () => { tap(); openHistory(); });
  $('drawerStatus').addEventListener('click', async () => {
    closeDrawer();
    const { openStatus } = await import('./sgo.js');
    openStatus();
  });
  $('drawerEvents')?.addEventListener('click', async () => {
    closeDrawer();
    const { openEvents } = await import('./events.js');
    openEvents();
  });
  $('drawerReferences')?.addEventListener('click', async () => {
    closeDrawer();
    const { openReferences } = await import('./events.js');
    openReferences();
  });
  $('drawerLock')?.addEventListener('click', () => { closeDrawer(); openLockSettings().catch(error => banner(error.message, true)); });
  $('drawerCalendar')?.addEventListener('click', () => { tap(); openCalendar(); });
  $('drawerDevices')?.addEventListener('click', async () => {
    tap();
    closeDrawer();
    const { openDevices } = await import('./devices.js');
    openDevices();
  });
  $('drawerAccount')?.addEventListener('click', async () => {
    tap();
    closeDrawer();
    const { openAccountSettings } = await import('./account.js');
    openAccountSettings();
  });
  $('drawerLogout')?.addEventListener('click', () => { tap(); chooseSignOut(Boolean(state.profile?.readonly)); });
  $('drawerBug')?.addEventListener('click', () => { tap(); closeDrawer(); openBugReport(); });
  bindTabs();
  bindPushToggle();
  $('pwaOpen')?.addEventListener('click', openInBrowser);
  $('installGuide')?.addEventListener('click', openInstallGuide);
  if (demo) bindDemo();
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

/** «Выйти из аккаунта»: из «Сетевого города» (школа, пароль) или из самого
 * приложения на этом устройстве. В Telegram вход в приложение — сам
 * Telegram, там остаётся только «Сетевой город»; при просмотре чужого
 * дневника своего входа в школу нет — только приложение. */
function chooseSignOut(readonly) {
  const options = [
    !readonly && ['netschool', 'Из «Сетевого города»',
      'Сменили пароль или школу — войдёте заново, приложение и настройки останутся'],
    !initData && ['app', 'Из приложения', 'На этом устройстве: дневник закроется, уведомления сюда приходить перестанут'],
  ].filter(Boolean);
  const body = openSheet('Выйти из аккаунта', `<div class="student-list">${options.map(([id, title, note]) =>
    `<button class="student-option" data-out="${id}">
      <span class="student-name">${title}<br><span class="muted small">${note}</span></span>
    </button>`).join('')}</div>`);
  body.querySelector('[data-out="netschool"]')?.addEventListener('click', () => { tap(); closeSheet(); closeDrawer(); signOutNetschool(); });
  body.querySelector('[data-out="app"]')?.addEventListener('click', () => { tap(); closeSheet(); closeDrawer(); signOut(); });
}

/** Выйти из «Сетевого города», оставшись в приложении (как /logout в боте):
 * после перезагрузки приложение само откроет вход в школу. */
async function signOutNetschool() {
  if (!window.confirm('Выйти из «Сетевого города»? Школу и вход придётся выбрать заново. '
    + 'Настройки, отметки о сделанных заданиях и история оценок вернутся, если войти в тот же дневник.')) return;
  try {
    await api('/api/account/netschool-logout', { method: 'POST', body: '{}' });
  } catch (error) {
    banner(error.message, true);
    return;
  }
  const { clearOffline } = await import('./lock.js');
  await clearOffline().catch(() => {});
  try {
    ['ns-viewing', 'ns-seen-marks', 'ns-shown-averages', 'ns-totals-counts'].forEach((key) => localStorage.removeItem(key));
  } catch { /* приватный режим */ }
  location.reload();
}

/** «Выйти из аккаунта» на сайте и в установленном приложении (в Telegram
 * вход — сам Telegram, выходить там не из чего). Отписывает устройство от
 * push, отзывает токен этого устройства, стирает офлайн-копию дневника и
 * запоминает выход: иначе иконка на домашнем экране, где токен вшит в
 * адрес, тут же входила бы обратно (см. boot.js). */
export async function signOut({ confirmed = false } = {}) {
  if (!confirmed && !window.confirm('Выйти из аккаунта на этом устройстве? Уведомления сюда приходить перестанут. '
    + 'Войти снова можно через Telegram-бота или по почте.')) return;
  await unsubscribePush().catch(() => {});
  await api('/api/account/logout', { method: 'POST', body: '{}' }).catch(() => {});
  const { clearOffline } = await import('./lock.js');
  await clearOffline().catch(() => {});
  try {
    localStorage.setItem('ns-signed-out', window.NETSCHOOL.token || '1');
    ['ns-viewing', 'ns-seen-marks', 'ns-shown-averages', 'ns-totals-counts'].forEach((key) => localStorage.removeItem(key));
  } catch { /* приватный режим */ }
  location.replace(`${window.NETSCHOOL.base}/`);
}
