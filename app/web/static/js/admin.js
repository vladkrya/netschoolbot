// Настройки → «Админ-настройки»: всё, что может только владелец (TG_ADMIN_ID).
// Сервер — app/web/admin.py; тумблеры сохраняются своими запросами
// (solver.py, account.py). Вкладка «Иконки» — app/web/app_icon.py.
'use strict';

import { api, banner, esc, openExternal, showFailure } from './core.js';
import { notify, select } from './haptics.js';
import { onScreenRefresh, pushScreen } from './screens.js';
import { closeSheet, openSheet } from './sheet.js';

const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

// Куда пишет каждый тумблер.
const SWITCHES = {
  solver_class: '/api/settings/solver-class',
  share_sgo: '/api/account/share-sgo',
  android_updates: '/api/admin/android-updates',
};

const WIDGET = [['main', 'Основной бот'], ['reserve', 'Резервный'], ['off', 'Выключен']];

function toggle(key, label, note, on) {
  return `<label class="switch-row"><span>${label}<span class="muted small switch-note">${note}</span></span>
      <input type="checkbox" switch class="switch" data-admin-switch="${key}" ${on ? 'checked' : ''}></label>`;
}

function widgetCard(widget) {
  const chips = WIDGET.filter(([mode]) => mode !== 'reserve' || widget.reserve || widget.mode === 'reserve')
    .map(([mode, label]) => `<button class="chip ${mode === widget.mode ? 'active' : ''}" data-widget="${mode}" aria-pressed="${mode === widget.mode}">${label}</button>`)
    .join('');
  return `<div class="card">
      <div class="card-title">Вход на сайт через Telegram</div>
      <p class="muted small">Кнопка входа и привязки Telegram на сайте. Домен ${widget.domain ? `<b>${esc(widget.domain)}</b>` : 'не задан (NETSCHOOL_TG_LOGIN_DOMAIN) — кнопки нет'}
        должен быть привязан в @BotFather (/setdomain) именно к выбранному боту. В боте то же — /tgwidget.</p>
      <div class="chips">${chips}</div>
    </div>`;
}

function tabs(active, pending) {
  return `<div class="chips admin-tabs">
      <button class="chip ${active === 'main' ? 'active' : ''}" data-admin-tab="main" aria-pressed="${active === 'main'}">Настройки</button>
      <button class="chip ${active === 'icons' ? 'active' : ''}" data-admin-tab="icons" aria-pressed="${active === 'icons'}">Иконки${pending ? ` · ${pending}` : ''}</button>
    </div>`;
}

const when = (seconds) => new Date(seconds * 1000).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

function iconRow(item) {
  const pending = item.status === 'pending';
  return `<div class="admin-icon">
      <img src="${item.image}" alt="" width="64" height="64">
      <div class="admin-icon-who"><b>${esc(item.user)}</b><span class="muted small">${esc(item.school)}${item.school ? ' · ' : ''}${when(item.created_at)}</span></div>
      <div class="admin-icon-actions">
        ${pending ? `<button class="btn" data-icon-id="${item.id}" data-decision="approve">Принять</button>` : ''}
        <button class="btn outline" data-icon-id="${item.id}" data-decision="reject">Отозвать</button>
      </div>
    </div>`;
}

function iconsTab(data) {
  const pending = data.items.filter((item) => item.status === 'pending');
  const approved = data.items.filter((item) => item.status === 'approved');
  return `<div class="card">
      <label class="switch-row"><span>Пуши о новых иконках<span class="muted small switch-note">приходят, когда кто-то поставил свою иконку</span></span>
        <input type="checkbox" switch class="switch" data-icon-push ${data.push ? 'checked' : ''}></label>
    </div>
    <div class="card">
      <div class="card-title">Не проверены <span class="muted small">— уже работают</span></div>
      ${pending.map(iconRow).join('') || '<div class="muted small">Новых иконок нет.</div>'}
    </div>
    ${approved.length ? `<div class="card">
      <div class="card-title">Проверены</div>
      ${approved.map(iconRow).join('')}
    </div>` : ''}
    <p class="muted small device-note">Иконки работают сразу после загрузки. Отзыв гасит адрес картинки: вместо неё отдаётся стандартная иконка.
      Android вернёт её на экран сам, на iPhone уже добавленная иконка остаётся до переустановки.</p>`;
}

export function openAdmin() {
  const body = pushScreen('Админ-настройки', 'видно только вам');
  body.innerHTML = '<div class="muted small">Загрузка…</div>';
  let tab = 'main';
  let pendingCount = 0;

  const drawIcons = (data) => {
    pendingCount = data.items.filter((item) => item.status === 'pending').length;
    body.innerHTML = tabs('icons', pendingCount) + iconsTab(data);
    bindTabs();
    body.querySelector('[data-icon-push]').addEventListener('change', async (event) => {
      const input = event.currentTarget;
      select();
      input.disabled = true;
      try {
        await post('/api/admin/icons/push', { on: input.checked });
        notify('success');
      } catch (error) {
        notify('error');
        input.checked = !input.checked;
        banner(error.message, true);
      } finally {
        input.disabled = false;
      }
    });
    const decide = async (button, payload) => {
      button.classList.add('loading');
      try {
        const { data: fresh } = await post(`/api/admin/icons/${button.dataset.iconId}`, payload);
        notify('success');
        closeSheet();
        drawIcons(fresh);
      } catch (error) {
        notify('error');
        banner(error.message, true);
        button.classList.remove('loading');
      }
    };
    body.querySelectorAll('[data-decision="approve"]').forEach((button) => button.addEventListener('click', () => {
      select();
      decide(button, { decision: 'approve' });
    }));
    // Отзыв — с причиной: она уйдёт автору в пуше и появится на его экране иконки.
    body.querySelectorAll('[data-decision="reject"]').forEach((button) => button.addEventListener('click', () => {
      select();
      const item = data.items.find((icon) => String(icon.id) === button.dataset.iconId);
      const sheet = openSheet('Отозвать иконку', `<div class="admin-icon">
          <img src="${item.image}" alt="" width="64" height="64">
          <div class="admin-icon-who"><b>${esc(item.user)}</b><span class="muted small">вернётся стандартная иконка, автору придёт уведомление</span></div>
        </div>
        <textarea class="field" data-reject-comment rows="3" maxlength="300" placeholder="Причина — необязательно"></textarea>
        <div class="app-icon-actions admin-reject-actions">
          <button class="btn" data-reject-confirm data-icon-id="${item.id}">Отозвать</button>
          <button class="btn outline" data-reject-cancel>Отмена</button>
        </div>`);
      sheet.querySelector('[data-reject-cancel]').addEventListener('click', closeSheet);
      sheet.querySelector('[data-reject-confirm]').addEventListener('click', (event) => decide(event.currentTarget,
        { decision: 'reject', comment: sheet.querySelector('[data-reject-comment]').value.trim() }));
    }));
  };

  const bindTabs = () => body.querySelectorAll('[data-admin-tab]').forEach((chip) => chip.addEventListener('click', () => {
    if (chip.dataset.adminTab === tab) return;
    select();
    tab = chip.dataset.adminTab;
    load();
  }));

  const draw = (data) => {
    const versions = data.android_updates?.versions || [];
    const current = Number(data.android_updates?.versionCode || 0);
    const nameOf = (code) => versions.find((v) => Number(v.versionCode) === Number(code))?.versionName || 'не определена';
    pendingCount = data.icons_pending || 0;
    body.innerHTML = `${tabs('main', pendingCount)}<div class="card">
        ${data.solver_class ? toggle('solver_class', 'Решения заданий всему классу',
          'кнопка «Решение» появится у одноклассников, которые пользуются ботом', data.solver_class.on) : ''}
        ${toggle('share_sgo', 'Разрешить другим аккаунтам использовать мой Сетевой город',
          'обычно один аккаунт «Сетевого города» подключается только к одному аккаунту приложения; кто уже подключился, останется и после выключения', data.share_sgo.on)}
      </div>
      <div class="card">
        <div class="card-title">Android-приложение</div>
        ${toggle('android_updates', 'Разрешить обновления APK', 'новые версии доступны всем, кто установил приложение; выключение скрывает новые версии и запрещает их скачивание', data.android_updates?.on !== false)}
        ${data.android_updates?.available ? `<p class="muted small">Актуальная сборка: ${esc(data.android_updates.version)} · ${(data.android_updates.size / 1024 / 1024).toFixed(1)} МБ</p>
          <button class="btn outline" data-android-download>Скачать актуальный APK</button>` : '<p class="muted small">Сборка ещё не опубликована.</p>'}
        <p class="muted small">Скачать сборку вручную можно и при выключенных обновлениях.</p>
        <label>Версия, которую нужно отключить
          <select class="field" data-android-version>${versions.map((v) => `<option value="${Number(v.versionCode)}">${esc(v.versionName)}${Number(v.versionCode) === current ? ' · актуальная' : ''}</option>`).join('')}</select></label>
        <button class="btn outline" data-android-block>Потребовать обновление этой версии</button>
        <button class="btn outline" data-android-block-below>Потребовать обновление этой версии и ниже</button>
        <p class="muted small">Нужна опубликованная более новая сборка. Работает в APK с поддержкой серверной блокировки.</p>
        ${(data.android_updates?.blockedVersionCodes || []).map((code) => `<div>Версия ${esc(nameOf(code))} отключена <button class="btn outline" data-android-allow="${Number(code)}">Разрешить снова</button></div>`).join('')}

      </div>
      ${widgetCard(data.widget)}
      <div class="card">
        <div class="card-title">Статистика</div>
        <div class="admin-stats" data-stats><button class="btn outline" data-stats-load>Показать</button></div>
      </div>
      <p class="muted small device-note">Группы и каналы для сообщений о «Сетевом городе» подключаются в боте: /groups и /addgroup.</p>`;
    bind();
  };

  const bind = () => {
    bindTabs();
    body.querySelectorAll('[data-admin-switch]').forEach((input) => input.addEventListener('change', async () => {
      select();
      input.disabled = true;
      try {
        await post(SWITCHES[input.dataset.adminSwitch], { on: input.checked });
        notify('success');
      } catch (error) {
        notify('error');
        input.checked = !input.checked;
        banner(error.message, true);
      } finally {
        input.disabled = false;
      }
    }));
    body.querySelectorAll('[data-widget]').forEach((button) => button.addEventListener('click', async () => {
      if (button.classList.contains('active')) return;
      select();
      try {
        const { data } = await post('/api/admin/widget', { mode: button.dataset.widget });
        notify('success');
        draw(data);
      } catch (error) {
        notify('error');
        banner(error.message, true);
      }
    }));
    const versionAction = async (payload, label, button) => {
      if (payload.blocked && !window.confirm(`Отключить ${label}? Они будут требовать обновление.`)) return;
      button.disabled = true;
      try {
        const { data } = await post('/api/admin/android-version', payload);
        draw(data);
      } catch (error) { banner(error.message, true); }
      finally { button.disabled = false; }
    };
    const picked = () => {
      const select = body.querySelector('[data-android-version]');
      return { code: Number(select?.value), name: select?.selectedOptions[0]?.textContent.replace(' · актуальная', '') || '' };
    };
    body.querySelector('[data-android-block]')?.addEventListener('click', (event) => {
      const { code, name } = picked();
      if (Number.isInteger(code) && code > 0) versionAction({ versionCode: code, blocked: true }, `версию ${name}`, event.currentTarget);
    });
    body.querySelector('[data-android-block-below]')?.addEventListener('click', (event) => {
      const { code, name } = picked();
      if (Number.isInteger(code) && code > 0) versionAction({ upTo: code, blocked: true }, `версию ${name} и все старше неё`, event.currentTarget);
    });
    body.querySelectorAll('[data-android-allow]').forEach((button) => button.addEventListener('click', () => versionAction({ versionCode: Number(button.dataset.androidAllow), blocked: false }, 'версию', button)));
    body.querySelector('[data-android-download]')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try { const { data } = await post('/api/admin/android-link'); openExternal(data.url); }
      catch (error) { banner(error.message, true); }
      finally { button.disabled = false; }
    });
    body.querySelector('[data-stats-load]').addEventListener('click', async (event) => {
      const box = body.querySelector('[data-stats]');
      event.currentTarget.disabled = true;
      try {
        const { data } = await api('/api/admin/stats');
        // HTML Telegram, собранный сервером из чисел (bot/handlers/admin.py).
        box.innerHTML = data.html;
      } catch (error) {
        box.innerHTML = `<div class="muted small">${esc(error.message)}</div>`;
      }
    });
  };

  const load = () => (tab === 'icons' ? api('/api/admin/icons').then(({ data }) => drawIcons(data))
    : api('/api/admin').then(({ data }) => draw(data)))
    .catch((error) => showFailure(body, error, `<div class="muted small">${esc(error.message)}</div>`));
  onScreenRefresh(body, load);
  load();
}
