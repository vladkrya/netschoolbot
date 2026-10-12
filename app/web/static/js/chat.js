// Школьные чаты: раздел «Чаты» во вкладке сообщений.
//
// Сервер — app/web/chat.py. Открытый чат раз в несколько секунд спрашивает
// новые сообщения (after) и изменения уже показанных (since): скрытие,
// удаление, реакции. Долгих соединений нет — через прокси и плохую связь
// это надёжнее.
'use strict';

import { api, banner, errorCard, esc, escRich, skeleton } from './core.js';
import { notify, select, tap } from './haptics.js';
import { ICONS } from './icons.js';
import { pushScreen } from './screens.js';
import { closeSheet, openSheet } from './sheet.js';

const POLL_MS = 4000;

let overview = null;

/* ─────────────────────────── Список чатов ─────────────────────────── */

export async function renderChats(container, force = false) {
  if (!overview || force) {
    container.innerHTML = skeleton(3);
    try {
      overview = (await api('/api/chat')).data;
    } catch (error) {
      container.innerHTML = errorCard(error, 'chatRetry');
      container.querySelector('#chatRetry')?.addEventListener('click', () => renderChats(container, true));
      return;
    }
  }
  if (!overview.joined && !overview.admin) {
    renderConsent(container);
    return;
  }
  renderRooms(container);
}

function renderConsent(container, error = '') {
  container.innerHTML = `
    <div class="card chat-consent">
      <div class="card-title">Школьные чаты</div>
      <p class="muted">Чат класса, параллели, всей школы и общий чат с ровесниками из других школ.</p>
      <ul class="chat-rules">${overview.rules.map((line) => `<li>${esc(line)}</li>`).join('')}</ul>
      <label class="chat-agree"><input type="checkbox" id="chatAgree">
        <span>Согласен с правилами и показом моего имени и номера класса</span></label>
      ${error ? `<p class="chat-error">${esc(error)}</p>` : ''}
      <button class="btn" id="chatJoin" disabled>Вступить</button>
    </div>`;
  const agree = container.querySelector('#chatAgree');
  const join = container.querySelector('#chatJoin');
  agree.addEventListener('change', () => { join.disabled = !agree.checked; select(); });
  join.addEventListener('click', async () => {
    join.disabled = true;
    join.classList.add('loading');
    try {
      overview = (await api('/api/chat/join', { method: 'POST', body: JSON.stringify({ consent: true }) })).data;
      notify('success');
      renderRooms(container);
    } catch (error) {
      notify('error');
      renderConsent(container, error.message);
    }
  });
}

function timeLabel(at) {
  const date = new Date(at * 1000);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
}

function renderRooms(container) {
  const identity = overview.admin && overview.joined ? `
    <div class="card chat-identity">
      <span>Писать как</span>
      <div class="chips">
        <button class="chip ${overview.as_admin ? 'active' : ''}" data-as-admin="1">admin</button>
        <button class="chip ${overview.as_admin ? '' : 'active'}" data-as-admin="0">${esc(overview.profile.first_name)}</button>
      </div>
    </div>` : '';
  const joinHint = overview.admin && !overview.joined
    ? '<button class="card chat-join-hint" id="chatJoinSelf">Вступить, чтобы писать и от своего имени ›</button>' : '';
  container.innerHTML = identity + joinHint + overview.rooms.map((room, index) => `
    <button class="card chat-room" data-room="${index}">
      <span class="chat-room-icon kind-${room.kind}">${ICONS.mail}</span>
      <span class="chat-room-main">
        <span class="chat-room-title">${esc(room.title)}</span>
        <span class="chat-room-last">${room.last
          ? `<b>${esc(room.last.author)}:</b> ${esc(room.last.text)}` : `<span class="muted">${esc(room.subtitle || 'Пока тихо')}</span>`}</span>
      </span>
      <span class="chat-room-side">
        ${room.last ? `<span class="muted small">${esc(timeLabel(room.last.at))}</span>` : ''}
        ${room.unread ? `<span class="nav-badge chat-unread">${room.unread > 99 ? '99+' : room.unread}</span>` : ''}
      </span>
    </button>`).join('') + (overview.joined
    ? '<button class="link chat-leave" id="chatLeave">Выйти из чатов</button>' : '');

  container.querySelectorAll('[data-room]').forEach((button) => button.addEventListener('click', () => {
    const room = overview.rooms[Number(button.dataset.room)];
    room.unread = 0;
    openRoom(room, () => renderChats(container, true));
  }));
  container.querySelectorAll('[data-as-admin]').forEach((button) => button.addEventListener('click', async () => {
    select();
    try {
      overview = (await api('/api/chat/identity', {
        method: 'POST', body: JSON.stringify({ as_admin: button.dataset.asAdmin === '1' }),
      })).data;
      renderRooms(container);
    } catch (error) {
      banner(error.message, true);
    }
  }));
  container.querySelector('#chatJoinSelf')?.addEventListener('click', () => renderConsent(container));
  container.querySelector('#chatLeave')?.addEventListener('click', async () => {
    if (!window.confirm('Выйти из чатов? Твои сообщения останутся, а вступить можно снова.')) return;
    overview = (await api('/api/chat/leave', { method: 'POST', body: '{}' })).data;
    renderChats(container);
  });
}

/* ─────────────────────────── Переписка ─────────────────────────── */

function authorLabel(message) {
  if (message.admin) return '<span class="chat-admin">admin</span>';
  return `${esc(message.author)}${message.grade ? ` <span class="muted">· ${message.grade}</span>` : ''}`;
}

function bodyText(message) {
  if (message.deleted) return '<span class="chat-gone">Сообщение удалено</span>';
  if (message.text == null) return '<span class="chat-gone">Сообщение скрыто модератором</span>';
  return escRich(message.text);
}

function dayKey(at) {
  return new Date(at * 1000).toDateString();
}

function dayTitle(at) {
  const date = new Date(at * 1000);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return 'Сегодня';
  if (date.toDateString() === yesterday.toDateString()) return 'Вчера';
  return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
}

function messageHtml(message) {
  const reactions = Object.entries(message.reactions || {})
    .map(([emoji, count]) => `<button class="chat-reaction ${message.my_reactions?.includes(emoji) ? 'on' : ''}"
      data-react="${esc(emoji)}">${esc(emoji)} ${count}</button>`).join('');
  return `<div class="chat-msg ${message.mine ? 'mine' : ''} ${message.admin ? 'admin' : ''} ${message.hidden ? 'hidden-msg' : ''}"
      data-id="${message.id}">
      <div class="chat-bubble">
        ${message.mine ? '' : `<div class="chat-author">${authorLabel(message)}</div>`}
        ${message.reply ? `<div class="chat-quote" data-jump="${message.reply.id}"><b>${esc(message.reply.author)}</b>
          <span>${message.reply.text == null ? 'сообщение скрыто' : esc(message.reply.text)}</span></div>` : ''}
        <div class="chat-text">${bodyText(message)}</div>
        <div class="chat-meta">${message.pinned ? '📌 ' : ''}${message.hidden ? 'скрыто · ' : ''}${esc(timeLabel(message.at))}</div>
      </div>
      ${reactions ? `<div class="chat-reactions">${reactions}</div>` : ''}
    </div>`;
}

export function openRoom(room, onClose) {
  const body = pushScreen(room.title, room.subtitle);
  const screen = body.closest('.screen');
  screen.classList.add('chat-screen');
  body.innerHTML = `
    <div class="chat-log"><div class="chat-older" hidden>Загрузка…</div><div class="chat-list"></div></div>
    <div class="chat-replying" hidden><span></span><button type="button" aria-label="Не отвечать">✕</button></div>
    <form class="chat-composer">
      <textarea rows="1" maxlength="2000" placeholder="Сообщение"></textarea>
      <button type="submit" class="chat-send" aria-label="Отправить">${ICONS.send}</button>
    </form>`;
  const log = body.querySelector('.chat-log');
  const list = body.querySelector('.chat-list');
  const input = body.querySelector('textarea');
  const replying = body.querySelector('.chat-replying');
  const messages = new Map();
  let lastId = 0;
  let firstId = null;
  let since = 0;
  let replyTo = null;
  let exhausted = false;
  let loadingOlder = false;
  let readSent = 0;

  const nearBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  const toBottom = () => { log.scrollTop = log.scrollHeight; };

  const redraw = () => {
    const ordered = [...messages.values()].sort((a, b) => a.id - b.id);
    let previousDay = '';
    list.innerHTML = ordered.map((message) => {
      const day = dayKey(message.at);
      const separator = day !== previousDay ? `<div class="chat-day">${esc(dayTitle(message.at))}</div>` : '';
      previousDay = day;
      return separator + messageHtml(message);
    }).join('') || '<div class="empty">Сообщений пока нет — напиши первым</div>';
  };

  const remember = (items) => {
    for (const message of items) {
      messages.set(message.id, message);
      lastId = Math.max(lastId, message.id);
      firstId = firstId == null ? message.id : Math.min(firstId, message.id);
    }
  };

  const markRead = () => {
    if (lastId <= readSent) return;
    readSent = lastId;
    api(`/api/chat/rooms/${encodeURIComponent(room.id)}/read`, {
      method: 'POST', body: JSON.stringify({ last_id: lastId }),
    }).catch(() => {});
  };

  const load = async () => {
    list.innerHTML = skeleton(4);
    try {
      const { data } = await api(`/api/chat/rooms/${encodeURIComponent(room.id)}/messages`);
      remember(data.messages);
      since = data.now;
      exhausted = data.messages.length < 50;
      redraw();
      toBottom();
      markRead();
    } catch (error) {
      list.innerHTML = errorCard(error);
    }
  };

  const poll = async () => {
    if (!body.isConnected) {
      clearInterval(timer);
      onClose?.();
      return;
    }
    if (document.hidden) return;
    try {
      const { data } = await api(
        `/api/chat/rooms/${encodeURIComponent(room.id)}/messages?after=${lastId}&since=${since}`);
      since = data.now;
      if (!data.messages.length && !data.changed.length) return;
      const stick = nearBottom();
      remember(data.messages);
      for (const message of data.changed) messages.set(message.id, message);
      redraw();
      if (stick) toBottom();
      if (stick) markRead();
    } catch {
      /* связь пропала — спросим в следующий раз */
    }
  };

  const loadOlder = async () => {
    if (exhausted || loadingOlder || firstId == null) return;
    loadingOlder = true;
    const older = body.querySelector('.chat-older');
    older.hidden = false;
    const height = log.scrollHeight;
    try {
      const { data } = await api(`/api/chat/rooms/${encodeURIComponent(room.id)}/messages?before=${firstId}`);
      exhausted = data.messages.length < 50;
      remember(data.messages);
      redraw();
      log.scrollTop = log.scrollHeight - height;
    } catch {
      /* попробуем при следующей прокрутке */
    } finally {
      older.hidden = true;
      loadingOlder = false;
    }
  };

  const setReply = (message) => {
    replyTo = message;
    replying.hidden = !message;
    if (message) {
      replying.querySelector('span').textContent = `Ответ ${message.admin ? 'admin' : message.author}: ${(message.text || '').slice(0, 80)}`;
      input.focus();
    }
  };

  const send = async () => {
    const text = input.value.trim();
    if (!text) return;
    const button = body.querySelector('.chat-send');
    button.disabled = true;
    try {
      const { data } = await api(`/api/chat/rooms/${encodeURIComponent(room.id)}/messages`, {
        method: 'POST', body: JSON.stringify({ text, reply_to: replyTo?.id ?? null }),
      });
      input.value = '';
      input.style.height = '';
      setReply(null);
      remember([data]);
      redraw();
      toBottom();
      readSent = data.id;
      tap('soft');
    } catch (error) {
      notify('error');
      banner(error.message, true);
    } finally {
      button.disabled = false;
    }
  };

  body.querySelector('.chat-composer').addEventListener('submit', (event) => {
    event.preventDefault();
    send();
  });
  input.addEventListener('keydown', (event) => {
    // Enter — отправить, Shift+Enter — новая строка (на телефоне Enter — перенос).
    if (event.key === 'Enter' && !event.shiftKey && window.matchMedia('(hover: hover)').matches) {
      event.preventDefault();
      send();
    }
  });
  input.addEventListener('input', () => {
    input.style.height = '';
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  });
  replying.querySelector('button').addEventListener('click', () => setReply(null));
  log.addEventListener('scroll', () => {
    if (log.scrollTop < 60) loadOlder();
    if (nearBottom()) markRead();
  });
  list.addEventListener('click', (event) => {
    const jump = event.target.closest('[data-jump]');
    if (jump) {
      list.querySelector(`[data-id="${jump.dataset.jump}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      return;
    }
    if (event.target.closest('a')) return;
    const element = event.target.closest('[data-id]');
    if (!element) return;
    const message = messages.get(Number(element.dataset.id));
    if (message) openActions(message, { setReply });
  });

  const timer = setInterval(poll, POLL_MS);
  load();
}

function openActions(message, { setReply }) {
  tap();
  const sheet = openSheet('', `
    <div class="chat-actions">
      ${message.text != null ? '<button class="btn outline" data-act="reply">Ответить</button>' : ''}
      ${message.text != null ? '<button class="btn outline" data-act="copy">Скопировать</button>' : ''}
    </div>`);
  sheet.querySelector('[data-act="reply"]')?.addEventListener('click', () => {
    closeSheet();
    setReply(message);
  });
  sheet.querySelector('[data-act="copy"]')?.addEventListener('click', async () => {
    closeSheet();
    try {
      await navigator.clipboard.writeText(message.text);
      banner('Скопировано');
      setTimeout(() => banner(''), 1500);
    } catch {
      /* буфер обмена недоступен */
    }
  });
}
