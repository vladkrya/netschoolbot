// Настройки: ученик, оформление, уведомления, калькулятор. В классическом
// дизайне — выдвижное меню ☰, как в официальном приложении; в новом —
// вкладка «Настройки» с ещё одним разделом: какие вкладки в нижней панели.
'use strict';

import { $, api, apiBlob, banner, demo, errorCard, esc, initData, pushEnabled, skeleton, state, tg } from './core.js';
import {
  ACCENTS, currentAccent, isNewDesign, saveAccent, saveBells, saveDesign, saveHomeworkStatus, saveWeekends, showBells,
  showHomeworkStatus, showWeekends, syncChromeColor,
} from './appearance.js';
import { ICONS } from './icons.js';
import { markBox } from './details.js';
import { loadCalc } from './calc.js';
import { openAssistant } from './assistant.js';
import { MAX_TABS, TAB_NAMES, availableTabs, navTabs, resetTabs, saveTabs } from './tabs.js';
import { loadTotals } from './totals.js';
import { pushScreen, syncBackButton } from './screens.js';
import { notify, select, tap } from './haptics.js';
import { getPref, setPref } from './prefs.js';
import { closeSheet, openSheet } from './sheet.js';
import { openBugReport } from './bugreport.js';
import { openLockSettings } from './lock.js';
import { canInstall, openInBrowser, openInstallGuide, wantsInstall } from './install.js';

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
      <div class="tab-list" id="tabList">${tabRows()}</div>
      <div class="muted small">Первая открывается при входе. <button class="link" id="tabsReset">Как было</button></div>
    </section>`;
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
  all_children: 'Следить за всеми детьми',
};

// Подписи-пояснения под переключателями.
const NOTIFY_NOTES = {
  homework_reminder: 'накануне учебного дня — что ещё не отмечено',
  grade_risk: 'если из-за новой оценки итоговая стала ниже',
  all_children: 'уведомления подписываются именем',
};

// Отметки посещаемости есть только в мобильном API.
const MOBILE_ONLY = new Set(['absences']);

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
  const rows = [`Последняя успешная проверка: ${when(status.last_ok_at)}`];
  if (failing) rows.push(`Ошибка ${when(status.last_error_at)}: ${esc(status.last_error)}`);
  if (status.running && status.next_check_at) rows.push(`Следующая: ${when(status.next_check_at)}`);
  if (status.queued) rows.push(`Ждут отправки: ${status.queued}`);
  return `<div class="muted small sync-status ${failing ? 'failing' : ''}">${rows.join('<br>')}</div>`;
}

/** Оценки в Excel: в Telegram — файлом в чат с ботом, в браузере — скачиванием. */
function openExport() {
  tap();
  const body = openSheet('Оценки в Excel', `
    <p class="muted small">Лист «Итоги» — средние и выходящие оценки по предметам, «Оценки» — все работы с датами и весом.</p>
    <div class="export-choices">
      <button class="btn" data-export="period">Текущий период</button>
      <button class="btn outline" data-export="year">Весь учебный год</button>
    </div>
    <div class="muted small export-status"></div>`);
  const status = body.querySelector('.export-status');
  body.querySelectorAll('[data-export]').forEach((button) => button.addEventListener('click', async () => {
    select();
    const scope = button.dataset.export;
    body.querySelectorAll('[data-export]').forEach((b) => { b.disabled = true; });
    status.textContent = 'Собираю оценки — за год это может занять до минуты…';
    try {
      if (initData) {
        await api('/api/export/send', { method: 'POST', body: JSON.stringify({ scope }) });
        status.textContent = 'Готово: файл в чате с ботом.';
      } else {
        const blob = await apiBlob(`/api/export.xlsx?scope=${scope}`);
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob.data);
        link.download = blob.name || 'Оценки.xlsx';
        document.body.appendChild(link);
        link.click();
        setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 1000);
        status.textContent = 'Файл скачан.';
      }
      notify('success');
    } catch (error) {
      notify('error');
      status.textContent = error.message;
    } finally {
      body.querySelectorAll('[data-export]').forEach((b) => { b.disabled = false; });
    }
  }));
}

/** Второй родитель: ссылка-приглашение и кто получает копии уведомлений. */
async function openFamily() {
  tap();
  const screen = pushScreen('Второй родитель');
  screen.innerHTML = skeleton(3);
  const draw = (data) => {
    screen.innerHTML = `<div class="card">
        <p>Второй родитель будет получать в Telegram те же уведомления: оценки, задания и сводки.
        Пароль от «Сетевого города» ему не нужен, менять что-либо в дневнике он не сможет.</p>
        <button class="btn" id="familyInvite">Пригласить по ссылке</button>
        <div class="family-link" id="familyLink" hidden></div>
      </div>
      ${data.followers.length ? `<div class="card"><div class="card-title">Получают копии</div>${data.followers.map((f) =>
        `<div class="family-row"><span>${esc(f.name || `ID ${f.id}`)}</span><button class="btn outline small" data-drop="${f.id}">Убрать</button></div>`).join('')}</div>` : ''}
      ${data.following.length ? `<div class="card"><div class="card-title">Вы получаете уведомления об</div>${data.following.map((f) =>
        `<div class="family-row"><span>${esc(f.name)}</span><button class="btn outline small" data-leave="${f.id}">Не получать</button></div>`).join('')}</div>` : ''}`;
    $('familyInvite').addEventListener('click', async (event) => {
      select();
      event.currentTarget.disabled = true;
      try {
        const { data: invite } = await api('/api/family/invite', { method: 'POST', body: '{}' });
        const box = $('familyLink');
        box.hidden = false;
        box.innerHTML = `<p class="muted small">Перешлите ссылку второму родителю. Она одноразовая и действует сутки.</p>
          <input class="field" readonly value="${esc(invite.url)}">
          <div class="family-actions"><button class="btn" id="familyShare">Поделиться</button>
          <button class="btn outline" id="familyCopy">Скопировать</button></div>`;
        $('familyShare').addEventListener('click', () => {
          const text = 'Ссылка, чтобы получать уведомления из школьного дневника';
          if (tg?.openTelegramLink) tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(invite.url)}&text=${encodeURIComponent(text)}`);
          else if (navigator.share) navigator.share({ url: invite.url, text }).catch(() => {});
          else window.open(invite.url, '_blank');
        });
        $('familyCopy').addEventListener('click', async () => {
          try { await navigator.clipboard.writeText(invite.url); notify('success'); banner('Скопировано', false, 2500); }
          catch { prompt('Скопируйте ссылку', invite.url); }
        });
      } catch (error) {
        notify('error');
        banner(error.message, true);
      } finally {
        event.currentTarget.disabled = false;
      }
    });
    screen.querySelectorAll('[data-drop], [data-leave]').forEach((button) => button.addEventListener('click', async () => {
      tap();
      const leave = 'leave' in button.dataset;
      try {
        const { data: fresh } = await api('/api/family/remove', {
          method: 'POST', body: JSON.stringify({ id: Number(button.dataset.drop || button.dataset.leave), leave }),
        });
        notify('success');
        draw(fresh);
      } catch (error) {
        notify('error');
        banner(error.message, true);
      }
    }));
  };
  try {
    draw((await api('/api/family')).data);
  } catch (error) {
    screen.innerHTML = errorCard(error);
  }
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

const HISTORY_KINDS = { new: 'Новая', changed: 'Исправлена', deleted: 'Удалена' };
const HISTORY_FILTERS = [['all', 'Все'], ['new', 'Новые'], ['changed', 'Исправленные'], ['deleted', 'Удалённые']];
const dayFormat = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' });
const clockFormat = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });

/** «Сегодня», «Вчера» или «10 октября» — заголовок группы по дню, когда
 *  изменение заметили. */
function historyDay(date) {
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((today - day) / 86400000);
  return diff === 0 ? 'Сегодня' : diff === 1 ? 'Вчера' : dayFormat.format(date);
}

function historyItem(item, index) {
  const seen = new Date(item.seen);
  const date = item.date.split('-').reverse().slice(0, 2).join('.');
  const marks = item.kind === 'changed'
    ? `${markBox(item.old, 'old')}<span class="hi-arrow">${ICONS.right}</span>${markBox(item.new, 'fresh')}`
    : markBox(item.mark, item.kind === 'deleted' ? 'old' : 'fresh');
  return `<div class="history-item hi-${esc(item.kind)}" style="--d:${Math.min(index, 12) * 35}ms">
      <div class="hi-main">
        <div class="hi-subject">${esc(item.subject)}</div>
        <div class="hi-title">${esc(item.title || 'Оценка')} · за ${esc(date)}</div>
        <div class="hi-meta"><span class="hi-kind">${HISTORY_KINDS[item.kind] || ''}</span>${esc(clockFormat.format(seen))}</div>
      </div>
      <div class="hi-marks">${marks}</div>
    </div>`;
}

async function openHistory() {
  closeDrawer();
  const screen = pushScreen('История оценок');
  screen.innerHTML = `<div class="history-skeleton">${'<div class="card"><div class="skeleton" style="width:50%"></div><div class="skeleton" style="width:75%"></div></div>'.repeat(5)}</div>`;
  const items = [];
  let filter = 'all';
  let more = true;
  let loading = false;

  const draw = () => {
    const shown = items.filter((item) => filter === 'all' || item.kind === filter);
    const count = (kind) => items.filter((item) => kind === 'all' || item.kind === kind).length;
    let lastDay = '';
    let index = 0;
    const list = shown.map((item) => {
      const day = historyDay(new Date(item.seen));
      const head = day !== lastDay ? `<div class="hi-day">${esc(day)}</div>` : '';
      lastDay = day;
      return head + historyItem(item, index++);
    }).join('');
    screen.innerHTML = `
      <div class="chips history-filters">${HISTORY_FILTERS.map(([key, label]) =>
        `<button class="chip ${key === filter ? 'active' : ''}" data-filter="${key}">${label}<span class="hi-count">${count(key)}</span></button>`).join('')}</div>
      <div class="history-list">${list || '<div class="empty muted">Таких изменений пока нет.</div>'}</div>
      ${more ? '<button class="btn outline hi-more" id="historyMore">Показать раньше</button>' : ''}`;
    screen.querySelectorAll('[data-filter]').forEach((chip) => chip.addEventListener('click', () => {
      if (chip.dataset.filter === filter) return;
      select();
      filter = chip.dataset.filter;
      draw();
    }));
    screen.querySelector('#historyMore')?.addEventListener('click', load);
  };

  async function load() {
    if (loading) return;
    loading = true;
    const button = screen.querySelector('#historyMore');
    if (button) { button.disabled = true; button.textContent = 'Загружаем…'; }
    try {
      const before = items.length ? `?before=${items[items.length - 1].id}` : '';
      const { data } = await api(`/api/history${before}`);
      items.push(...data.items);
      // Сервер отдаёт по 50: меньше — значит, старше ничего нет.
      more = data.items.length >= 50;
      if (!items.length) {
        screen.innerHTML = `<div class="empty history-empty">${ICONS.clock}<div>Пока пусто: изменения появятся после следующих проверок.</div></div>`;
        return;
      }
      draw();
    } catch (error) {
      if (!items.length) screen.innerHTML = errorCard(error);
      else banner(error.message, true);
    } finally {
      loading = false;
    }
  }
  await load();
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
  if (where === 'install') {
    return `<div class="muted small">Пуш на iPhone работает в приложении с экрана «Домой».</div>
        <button class="btn outline" id="installGuide">Как установить</button>`;
  }
  const install = canInstall()
    ? '<button class="btn outline" id="installGuide">Установить на экран «Домой»</button>' : '';
  if (where === 'ready') {
    return `<label class="switch-row"><span>Пуш на этом устройстве</span>
        <input type="checkbox" switch class="switch" id="pushToggle" disabled></label>${install}`;
  }
  return install;
}

function pushHowto() {
  // Внутри Telegram web push недоступен: уведомления браузера работают только
  // у сайта, открытого в самом браузере или установленного на экран «Домой».
  if (pushAvailability() !== 'telegram' || !wantsInstall()) return '';
  return `<section class="drawer-section" data-install-hint>
      <div class="drawer-heading">Приложение на экране «Домой»</div>
      <ol class="howto">
        <li>Нажмите «Открыть в браузере» — откроется ваша личная ссылка.</li>
        <li><b>iPhone:</b> в Safari «Поделиться» → «На экран Домой». <b>Android:</b> в Chrome ⋮ → «Установить приложение».</li>
        <li>Запустите дневник с иконки — он сам спросит про уведомления.</li>
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
  const accent = currentAccent();
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
      <div class="drawer-name">${esc(data.name)}</div>
      <div class="muted small" id="studentClass"${studentClass ? '' : ' hidden'}>${esc(studentClass)}</div>
      <div class="muted small">${esc(data.school)}</div>
      ${data.students.length > 1 ? `<div class="drawer-children">${data.students
        .map((s) => `<button class="chip ${s.current ? 'active' : ''}" data-student="${s.id}">${esc(s.name)}</button>`)
        .join('')}</div>` : ''}
    </section>
    <section class="drawer-section">
      <div class="drawer-heading">Дополнительно</div>
      ${data.assistant && !demo && !navTabs().includes('assistant') ? `<button class="drawer-link" id="drawerAssistant">${ICONS.sparkles} ИИ-помощник</button>` : ''}
      ${demo ? '' : `<button class="drawer-link" id="drawerLock">${ICONS.shield} Защита входа</button>`}
      ${demo ? '' : `<button class="drawer-link" id="drawerAccount">${ICONS.mail} Вход по почте</button>`}
      ${demo ? '' : `<button class="drawer-link" id="drawerBug">${ICONS.chat} Сообщить об ошибке</button>`}
      <button class="drawer-link" id="drawerCalc">${ICONS.calc} Калькулятор баллов</button>
      <button class="drawer-link" id="drawerHistory">${ICONS.clock} История оценок</button>
      ${demo ? '' : `<button class="drawer-link" id="drawerExport">${ICONS.table} Оценки в Excel</button>`}
      ${demo ? '' : `<button class="drawer-link" id="drawerFamily">${ICONS.users} Второй родитель</button>`}
      ${demo ? '' : `<button class="drawer-link" id="drawerCalendar">${ICONS.calendar} Расписание в календаре</button>`}
    </section>
    <section class="drawer-section">
      <div class="drawer-heading">Оформление</div>
      <label class="switch-row design-row"><span>Старый дизайн</span>
        <input type="checkbox" switch class="switch" id="designOld" ${isNewDesign() ? '' : 'checked'}></label>
      <label class="switch-row"><span>Подсвечивать Д/З на главной<span class="muted small switch-note">красным — не сделано, зелёным — сделано</span></span>
        <input type="checkbox" switch class="switch" id="hwStatusShow" ${showHomeworkStatus() ? 'checked' : ''}></label>
      <label class="switch-row"><span>Показывать выходные без уроков</span>
        <input type="checkbox" switch class="switch" id="weekendsShow" ${showWeekends() ? 'checked' : ''}></label>
      ${isNewDesign() ? '' : `<label class="switch-row"><span>Звонки вместо расписания недели</span>
        <input type="checkbox" switch class="switch" id="bellsTab" ${showBells() ? 'checked' : ''}></label>`}
      ${isNewDesign() ? themeSegment : `${radio('light', 'Светлая тема')}
      ${radio('auto', 'Как в системе')}
      ${radio('dark', 'Тёмная тема')}`}
      <div class="drawer-sub">${ICONS.palette} ${isNewDesign() ? 'Цвет акцента' : 'Цвет меню'}</div>
      <div class="swatches">
        ${ACCENTS.map(([color, name]) => `<button class="swatch ${color === accent ? 'on' : ''}" data-accent="${color}" style="background:${color}" aria-label="${name}" title="${name}"></button>`).join('')}
        <label class="swatch custom ${ACCENTS.some(([c]) => c === accent) ? '' : 'on'}" title="Свой цвет" style="background:${accent}">
          <input type="color" id="accentCustom" value="${accent}">＋</label>
      </div>
    </section>
    ${isNewDesign() ? tabsSection() : ''}
    <section class="drawer-section">
      <div class="drawer-heading">Уведомления</div>
      ${toggle('telegram', 'В Telegram', data.notifications.telegram !== false)}
      ${pushRow()}
      <div class="muted small" id="notifyNowhere" hidden>Сейчас уведомления никуда не приходят.</div>
      <div class="drawer-sub">О чём сообщать</div>
      ${Object.entries(NOTIFY_LABELS)
        .filter(([key]) => !MOBILE_ONLY.has(key) || data.api_mode === 'mobile')
        .filter(([key]) => key !== 'all_children' || data.students.length > 1)
        .map(([key, label]) => toggle(key, label, data.notifications[key]))
        .join('')}
      <label class="switch-row"><span>Время напоминания</span>
        <select class="field reminder-hour" id="reminderHour">${[15, 16, 17, 18, 19, 20, 21].map((hour) =>
          `<option value="${hour}" ${hour === (data.notifications.reminder_hour ?? 18) ? 'selected' : ''}>${hour}:00</option>`).join('')}</select></label>
      <div class="drawer-sub">Какие новые оценки присылать</div>
      <div class="chips marks-filter" id="marksFilter">${[5, 4, 3, 2].map((value) => {
        const chosen = data.notifications.marks_filter || [];
        const on = !chosen.length || chosen.includes(value);
        return `<button class="chip ${on ? 'active' : ''}" data-mark-value="${value}" aria-pressed="${on}">${value}</button>`;
      }).join('')}</div>
      <div class="muted small">Изменения и удаления оценок приходят всегда.</div>
      <div class="muted small">Проверка каждые ${data.interval_minutes} мин</div>
      ${statusLine(data.status)}
    </section>
    ${demoSection()}
    <div class="drawer-foot muted small">Дневник «Сетевого города»</div>`;

  bindDrawer();
  loadStudentClass(currentStudent);
}

function bindDrawer() {
  const body = settingsRoot();
  body.querySelectorAll('input[name="theme"]').forEach((input) =>
    input.addEventListener('change', () => {
      select();
      setPref('theme', input.value);
      applyStoredTheme();
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
    applyStoredTheme();
  });
  $('hwStatusShow').addEventListener('change', (event) => {
    select();
    saveHomeworkStatus(event.target.checked);
  });
  $('weekendsShow').addEventListener('change', (event) => {
    select();
    saveWeekends(event.target.checked);
  });
  $('bellsTab')?.addEventListener('change', (event) => {
    select();
    saveBells(event.target.checked);
  });
  $('designOld').addEventListener('change', (event) => {
    select();
    saveDesign(!event.target.checked);
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
  $('reminderHour')?.addEventListener('change', async (event) => {
    select();
    try {
      const { data } = await api('/api/settings/reminder', { method: 'POST', body: JSON.stringify({ hour: Number(event.target.value) }) });
      state.profile.notifications = data;
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
      const { data } = await api('/api/settings/marks-filter', { method: 'POST', body: JSON.stringify({ values }) });
      state.profile.notifications = data;
    } catch (error) {
      chip.classList.toggle('active');
      notify('error');
      banner(error.message, true);
    }
  });
  $('drawerExport')?.addEventListener('click', () => { closeDrawer(); openExport(); });
  $('drawerFamily')?.addEventListener('click', () => { closeDrawer(); openFamily(); });
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
  $('drawerAccount')?.addEventListener('click', async () => {
    tap();
    closeDrawer();
    const { openAccountSettings } = await import('./account.js');
    openAccountSettings();
  });
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

/**
 * Первый запуск с экрана «Домой»: сразу спросить разрешение на уведомления
 * и подписать устройство на пуш. iOS показывает запрос только в ответ на
 * нажатие — если без него не вышло, спросим при первом касании экрана.
 */
export function offerPushOnFirstLaunch() {
  const installed = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  if (demo || !installed || pushAvailability() !== 'ready') return;
  // Пуш включён по умолчанию: разрешение уже есть, а подписки нет (например,
  // приложение переустановили) — подписываемся, если сами не выключали.
  if (Notification.permission === 'granted') {
    if (!pushTurnedOff()) currentSubscription().then((found) => found || subscribePush()).catch(() => {});
    return;
  }
  if (Notification.permission !== 'default') return;
  try {
    if (localStorage.getItem('pushAsked')) return;
  } catch { /* без хранилища просто спросим */ }
  const ask = async () => {
    try {
      if ((await Notification.requestPermission()) === 'default') return false;
      try { localStorage.setItem('pushAsked', '1'); } catch { /* ничего */ }
      if (Notification.permission === 'granted') await subscribePush();
    } catch { /* откажет — включат в настройках */ }
    return true;
  };
  ask().then((answered) => {
    if (answered) return;
    const onTap = () => {
      document.removeEventListener('click', onTap, true);
      ask();
    };
    document.addEventListener('click', onTap, true);
  });
}

const PUSH_OFF_KEY = 'pushOff';

function pushTurnedOff() {
  try { return localStorage.getItem(PUSH_OFF_KEY) === '1'; } catch { return false; }
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
      try {
        if (input.checked) localStorage.removeItem(PUSH_OFF_KEY);
        else localStorage.setItem(PUSH_OFF_KEY, '1');
      } catch { /* ничего */ }
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
