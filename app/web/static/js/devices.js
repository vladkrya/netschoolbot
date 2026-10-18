// Настройки → «Устройства»: где открыт дневник и завершение сеансов.
// Сервер — app/web/devices.py. Telegram — основной сеанс, его не завершить.
'use strict';

import { api, banner, esc, initData, plural, tg } from './core.js';
import { notify } from './haptics.js';
import { ICONS } from './icons.js';
import { pushScreen } from './screens.js';

const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

/** Точные дата и время: «18 октября 2026 в 01:45». */
function when(seconds) {
  if (!seconds) return '';
  const date = new Date(seconds * 1000);
  const day = date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).replace(/\s*г\.$/, '');
  const time = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  return `${day} в ${time}`;
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
  const clients = item.clients ? `<button class="device-more" data-clients>
      <span>${item.clients.length ? `${item.clients.length} ${plural(item.clients.length, 'устройство', 'устройства', 'устройств')}` : 'Какие устройства'}</span>${ICONS.right}</button>` : '';
  const action = item.main ? '' : `<button class="btn outline danger device-end" data-end="${esc(item.id)}">${item.current ? 'Выйти' : 'Завершить'}</button>`;
  return `<div class="card device-row">
    <div class="device-head">
      <span class="device-icon">${ICONS[ICON[item.kind]] || ICONS.install}</span>
      <div class="device-title"><b>${esc(item.label)}</b>${badge}</div>
    </div>
    <p class="muted small">${esc(describe(item))}</p>
    ${seen}${clients}${action}
  </div>`;
}

function confirmText(item) {
  if (item.current) return 'Это устройство — вы выйдете из аккаунта здесь. Продолжить?';
  if (item.kind === 'links') return 'Завершить ссылки из бота? Старые иконки на экране «Домой» и ссылки с ключом в адресе перестанут открывать дневник. Если ваш календарь подписан по старой ссылке — переподпишитесь в меню.';
  if (item.kind === 'calendar') return 'Отключить подписку на календарь? Расписание в календаре перестанет обновляться.';
  return `Завершить сеанс «${item.label}»? На том устройстве дневник перестанет открываться — войти снова можно через бота или по почте.`;
}

/** Подвкладка «Ссылки из бота»: какие устройства ходят по общему ключу. */
function clientIcon(client) {
  const text = `${client.label} ${client.details}`;
  if (/iPhone|iPad|Android/.test(text)) return ICONS.install;
  return ICONS.table;
}

function openLinkClients(item, onEndAll) {
  const body = pushScreen('Ссылки из бота');
  const cards = item.clients.map((client) => `<div class="card device-row">
      <div class="device-head">
        <span class="device-icon">${clientIcon(client)}</span>
        <div class="device-title"><b>${esc(client.label)}</b></div>
      </div>
      ${client.details ? `<p class="device-detail">${esc(client.details)}</p>` : ''}
      <p class="muted small">Впервые: ${when(client.first_seen)}${client.ip ? ` · адрес ${esc(client.ip)}` : ''}</p>
      <span class="device-seen">Активность: ${when(client.last_seen)}</span>
    </div>`).join('');
  body.innerHTML = `
    <p class="muted small device-note">Эти устройства открывают дневник по одной и той же ссылке с ключом в адресе — старые иконки на экране «Домой» и ссылки из бота. Завершить их можно только все вместе.</p>
    ${cards || '<div class="card"><p class="muted">Пока никого не видно: устройства записываются, когда открывают дневник по такой ссылке.</p></div>'}
    <button class="btn outline danger device-end-all" data-end-links>Завершить все ссылки из бота</button>`;
  body.querySelector('[data-end-links]').addEventListener('click', () => onEndAll(body));
}

/** «Добавить устройство»: ссылка на сутки (как /app_install в боте) и QR. */
async function openNewDevice() {
  const body = pushScreen('Новое устройство');
  body.innerHTML = '<div class="card"><div class="muted">Готовим ссылку…</div></div>';
  let data;
  try {
    ({ data } = await post('/api/devices/new-link'));
  } catch (error) {
    body.innerHTML = `<div class="card error-card"><p>${esc(error.message)}</p></div>`;
    return;
  }
  body.innerHTML = `
    <div class="card device-new">
      <p>Откройте ссылку на новом устройстве — в Safari, Chrome или другом браузере, — и дневник откроется без входа.
        Потом его можно добавить на экран «Домой».</p>
      ${data.qr ? `<img class="device-qr" src="${data.qr}" alt="QR-код ссылки" width="220" height="220">
        <p class="muted small device-qr-hint">Наведите камеру телефона на код.</p>` : ''}
      <div class="device-link"><code>${esc(data.url)}</code></div>
      <div class="device-new-actions">
        <button class="btn" data-copy>${ICONS.doc} Скопировать</button>
        ${navigator.share ? `<button class="btn outline" data-share>${ICONS.share} Поделиться</button>` : ''}
        ${initData && tg?.openLink ? `<button class="btn outline device-open" data-open>${ICONS.external} Открыть в браузере</button>` : ''}
      </div>
      <p class="muted small">Ссылка действует сутки и открывает ваш дневник — не пересылайте её чужим. Каждое открытие появится в «Устройствах» отдельной строкой, и его можно будет завершить.</p>
    </div>`;
  body.querySelector('[data-copy]').addEventListener('click', () => {
    navigator.clipboard?.writeText(data.url).then(
      () => { notify('success'); banner('Ссылка скопирована', false, 1500); },
      () => { notify('error'); banner('Не получилось скопировать — выделите ссылку вручную', true); },
    );
  });
  body.querySelector('[data-share]')?.addEventListener('click', () => {
    navigator.share({ title: 'Дневник', url: data.url }).catch(() => {});
  });
  // Внешний браузер этого же телефона, а не встроенный в Telegram.
  body.querySelector('[data-open]')?.addEventListener('click', () => {
    tg.openLink(data.url, { try_instant_view: false });
  });
}

export function openDevices() {
  const body = pushScreen('Устройства');
  const draw = (items) => {
    const others = items.some((item) => !item.main && !item.current && item.kind !== 'calendar');
    body.innerHTML = `<button class="btn device-add" data-add>${ICONS.plus} Добавить устройство</button>
      ${items.map(row).join('')}
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
    body.querySelector('[data-add]').addEventListener('click', openNewDevice);
    body.querySelector('[data-clients]')?.addEventListener('click', () => {
      const links = items.find((entry) => entry.kind === 'links');
      openLinkClients(links, async (screen) => {
        if (!window.confirm(confirmText(links))) return;
        try {
          draw((await post('/api/devices/revoke', { id: 'links' })).data.items);
          notify('success');
          screen.closest('.screen')?.querySelector('.screen-back')?.click();
        } catch (error) {
          banner(error.message, true);
        }
      });
    });
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
