// Настройки → «Устройства»: где открыт дневник и завершение сеансов.
// Сервер — app/web/devices.py. Telegram — основной сеанс, его не завершить.
'use strict';

import { api, banner, esc, plural } from './core.js';
import { notify } from './haptics.js';
import { ICONS } from './icons.js';
import { pushScreen } from './screens.js';

const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

function when(seconds) {
  if (!seconds) return '';
  const date = new Date(seconds * 1000);
  const today = new Date();
  const time = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  if (date.toDateString() === today.toDateString()) return `сегодня в ${time}`;
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return `вчера в ${time}`;
  return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: date.getFullYear() === today.getFullYear() ? undefined : 'numeric' });
}

const ICON = { telegram: 'send', device: 'install', links: 'share', calendar: 'calendar' };

function describe(item) {
  if (item.kind === 'telegram') return 'Основной сеанс — мини-приложение в боте. Завершить его нельзя.';
  if (item.kind === 'links') {
    return `Старые ссылки и иконки на экране «Домой», где ключ доступа прямо в адресе${item.count > 1 ? ` (${item.count} ${plural(item.count, 'ключ', 'ключа', 'ключей')})` : ''}. Отличить их друг от друга нельзя — завершаются разом.`;
  }
  if (item.kind === 'calendar') return 'Календарь телефона забирает расписание. Ничего, кроме расписания, по этой ссылке не открыть.';
  return item.created ? `Вход ${when(item.created)}` : '';
}

function row(item) {
  const seen = item.last_used ? `<span class="device-seen">Активность: ${when(item.last_used)}</span>` : '';
  const badge = item.current ? '<span class="device-badge">Это устройство</span>' : '';
  const action = item.main ? '' : `<button class="btn outline danger device-end" data-end="${esc(item.id)}">${item.current ? 'Выйти' : 'Завершить'}</button>`;
  return `<div class="card device-row">
    <div class="device-head">
      <span class="device-icon">${ICONS[ICON[item.kind]] || ICONS.install}</span>
      <div class="device-title"><b>${esc(item.label)}</b>${badge}</div>
    </div>
    <p class="muted small">${esc(describe(item))}</p>
    ${seen}${action}
  </div>`;
}

function confirmText(item) {
  if (item.current) return 'Это устройство — вы выйдете из аккаунта здесь. Продолжить?';
  if (item.kind === 'links') return 'Завершить ссылки из бота? Старые иконки на экране «Домой» и ссылки с ключом в адресе перестанут открывать дневник. Если ваш календарь подписан по старой ссылке — переподпишитесь в меню.';
  if (item.kind === 'calendar') return 'Отключить подписку на календарь? Расписание в календаре перестанет обновляться.';
  return `Завершить сеанс «${item.label}»? На том устройстве дневник перестанет открываться — войти снова можно через бота или по почте.`;
}

export function openDevices() {
  const body = pushScreen('Устройства');
  const draw = (items) => {
    const others = items.some((item) => !item.main && !item.current && item.kind !== 'calendar');
    body.innerHTML = `${items.map(row).join('')}
      ${others ? '<button class="btn outline danger device-end-all" data-end-all>Завершить все другие сеансы</button>' : ''}
      <p class="muted small device-note">Если ссылка на дневник могла попасть к чужим — завершите лишние сеансы или отправьте боту /app_reset: он отзовёт все ссылки сразу.</p>`;
    body.querySelectorAll('[data-end]').forEach((button) => button.addEventListener('click', async () => {
      const item = items.find((entry) => entry.id === button.dataset.end);
      if (!item || !window.confirm(confirmText(item))) return;
      if (item.current) {
        const { signOut } = await import('./profile.js');
        signOut({ confirmed: true });
        return;
      }
      button.disabled = true;
      try {
        draw((await post('/api/devices/revoke', { id: item.id })).data.items);
        notify('success');
      } catch (error) {
        button.disabled = false;
        banner(error.message, true);
      }
    }));
    body.querySelector('[data-end-all]')?.addEventListener('click', async (event) => {
      if (!window.confirm('Завершить все сеансы, кроме этого? Telegram и подписка на календарь останутся.')) return;
      event.currentTarget.disabled = true;
      try {
        draw((await post('/api/devices/revoke-others')).data.items);
        notify('success');
      } catch (error) {
        banner(error.message, true);
      }
    });
  };
  body.innerHTML = '<div class="card"><div class="muted">Загрузка…</div></div>';
  api('/api/devices').then(({ data }) => draw(data.items))
    .catch((error) => { body.innerHTML = `<div class="card error-card"><p>${esc(error.message)}</p></div>`; });
}
