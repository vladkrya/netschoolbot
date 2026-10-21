// Настройки → «Админ-настройки»: всё, что может только владелец (TG_ADMIN_ID).
// Сервер — app/web/admin.py; тумблеры сохраняются своими запросами
// (solver.py, chat.py, account.py).
'use strict';

import { api, banner, esc } from './core.js';
import { notify, select } from './haptics.js';
import { pushScreen } from './screens.js';

const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

// Куда пишет каждый тумблер.
const SWITCHES = {
  solver_class: '/api/settings/solver-class',
  chats: '/api/settings/chats',
  share_sgo: '/api/account/share-sgo',
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

export function openAdmin() {
  const body = pushScreen('Админ-настройки', 'видно только вам');
  body.innerHTML = '<div class="muted small">Загрузка…</div>';

  const draw = (data) => {
    body.innerHTML = `<div class="card">
        ${data.solver_class ? toggle('solver_class', 'Решения заданий всему классу',
          'кнопка «Решение» появится у одноклассников, которые пользуются ботом', data.solver_class.on) : ''}
        ${toggle('chats', 'Чаты в «Сообщениях»',
          'выключенные чаты скрыты у всех, кроме вас; переписка сохраняется', data.chats.on)}
        ${toggle('share_sgo', 'Разрешить другим аккаунтам использовать мой Сетевой город',
          'обычно один аккаунт «Сетевого города» подключается только к одному аккаунту приложения; кто уже подключился, останется и после выключения', data.share_sgo.on)}
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

  api('/api/admin')
    .then(({ data }) => draw(data))
    .catch((error) => { body.innerHTML = `<div class="muted small">${esc(error.message)}</div>`; });
}
