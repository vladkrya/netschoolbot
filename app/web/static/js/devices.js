// Настройки → «Устройства»: где открыт дневник и завершение сеансов.
// Сервер — app/web/devices.py. Telegram — основной сеанс, его не завершить.
'use strict';

import { api, banner, esc, initData, plural, tg } from './core.js';
import { notify } from './haptics.js';
import { ICONS } from './icons.js';
import { popScreen, pushScreen } from './screens.js';

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

// Как вошли на устройство (miniapp_tokens.method, app/web/account.py и др.).
const METHODS = {
  telegram: 'через Telegram',
  'telegram-widget': 'через Telegram',
  bot: 'через Telegram-бота',
  email: 'по почте и паролю',
  register: 'при регистрации по почте',
  reset: 'после сброса пароля по почте',
  'device-link': 'по ссылке «Добавить устройство»',
  icon: 'с иконки на экране «Домой»',
  install: 'по ссылке установки из бота',
};

// «Завершать, если сеанс неактивен»: дни, как в /api/devices/auto-end.
const INACTIVE = [[0, 'Никогда'], [7, '1 неделя'], [30, '1 месяц'], [90, '3 месяца'], [180, '6 месяцев'], [365, '1 год']];

function describe(item) {
  if (item.kind === 'telegram') return 'Основной сеанс — мини-приложение в боте. Завершить его нельзя.';
  if (item.kind === 'links') {
    return `Старые ссылки и иконки на экране «Домой», где ключ доступа прямо в адресе${item.count > 1 ? ` (${item.count} ${plural(item.count, 'ключ', 'ключа', 'ключей')})` : ''}. Отличить их друг от друга нельзя — завершаются разом.`;
  }
  if (item.kind === 'calendar') return 'Календарь телефона забирает расписание. Ничего, кроме расписания, по этой ссылке не открыть.';
  if (!METHODS[item.method]) return item.created ? `Вход ${when(item.created)}` : '';
  const how = `Вход ${METHODS[item.method]}`;
  return item.created ? `${how} · ${when(item.created)}` : how;
}

function row(item) {
  const seen = item.last_used ? `<span class="device-seen">Активность: ${when(item.last_used)}</span>` : '';
  const badge = item.current ? '<span class="device-badge">Это устройство</span>' : '';
  const clients = item.clients ? `<button class="device-more" data-clients>
      <span>${item.clients.length ? `${item.clients.length} ${plural(item.clients.length, 'устройство', 'устройства', 'устройств')}` : 'Какие устройства'}</span>${ICONS.right}</button>` : '';
  const action = item.main ? '' : `<button class="btn outline danger device-end" data-end="${esc(item.id)}">${item.current ? 'Выйти' : 'Завершить'}</button>`;
  const rename = item.kind === 'device'
    ? `<button class="link device-rename" data-rename="${esc(item.id)}">${item.name ? 'Изменить подпись' : 'Подписать'}</button>` : '';
  return `<div class="card device-row" data-row="${esc(item.id)}">
    <div class="device-head">
      <span class="device-icon">${ICONS[ICON[item.kind]] || ICONS.install}</span>
      <div class="device-title"><b>${esc(item.name || item.label)}</b>${badge}</div>
    </div>
    ${deviceDetail(item)}
    <p class="muted small">${esc(describe(item))} ${rename}</p>
    ${seen}${clients}${action}
  </div>`;
}

/** «Safari · iPhone» под своей подписью, модель с версией системы и адрес. */
function deviceDetail(item) {
  const parts = [item.name ? item.label : '', item.details, item.ip ? `адрес ${item.ip}` : ''].filter(Boolean);
  return parts.length ? `<p class="device-detail">${esc(parts.join(' · '))}</p>` : '';
}

function confirmText(item) {
  if (item.current) return 'Это устройство — вы выйдете из аккаунта здесь. Продолжить?';
  if (item.kind === 'links') return 'Завершить ссылки из бота? Старые иконки на экране «Домой» и ссылки с ключом в адресе перестанут открывать дневник. Если ваш календарь подписан по старой ссылке — переподпишитесь в меню.';
  if (item.kind === 'calendar') return 'Отключить подписку на календарь? Расписание в календаре перестанет обновляться.';
  return `Завершить сеанс «${item.name || item.label}»? На том устройстве дневник перестанет открываться — войти снова можно через бота или по почте.`;
}

/** Своя подпись сеанса прямо в карточке: «Планшет на кухне». */
function editName(card, item, onSaved) {
  const title = card.querySelector('.device-title');
  const before = title.innerHTML;
  title.innerHTML = `<form class="device-name-form">
      <input class="field" name="name" maxlength="40" placeholder="${esc(item.label)}" value="${esc(item.name || '')}"
        aria-label="Подпись устройства" autocomplete="off" enterkeyhint="done">
      <button class="btn" type="submit">Сохранить</button>
      <button class="link" type="button" data-cancel-name>Отмена</button>
    </form>`;
  card.classList.add('editing');
  const form = title.querySelector('form');
  const close = () => { title.innerHTML = before; card.classList.remove('editing'); };
  form.name.focus();
  form.querySelector('[data-cancel-name]').addEventListener('click', close);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      onSaved((await post('/api/devices/rename', { id: item.id, name: form.name.value })).data.items);
      notify('success');
    } catch (error) {
      button.disabled = false;
      banner(error.message, true);
    }
  });
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

/** Сколько осталось: «59:07». */
function countdown(seconds) {
  const total = Math.max(0, Math.round(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

/** QR-код картинкой: в Telegram — в чат с ботом (из окна файл не сохранить),
 *  в браузере — через «Поделиться» (там есть «Сохранить изображение») или загрузкой. */
async function saveQr(data) {
  if (initData) {
    await post('/api/devices/new-link/send-qr', { code: data.code });
    notify('success');
    banner('QR-код в чате с ботом', false, 2000);
    return;
  }
  const blob = await (await fetch(data.qr)).blob();
  const file = new File([blob], 'dnevnik-qr.png', { type: 'image/png' });
  if (navigator.canShare?.({ files: [file] })) {
    await navigator.share({ files: [file] }).catch(() => {});
    return;
  }
  const link = Object.assign(document.createElement('a'), { href: data.qr, download: file.name });
  link.click();
}

/** «Добавить устройство»: ссылка на 15 минут и одно открытие, QR. Экран ждёт,
 *  пока её откроют, и показывает, какое устройство вошло. */
async function openNewDevice(onConnected) {
  const body = pushScreen('Новое устройство');
  let timer = 0;
  const stop = () => clearInterval(timer);
  const finish = (title, text, action) => {
    stop();
    body.innerHTML = `<div class="card device-new device-finish">
        ${action === 'done' ? `<span class="device-finish-icon">${ICONS.check}</span>` : ''}
        <b>${esc(title)}</b>
        <p class="muted">${esc(text)}</p>
        ${action === 'done' ? '<button class="btn" data-done>Готово</button>' : `<button class="btn" data-again>${ICONS.plus} Новая ссылка</button>`}
      </div>`;
    body.querySelector('[data-done]')?.addEventListener('click', () => popScreen());
    body.querySelector('[data-again]')?.addEventListener('click', create);
  };
  const expired = () => finish('Ссылка истекла', 'Она действовала 15 минут. Создайте новую — старая уже ничего не открывает.');

  const show = (data) => {
    const external = initData && tg?.openLink;
    body.innerHTML = `
      <div class="card device-new">
        <p>Откройте ссылку на новом устройстве — в Safari, Chrome или другом браузере, — и дневник откроется без входа.
          На телефоне сразу появится подсказка, как добавить его на экран «Домой».</p>
        <div class="device-secret hidden">
          ${data.qr ? `<img class="device-qr" src="${data.qr}" alt="QR-код ссылки" width="220" height="220">
            <p class="muted small device-qr-hint">Наведите камеру телефона на код.</p>` : ''}
          <div class="device-link"><code>${esc(data.url)}</code></div>
          <button class="btn outline device-reveal" data-reveal>${ICONS.eye} Показать</button>
        </div>
        <p class="device-timer">Действует <b data-left>${countdown(data.expires - Date.now() / 1000)}</b> · на одно открытие
          <button class="link device-hide" data-hide hidden>Скрыть</button></p>
        <div class="device-new-actions">
          <button class="btn" data-copy>${ICONS.doc} Скопировать</button>
          ${navigator.share ? `<button class="btn outline" data-share>${ICONS.share} Поделиться</button>` : ''}
          ${external ? `<button class="btn outline" data-open>${ICONS.external} В браузере</button>` : ''}
          ${data.qr ? `<button class="btn outline" data-qr>${ICONS.qr} ${initData ? 'QR-код в чат' : 'Сохранить QR'}</button>` : ''}
        </div>
        <p class="muted small">Ссылка открывает ваш дневник — не пересылайте её чужим. Вошедшее устройство появится в «Устройствах», и его можно будет завершить.</p>
        <button class="btn outline danger device-end-all" data-cancel>Отменить ссылку</button>
      </div>`;
    const secret = body.querySelector('.device-secret');
    const hide = body.querySelector('[data-hide]');
    const reveal = (open) => {
      secret.classList.toggle('hidden', !open);
      hide.hidden = !open;
    };
    body.querySelector('[data-reveal]').addEventListener('click', () => reveal(true));
    hide.addEventListener('click', () => reveal(false));
    body.querySelector('[data-copy]').addEventListener('click', () => {
      navigator.clipboard?.writeText(data.url).then(
        () => { notify('success'); banner('Ссылка скопирована', false, 1500); },
        () => { notify('error'); banner('Не получилось скопировать — откройте и выделите ссылку вручную', true); },
      );
    });
    body.querySelector('[data-share]')?.addEventListener('click', () => {
      navigator.share({ title: 'Дневник', url: data.url }).catch(() => {});
    });
    // Внешний браузер этого же телефона, а не встроенный в Telegram.
    body.querySelector('[data-open]')?.addEventListener('click', () => {
      tg.openLink(data.url, { try_instant_view: false });
    });
    body.querySelector('[data-qr]')?.addEventListener('click', (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      saveQr(data).catch((error) => banner(error.message, true)).finally(() => { button.disabled = false; });
    });
    body.querySelector('[data-cancel]').addEventListener('click', async () => {
      stop();
      await post('/api/devices/new-link/cancel', { code: data.code }).catch(() => {});
      finish('Ссылка отменена', 'По ней больше не войти. Если понадобится — создайте новую.');
    });

    // Раз в секунду — таймер, раз в три — не открыли ли ссылку.
    const left = body.querySelector('[data-left]');
    let tick = 0;
    let asking = false;
    timer = setInterval(async () => {
      if (!body.isConnected) return stop();
      const seconds = data.expires - Date.now() / 1000;
      left.textContent = countdown(seconds);
      if (seconds <= 0) return expired();
      if (++tick % 3 || asking || document.hidden) return;
      asking = true;
      try {
        const { data: status } = await post('/api/devices/new-link/status', { code: data.code });
        if (!body.isConnected) return;
        if (status.state === 'used') {
          notify('success');
          onConnected?.();
          finish('Устройство подключено', `${status.label}. Оно уже в «Устройствах» — там его можно завершить.`, 'done');
        } else if (status.state === 'expired') {
          expired();
        } else if (status.state === 'gone') {
          finish('Ссылка отменена', 'Её отозвали — например, кнопкой «Завершить все другие сеансы». Создайте новую.');
        }
      } catch { /* сеть моргнула — спросим в следующий раз */ } finally {
        asking = false;
      }
    }, 1000);
  };

  async function create() {
    stop();
    body.innerHTML = '<div class="card"><div class="muted">Готовим ссылку…</div></div>';
    try {
      show((await post('/api/devices/new-link')).data);
    } catch (error) {
      body.innerHTML = `<div class="card error-card"><p>${esc(error.message)}</p></div>`;
    }
  }
  await create();
}

export function openDevices() {
  const body = pushScreen('Устройства');
  let inactiveDays = 0;
  const draw = (items, days = inactiveDays) => {
    inactiveDays = days;
    const others = items.some((item) => !item.main && !item.current && item.kind !== 'calendar');
    // Сначала это устройство, потом остальные по свежести, в конце — особые доступы.
    const recent = (a, b) => (b.last_used || b.created || 0) - (a.last_used || a.created || 0);
    const current = items.filter((item) => item.current);
    const devices = items.filter((item) => !item.current && (item.kind === 'telegram' || item.kind === 'device'))
      .sort((a, b) => (Number(Boolean(b.main)) - Number(Boolean(a.main))) || recent(a, b));
    const special = items.filter((item) => !item.current && (item.kind === 'links' || item.kind === 'calendar'));
    const group = (title, list) => (list.length ? `<h3 class="device-group">${title}</h3>${list.map(row).join('')}` : '');
    const options = INACTIVE.map(([value, label]) => `<option value="${value}"${value === days ? ' selected' : ''}>${label}</option>`).join('');
    body.innerHTML = `<p class="muted small device-note device-intro">Здесь все места, где открыт ваш дневник: у каждого видно, как и когда вошли, с какого устройства и откуда.
        Не узнаёте сеанс — завершите его.</p>
      <button class="btn device-add" data-add>${ICONS.plus} Добавить устройство</button>
      ${group('Это устройство', current)}
      ${group(current.length ? 'Другие устройства' : 'Устройства', devices)}
      ${group('Другие доступы', special)}
      ${others ? '<button class="btn outline danger device-end-all" data-end-all>Завершить все другие сеансы</button>' : ''}
      <div class="card device-auto">
        <label class="device-auto-row"><span><b>Завершать сеанс, если он неактивен</b></span>
          <select class="field" data-inactive aria-label="Через сколько завершать неактивные сеансы">${options}</select></label>
        <p class="muted small">Если на устройстве не открывали дневник дольше этого срока, сеанс завершится сам — там понадобится войти заново. Telegram и подписка на календарь не завершаются.</p>
      </div>
      <p class="muted small device-note">Если ссылка на дневник могла попасть к чужим — завершите лишние сеансы или отправьте боту /app_reset: он отзовёт все ссылки сразу.</p>`;
    body.querySelector('[data-inactive]').addEventListener('change', async (event) => {
      const select = event.currentTarget;
      select.disabled = true;
      try {
        const { data } = await post('/api/devices/auto-end', { days: Number(select.value) });
        draw(data.items, data.inactive_days);
        notify('success');
      } catch (error) {
        select.value = String(inactiveDays);
        select.disabled = false;
        banner(error.message, true);
      }
    });
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
    body.querySelectorAll('[data-rename]').forEach((button) => button.addEventListener('click', () => {
      const item = items.find((entry) => entry.id === button.dataset.rename);
      if (item) editName(button.closest('.device-row'), item, draw);
    }));
    body.querySelector('[data-add]').addEventListener('click', () => openNewDevice(load));
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
  // После входа нового устройства список обновляется, пока он под экраном ссылки.
  const load = () => api('/api/devices').then(({ data }) => draw(data.items, data.inactive_days))
    .catch((error) => { body.innerHTML = `<div class="card error-card"><p>${esc(error.message)}</p></div>`; });
  body.innerHTML = '<div class="card"><div class="muted">Загрузка…</div></div>';
  load();
  // Вход, выход и подписи на других устройствах — сразу здесь (app/web/sync.py).
  const live = ({ detail }) => {
    if (!body.isConnected) return window.removeEventListener('netschool:sync', live);
    if (detail.topic === 'devices' && !body.querySelector('.device-row.editing')) load();
  };
  window.addEventListener('netschool:sync', live);
}
