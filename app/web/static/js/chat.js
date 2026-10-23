// Школьные чаты: раздел «Чаты» во вкладке сообщений.
//
// Сервер — app/web/chat.py. По сигналу WebSocket открытый чат запрашивает
// новые сообщения (after) и изменения уже показанных (since): скрытие,
// удаление, реакции. При недоступном WebSocket — опрос раз в 4 секунды.
'use strict';

import { api, banner, errorCard, esc, escRich, skeleton } from './core.js';
import { notify, select, tap } from './haptics.js';
import { ICONS } from './icons.js';
import { pushScreen, reopenOnRefresh, topScreen } from './screens.js';
import { closeSheet, openSheet } from './sheet.js';

import { chatConnected, chatEvents, refreshChatUnread, sendTyping, watchRoom } from './chat-live.js';
const POLL_MS = 4000;
const NOTIFY_LABELS = { all: 'Все сообщения', mentions: 'Только упоминания и ответы', off: 'Выключены' };
let roomsContainer = null;
let refreshingRooms = false;
chatEvents.addEventListener('change', async ({ detail }) => {
  if (!roomsContainer?.isConnected || roomsContainer.dataset.chatView !== '1' || !['msg', 'upd', 'unread', 'connected', 'snapshot'].includes(detail.t) || refreshingRooms) return;
  refreshingRooms = true;
  try {
    const fresh = (await api('/api/chat')).data;
    // Ничего не поменялось — не трогаем список: иначе он заново «проявлялся»
    // после каждого фонового обновления (сразу после вступления — дважды).
    const same = JSON.stringify(fresh) === JSON.stringify(overview);
    overview = fresh;
    if (!same && roomsContainer.isConnected && roomsContainer.dataset.chatView === '1' && !roomsContainer.querySelector('.chat-consent') && !roomsContainer.querySelector('.chat-nick-form')) renderRooms(roomsContainer, true);
  } catch { /* повторим при следующем событии */ }
  finally { refreshingRooms = false; }
});

let overview = null;

/* ─────────────────────────── Список чатов ─────────────────────────── */

export async function renderChats(container, force = false) {
  container.dataset.chatView = '1';
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
  if (overview.joined && !overview.profile?.nick) {
    renderNick(container);
    return;
  }
  renderRooms(container);
}

function renderConsent(container, error = '') {
  container.innerHTML = `
    <div class="card chat-consent">
      <div class="card-title">Школьные чаты</div>
      <p class="muted">Чат своей параллели (все 9-е твоей школы), всей школы и — с 6 класса — общий чат учеников всех школ.</p>
      <ul class="chat-rules">${overview.rules.map((line) => `<li>${esc(line)}</li>`).join('')}</ul>
      <label class="detail-label" for="chatNick">Твой ник</label>
      ${overview.saved_nick
    ? `<input class="field" id="chatNick" value="${esc(overview.saved_nick)}" disabled>
      <p class="muted small">Ник остаётся прежним — сменить его может только админ.</p>`
    : `<input class="field" id="chatNick" maxlength="20" autocomplete="off" placeholder="Например, Cosmos_9">
      <p class="muted small">${esc(overview.nick_hint || '3–20 символов: буквы, цифры и _, начинается с буквы')}</p>`}
      <label class="chat-agree"><input type="checkbox" id="chatAgree">
        <span>Согласен с правилами и показом моего ника и номера класса</span></label>
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
      overview = (await api('/api/chat/join', { method: 'POST', body: JSON.stringify({ consent: true, nick: container.querySelector('#chatNick').value.trim() }) })).data;
      notify('success');
      refreshChatUnread();
      renderRooms(container);
    } catch (error) {
      notify('error');
      container.querySelector('.chat-error')?.remove();
      join.insertAdjacentHTML('beforebegin', `<p class="chat-error">${esc(error.message)}</p>`);
      join.disabled = !agree.checked;
      join.classList.remove('loading');
    }
  });
}

function renderNick(container) {
  container.innerHTML = `<div class="card chat-nick-form"><div class="card-title">Выбери ник для чатов</div>
    <p class="muted">Настоящее имя больше не показывается. Другие участники увидят только ник и номер класса.</p>
    <input class="field" maxlength="20" autocomplete="off" placeholder="Твой ник">
    <p class="muted small">${esc(overview.nick_hint)}</p><button class="btn">Сохранить</button></div>`;
  const input = container.querySelector('input');
  const button = container.querySelector('button');
  button.addEventListener('click', async () => {
    button.disabled = true;
    const data = await act('/api/chat/nick', { nick: input.value.trim() });
    button.disabled = false;
    if (data) { overview = data; renderRooms(container); refreshChatUnread(); }
  });
}

function adminRename(userId, nick, done) {
  const sheet = openSheet('Сменить ник участника', `<input class="field" maxlength="20" value="${esc(nick || '')}">
    <p class="muted small">${esc(overview.nick_hint)}</p><button class="btn">Сохранить</button>`);
  const button = sheet.querySelector('button');
  button.addEventListener('click', async () => {
    button.disabled = true;
    const data = await act(`/api/chat/profiles/${userId}/nick`, { nick: sheet.querySelector('input').value.trim() });
    button.disabled = false;
    if (data) { closeSheet(); done?.(data); }
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

/** quiet — фоновое обновление: список меняется на месте, без анимации появления. */
function renderRooms(container, quiet = false) {
  roomsContainer = container;
  container.classList.toggle('quiet', quiet);
  const identity = overview.admin && overview.joined ? `
    <div class="card chat-identity">
      <span>Аккаунт</span>
      <div class="chips">
        <button class="chip ${overview.as_admin ? 'active' : ''}" data-as-admin="1">Админ${verifiedBadge('admin')}</button>
        <button class="chip ${overview.as_admin ? '' : 'active'}" data-as-admin="0">${esc(overview.profile.nick)}${verifiedBadge(overview.role)}</button>
      </div>
    </div>` : '';
  const reports = overview.moderator ? `
    <button class="card chat-room chat-reports" id="chatReports">
      <span class="chat-room-icon">⚠️</span>
      <span class="chat-room-main"><span class="chat-room-title">Жалобы</span>
        <span class="chat-room-last">${overview.reports ? `Ждут решения: ${overview.reports}` : 'Новых нет'}</span></span>
      ${overview.reports ? `<span class="nav-badge chat-unread">${overview.reports}</span>` : ''}
    </button>` : '';
  const joinHint = overview.admin && !overview.joined
    ? '<button class="card chat-join-hint" id="chatJoinSelf">Вступить, чтобы писать и от своего имени ›</button>' : '';
  const startPrivate = overview.admin_mode
    ? '<button class="btn outline" id="chatStartPrivate">Написать участнику</button>' : '';
  container.innerHTML = identity + reports + joinHint + startPrivate + overview.rooms.map((room, index) => `
    <button class="card chat-room" data-room="${index}">
      <span class="chat-room-icon kind-${room.kind}">${ICONS.mail}</span>
      <span class="chat-room-main">
        <span class="chat-room-title">${esc(room.title)}${verifiedBadge(room.role)}</span>
        <span class="chat-room-last">${room.last
          ? `<b>${esc(room.last.author)}${verifiedBadge(room.last.role)}:</b> ${esc(room.last.text)}` : `<span class="muted">${esc(room.subtitle || 'Пока тихо')}</span>`}</span>
      </span>
      <span class="chat-room-side">
        ${room.last ? `<span class="muted small">${esc(timeLabel(room.last.at))}</span>` : ''}
        ${room.unread ? `<span class="nav-badge chat-unread">${room.unread > 99 ? '99+' : room.unread}</span>` : ''}
      </span>
    </button>`).join('') + (overview.joined
    ? `${overview.admin ? '<span class="muted small">Ник:' : '<button class="link chat-leave" id="chatEditNick">Ник:'} ${esc(overview.profile.nick)}${verifiedBadge(overview.role)}${overview.admin ? '</span>' : ' · смена через админа</button>'}
       <button class="link chat-leave" id="chatLeave">Выйти из чатов</button>` : '');
  container.querySelector('#chatStartPrivate')?.addEventListener('click', () => chooseParticipant(() => renderChats(container, true)));
  container.querySelector('#chatEditNick')?.addEventListener('click', () => {
    const room = overview.rooms.find(item => item.kind === 'support' && item.own);
    if (room) openRoom(room, () => renderChats(container, true));
  });

  container.querySelectorAll('[data-room]').forEach((button) => button.addEventListener('click', () => {
    const room = overview.rooms[Number(button.dataset.room)];
    room.unread = 0;
    openRoom(room, () => { if (container.dataset.chatView === '1') renderChats(container, true); });
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
  container.querySelector('#chatReports')?.addEventListener('click', () => openReports(() => renderChats(container, true)));
  container.querySelector('#chatLeave')?.addEventListener('click', async () => {
    if (!window.confirm('Выйти из чатов? Твои сообщения останутся, а вступить можно снова.')) return;
    overview = (await api('/api/chat/leave', { method: 'POST', body: '{}' })).data;
    refreshChatUnread();
    renderChats(container);
  });
}

async function chooseParticipant(onClose) {
  const sheet = openSheet('Написать участнику', skeleton(3));
  try {
    const { data } = await api('/api/chat/participants');
    sheet.innerHTML = '<input class="field" placeholder="Найти по нику" aria-label="Найти участника"><div class="chat-participant-list"></div>';
    const list = sheet.querySelector('.chat-participant-list');
    const input = sheet.querySelector('input');
    const draw = () => {
      const items = data.items.filter(item => `${item.nick} ${item.school}`.toLowerCase().includes(input.value.trim().toLowerCase()));
      list.innerHTML = items.map((item, index) => `<button class="card chat-room" data-participant="${index}"><span>${esc(item.nick)}${verifiedBadge(item.role)}<br><span class="muted small">${item.grade} класс · ${esc(item.school)}</span></span></button>`).join('') || '<p class="muted">Участников нет</p>';
      list.querySelectorAll('[data-participant]').forEach(button => button.addEventListener('click', () => {
        const item = items[Number(button.dataset.participant)];
        closeSheet();
        openRoom(item.room, onClose);
      }));
    };
    input.addEventListener('input', draw);
    draw();
  } catch (error) { sheet.innerHTML = errorCard(error); }
}

/* ─────────────────────────── Переписка ─────────────────────────── */

function verifiedBadge(role) {
  const label = role === 'admin' ? 'Администратор' : role === 'moderator' ? 'Модератор' : '';
  if (!label) return '';
  return `<span class="chat-verified" role="img" aria-label="${label}" title="${label}"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="9" fill="currentColor"/><path d="m5.5 10 3 3 6-6" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>`;
}

function authorLabel(message) {
  if (message.admin) return `<span class="chat-admin">admin</span>${verifiedBadge(message.role)}`;
  return `${esc(message.author)}${verifiedBadge(message.role)}${message.grade ? ` <span class="muted">· ${message.grade}</span>` : ''}`;
}

function bodyText(message) {
  if (message.deleted) return `<span class="chat-gone">${message.deleted_by === 'author' ? 'Сообщение удалено автором' : 'Сообщение удалено администратором'}</span>`;
  if (message.text == null) return '<span class="chat-gone">Сообщение скрыто модератором</span>';
  const text = escRich(message.text).replace(/(^|\s)@([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_]{1,19})/g, '$1<b class="chat-mention">@$2</b>');
  return message.poll ? `<b>📊 ${text}</b>${pollHtml(message.poll)}` : text;
}

function pollHtml(poll) {
  return `<div class="chat-poll">${poll.options.map((option, index) => {
    const share = poll.total ? Math.round((option.votes / poll.total) * 100) : 0;
    return `<button class="chat-poll-option ${poll.my === index ? 'on' : ''}" data-vote="${index}">
      <span class="chat-poll-bar" style="width:${share}%"></span>
      <span class="chat-poll-text">${poll.my === index ? '✓ ' : ''}${esc(option.text)}</span>
      <span class="chat-poll-count">${option.votes}</span></button>`;
  }).join('')}<div class="chat-poll-total">Голосов: ${poll.total}</div></div>`;
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
  if (message.kind === 'system') return `<div class="chat-system" data-id="${message.id}">${bodyText(message)}</div>`;
  const reactions = Object.entries(message.reactions || {})
    .map(([emoji, count]) => `<button class="chat-reaction ${message.my_reactions?.includes(emoji) ? 'on' : ''}"
      data-react="${esc(emoji)}">${esc(emoji)} ${count}</button>`).join('');
  return `<div class="chat-msg ${message.mine ? 'mine' : ''} ${message.admin ? 'admin' : ''} ${message.hidden ? 'hidden-msg' : ''}"
      data-id="${message.id}">
      <div class="chat-bubble">
        ${message.mine && !message.role ? '' : `<div class="chat-author">${authorLabel(message)}</div>`}
        ${message.reply ? `<div class="chat-quote" data-jump="${message.reply.id}"><b>${esc(message.reply.author)}${verifiedBadge(message.reply.role)}</b>
          <span>${message.reply.text == null ? 'сообщение скрыто' : esc(message.reply.text)}</span></div>` : ''}
        <div class="chat-text">${bodyText(message)}</div>
        <div class="chat-meta">${message.pinned ? '📌 ' : ''}${message.hidden ? 'скрыто · ' : ''}${message.edited ? 'изменено · ' : ''}${esc(timeLabel(message.at))}${message.mine ? ` <span class="chat-read" aria-label="${message.read ? 'Прочитано' : 'Отправлено'}">${message.read ? '✓✓' : '✓'}</span>` : ''}</div>
        ${reactions ? `<div class="chat-reactions">${reactions}</div>` : ''}
      </div>
    </div>`;
}

export function openRoom(room, onClose, targetId = null) {
  const body = pushScreen(room.title, room.subtitle);
  const screen = body.closest('.screen');
  screen.classList.add('chat-screen');
  screen.dataset.room = room.id;
  watchRoom(room.id);
  const bell = document.createElement('button');
  bell.className = 'screen-back chat-bell';
  bell.setAttribute('aria-label', 'Уведомления о чате');
  screen.querySelector('.screen-head').appendChild(bell);
  if (overview.admin_mode && room.kind === 'support' && room.owner_id) {
    const rename = document.createElement('button');
    rename.className = 'chip';
    rename.textContent = 'Сменить ник';
    rename.addEventListener('click', () => adminRename(room.owner_id, room.owner_nick, (data) => {
      room.owner_nick = data.nick;
      renderChats(roomsContainer, true);
    }));
    screen.querySelector('.screen-head').appendChild(rename);
  }
  const paintBell = (mode) => {
    room.notify = mode || 'mentions';
    bell.textContent = room.notify === 'off' ? '🔕' : '🔔';
    bell.title = NOTIFY_LABELS[room.notify];
  };
  paintBell(room.notify);
  bell.addEventListener('click', () => {
    const sheet = openSheet('Уведомления о чате', `<div class="chat-actions">${Object.entries(NOTIFY_LABELS)
      .map(([mode, label]) => `<button class="btn outline" data-notify="${mode}">${room.notify === mode ? '✓ ' : ''}${label}</button>`).join('')}</div>`);
    sheet.querySelectorAll('[data-notify]').forEach(button => button.addEventListener('click', async () => {
      const data = await act(`/api/chat/rooms/${encodeURIComponent(room.id)}/notify`, { mode: button.dataset.notify });
      if (data) { paintBell(data.notify); closeSheet(); refreshChatUnread(); }
    }));
  });
  body.innerHTML = `
    <button class="chat-pinned" hidden></button>
    <div class="chat-log"><div class="chat-older" hidden>Загрузка…</div><div class="chat-list"></div></div>
    <div class="chat-typing" hidden></div>
    <div class="chat-replying" hidden><span></span><button type="button" aria-label="Не отвечать">✕</button></div>
    <div class="chat-members" hidden></div>
    <form class="chat-composer">
      ${room.kind === 'global' ? '' : '<button type="button" class="chat-poll-btn" aria-label="Опрос">📊</button>'}
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
  let moderator = false;
  let admin = false;
  let polling = false;
  let pendingPoll = false;
  let ready = false;
  const pinnedBar = body.querySelector('.chat-pinned');

  const showPinned = (pinned) => {
    pinnedBar.hidden = !pinned;
    if (pinned) {
      pinnedBar.dataset.jump = pinned.id;
      pinnedBar.innerHTML = `<b>📌 Закреплено</b><span>${esc(pinned.text || '')}</span>`;
    }
  };

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

  const markRead = async () => {
    if (lastId <= readSent || document.hidden || topScreen() !== screen || !nearBottom()) return;
    const upto = lastId;
    readSent = upto;
    try {
      await api(`/api/chat/rooms/${encodeURIComponent(room.id)}/read`, { method: 'POST', body: JSON.stringify({ last_id: upto }) });
      refreshChatUnread();
    } catch { readSent = 0; }
  };

  const jumpTo = async (id) => {
    let element = list.querySelector(`[data-id="${Number(id)}"]`);
    if (!element) {
      try {
        const { data } = await api(`/api/chat/rooms/${encodeURIComponent(room.id)}/messages?around=${Number(id)}`);
        remember(data.messages);
        redraw();
        element = list.querySelector(`[data-id="${Number(id)}"]`);
      } catch (error) { banner(error.message, true); return; }
    }
    if (!element) { banner('Сообщение не найдено', true); return; }
    element.scrollIntoView({ block: 'center', behavior: 'smooth' });
    element.classList.add('chat-highlight');
    setTimeout(() => element.classList.remove('chat-highlight'), 2200);
  };

  const load = async () => {
    list.innerHTML = skeleton(4);
    try {
      const { data } = await api(`/api/chat/rooms/${encodeURIComponent(room.id)}/messages`);
      remember(data.messages);
      since = data.now;
      moderator = data.moderator;
      admin = data.admin;
      paintBell(data.notify);
      showPinned(data.pinned);
      exhausted = data.messages.length < 50;
      redraw();
      ready = true;
      if (targetId) await jumpTo(targetId);
      else { toBottom(); markRead(); }
      poll(); // догнать сигнал, который пришёл во время загрузки
      if (data.need_nick) {
        input.disabled = true;
        input.placeholder = 'Выбери ник в списке чатов';
        body.querySelector('.chat-send').disabled = true;
      }
    } catch (error) {
      list.innerHTML = errorCard(error);
    }
  };

  const poll = async () => {
    if (!body.isConnected || document.hidden || !ready) return;
    if (polling) { pendingPoll = true; return; }
    polling = true;
    try {
      const { data } = await api(`/api/chat/rooms/${encodeURIComponent(room.id)}/messages?after=${lastId}&since=${since}`);
      // Полная страница: не теряем изменения следующих страниц.
      if (data.messages.length < 50) since = data.now;
      else pendingPoll = true;
      showPinned(data.pinned);
      moderator = data.moderator;
      admin = data.admin;
      const stick = nearBottom();
      const scroll = log.scrollTop;
      remember(data.messages);
      for (const message of data.changed) messages.set(message.id, message);
      for (const message of messages.values()) if (message.mine) message.read = data.others_read >= message.id;
      redraw();
      if (stick) { toBottom(); markRead(); }
      else log.scrollTop = scroll;
    } catch { /* связь пропала — следующий запрос догонит */ }
    finally {
      polling = false;
      if (pendingPoll) { pendingPoll = false; poll(); }
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
      log.scrollTop += log.scrollHeight - height;
    } catch {
      /* попробуем при следующей прокрутке */
    } finally {
      older.hidden = true;
      loadingOlder = false;
    }
  };

  let editing = null;
  const setReply = (message) => {
    replyTo = message;
    if (message) editing = null;
    replying.hidden = !message;
    if (message) {
      replying.querySelector('span').textContent = `Ответ ${message.admin ? 'admin' : message.author}: ${(message.text || '').slice(0, 80)}`;
      input.focus();
    }
  };
  const setEdit = (message) => {
    const wasEditing = editing;
    editing = message;
    replyTo = null;
    replying.hidden = !message;
    if (message) {
      replying.querySelector('span').textContent = `Изменение: ${(message.text || '').slice(0, 80)}`;
      input.value = message.text || '';
      input.dispatchEvent(new Event('input'));
      input.focus();
    } else if (wasEditing) {
      input.value = '';
      input.style.height = '';
    }
  };

  const send = async () => {
    const text = input.value.trim();
    if (!text) return;
    const button = body.querySelector('.chat-send');
    button.disabled = true;
    if (editing) {
      try {
        const { data } = await api(`/api/chat/messages/${editing.id}/edit`, {
          method: 'POST', body: JSON.stringify({ text }),
        });
        update(data);
        setEdit(null);
        tap('soft');
      } catch (error) {
        notify('error');
        banner(error.message, true);
      } finally {
        button.disabled = false;
      }
      return;
    }
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
  const suggestions = body.querySelector('.chat-members');
  let suggestTimer = 0;
  let suggestVersion = 0;
  input.addEventListener('input', () => {
    clearTimeout(suggestTimer);
    const version = ++suggestVersion;
    suggestions.hidden = true;
    const before = input.value.slice(0, input.selectionStart);
    const match = before.match(/(?:^|\s)@([A-Za-zА-Яа-яЁё0-9_]*)$/);
    if (match) suggestTimer = setTimeout(async () => {
      try {
        const { data } = await api(`/api/chat/rooms/${encodeURIComponent(room.id)}/members?q=${encodeURIComponent(match[1])}`);
        if (version !== suggestVersion || !body.isConnected) return;
        suggestions.innerHTML = data.items.map((item, index) => `<button type="button" data-member="${index}">@${esc(item.nick)}${verifiedBadge(item.role)}${item.grade ? ` · ${item.grade}` : ''}</button>`).join('');
        suggestions.hidden = !data.items.length;
        suggestions.querySelectorAll('button').forEach(button => button.addEventListener('click', () => {
          const at = before.length - match[1].length - 1;
          const insert = `@${data.items[Number(button.dataset.member)].nick} `;
          input.value = input.value.slice(0, at) + insert + input.value.slice(before.length);
          input.focus();
          input.setSelectionRange(at + insert.length, at + insert.length);
          suggestions.hidden = true;
          ++suggestVersion;
        }));
      } catch { /* без подсказки можно набрать ник вручную */ }
    }, 180);
  });
  input.addEventListener('input', () => {
    input.style.height = '';
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  });
  replying.querySelector('button').addEventListener('click', () => (editing ? setEdit(null) : setReply(null)));
  // «Печатает…»: своё — другим по сокету, чужое — строкой над полем ввода.
  input.addEventListener('input', () => { if (input.value.trim() && !editing) sendTyping(room.id); });
  const typingLine = body.querySelector('.chat-typing');
  const typists = new Map();
  const paintTyping = () => {
    const now = Date.now();
    for (const [nick, until] of typists) if (until < now) typists.delete(nick);
    const names = [...typists.keys()];
    typingLine.hidden = !names.length;
    typingLine.textContent = !names.length ? ''
      : names.length === 1 ? `${names[0]} печатает…`
        : names.length <= 3 ? `${names.join(', ')} печатают…` : `${names.length} человека печатают…`;
  };
  const typingTimer = setInterval(() => {
    if (!body.isConnected) { clearInterval(typingTimer); return; }
    paintTyping();
  }, 1000);
  log.addEventListener('scroll', () => {
    if (log.scrollTop < 60) loadOlder();
    if (nearBottom()) markRead();
  });
  const update = (message) => {
    messages.set(message.id, message);
    redraw();
  };
  pinnedBar.addEventListener('click', () => {
    jumpTo(pinnedBar.dataset.jump);
  });
  body.querySelector('.chat-poll-btn')?.addEventListener('click', () => openPollEditor(room, (message) => {
    remember([message]);
    redraw();
    toBottom();
  }));
  const mention = (message) => {
    const name = message.admin ? 'admin' : message.author;
    input.value = `${input.value.replace(/\s*$/, '')}${input.value.trim() ? ' ' : ''}@${name} `;
    input.focus();
  };
  list.addEventListener('click', (event) => {
    const vote = event.target.closest('[data-vote]');
    if (vote) {
      const element = vote.closest('[data-id]');
      act(`/api/chat/messages/${element.dataset.id}/vote`, { option: Number(vote.dataset.vote) }, update);
      select();
      return;
    }
    const react = event.target.closest('[data-react]');
    if (react) {
      const element = react.closest('[data-id]');
      toggleReaction(Number(element.dataset.id), react.dataset.react, update);
      return;
    }
    const jump = event.target.closest('[data-jump]');
    if (jump) {
      jumpTo(jump.dataset.jump);
      return;
    }
    if (event.target.closest('a')) return;
    const element = event.target.closest('[data-id]');
    if (!element) return;
    const message = messages.get(Number(element.dataset.id));
    if (message && message.kind !== 'system') openActions(message, { setReply, setEdit, update, moderator, admin, room, mention });
  });

  const live = ({ detail }) => {
    if (detail.t === 'typing') {
      if (detail.room === room.id && detail.nick) {
        typists.set(detail.nick, Date.now() + 4500);
        paintTyping();
      }
      return;
    }
    if (detail.t === 'msg' && detail.room === room.id) {
      // Сообщение ушло — «печатает» у его автора уже неправда.
      typists.clear();
      paintTyping();
    }
    if (detail.room === room.id || detail.t === 'connected') poll();
  };
  chatEvents.addEventListener('change', live);
  const timer = setInterval(() => {
    if (!body.isConnected) {
      clearInterval(timer);
      chatEvents.removeEventListener('change', live);
      watchRoom(topScreen()?.dataset.room || null);
      refreshChatUnread();
      onClose?.();
      return;
    }
    watchRoom(document.hidden || topScreen() !== screen ? null : room.id);
    if (!chatConnected()) poll();
  }, POLL_MS);
  load();
}

async function toggleReaction(id, emoji, update) {
  select();
  try {
    const { data } = await api(`/api/chat/messages/${id}/react`, { method: 'POST', body: JSON.stringify({ emoji }) });
    update(data);
  } catch (error) {
    banner(error.message, true);
  }
}

async function act(path, body, done) {
  try {
    const { data } = await api(path, { method: 'POST', body: JSON.stringify(body) });
    done?.(data);
    return data;
  } catch (error) {
    notify('error');
    banner(error.message, true);
    return null;
  }
}

function openActions(message, { setReply, setEdit, update, moderator, admin, room, mention }) {
  tap();
  const alive = message.text != null;
  // Сервер проверит то же: своё, от той же подписи, обычный текст, двое суток.
  const editable = message.mine && alive && message.kind === 'text' && !message.poll
    && Date.now() / 1000 - message.at < 48 * 3600;
  const canReact = !message.deleted;
  const author = !message.mine && !message.admin;
  const button = (act, label, extra = '') => `<button class="btn outline ${extra}" data-act="${act}">${label}</button>`;
  const sheet = openSheet('', `
    ${canReact ? `<div class="chat-react-row">${(overview?.reactions || ['👍', '❤️', '😂', '😮', '😢', '🔥'])
      .map((emoji) => `<button class="chat-react-pick ${message.my_reactions?.includes(emoji) ? 'on' : ''}"
        data-pick="${emoji}">${emoji}</button>`).join('')}</div>` : ''}
    <div class="chat-actions">
      ${alive ? button('reply', 'Ответить') : ''}
      ${editable ? button('edit', 'Изменить') : ''}
      ${!message.mine ? button('mention', `Упомянуть ${esc(message.admin ? 'admin' : message.author)}`) : ''}
      ${alive ? button('copy', 'Скопировать') : ''}
      ${message.mine || admin ? button('reads', 'Кто прочитал') : ''}
      ${author && alive ? button('report', 'Пожаловаться', 'danger') : ''}
      ${(message.mine || moderator) && !message.deleted ? button('delete', 'Удалить', 'danger') : ''}
      ${moderator && !message.deleted ? (message.hidden ? button('unhide', 'Показать снова') : button('hide', 'Скрыть')) : ''}
      ${moderator && alive ? (message.pinned ? button('unpin', 'Открепить') : button('pin', 'Закрепить')) : ''}
      ${moderator && author ? button('mute60', 'Запретить писать на 1 час') + button('mute1440', 'Запретить писать на сутки') : ''}
      ${admin && !message.admin && message.author_id ? button('rename', 'Сменить ник') : ''}
      ${admin && author ? button('ban', 'Заблокировать в чатах', 'danger') + button('unban', 'Разблокировать') : ''}
      ${admin && author && !['global', 'support'].includes(room.kind) ? button('moderator', 'Сделать модератором школы') + button('unmoderator', 'Снять модератора') : ''}
    </div>`);
  sheet.querySelectorAll('[data-pick]').forEach((pick) => pick.addEventListener('click', () => {
    closeSheet();
    toggleReaction(message.id, pick.dataset.pick, update);
  }));
  const on = (name, handler) => sheet.querySelector(`[data-act="${name}"]`)?.addEventListener('click', () => {
    closeSheet();
    handler();
  });
  const path = `/api/chat/messages/${message.id}`;
  on('reply', () => setReply(message));
  on('edit', () => setEdit(message));
  on('reads', async () => {
    const view = openSheet('Кто прочитал', skeleton(2));
    try {
      const { data } = await api(`${path}/reads`);
      view.innerHTML = data.items.map(item => `<div class="chat-reader">${esc(item.nick)}${verifiedBadge(item.role)}${item.grade ? ` · ${item.grade}` : ''}</div>`).join('') || '<p class="muted">Пока никто не прочитал</p>';
    } catch (error) { view.innerHTML = errorCard(error); }
  });
  on('rename', () => adminRename(message.author_id, message.author, () => { refreshChatUnread(); }));
  on('mention', () => mention(message));
  on('copy', async () => {
    try {
      await navigator.clipboard.writeText(message.text);
      banner('Скопировано');
      setTimeout(() => banner(''), 1500);
    } catch {
      /* буфер обмена недоступен */
    }
  });
  on('report', () => act(`${path}/report`, {}, (data) => {
    update(data);
    notify('success');
    banner('Жалоба отправлена — модераторы посмотрят');
    setTimeout(() => banner(''), 2500);
  }));
  on('delete', () => {
    if (window.confirm('Удалить сообщение?')) act(`${path}/moderate`, { action: 'delete' }, update);
  });
  for (const action of ['hide', 'unhide', 'pin', 'unpin']) {
    on(action, () => act(`${path}/moderate`, { action }, update));
  }
  const toAuthor = (body, text) => act(`${path}/author`, body, () => {
    notify('success');
    banner(text);
    setTimeout(() => banner(''), 2500);
  });
  on('mute60', () => toAuthor({ action: 'mute', minutes: 60 }, 'Не сможет писать час'));
  on('mute1440', () => toAuthor({ action: 'mute', minutes: 1440 }, 'Не сможет писать сутки'));
  on('ban', () => {
    if (window.confirm(`Заблокировать ${message.author} во всех чатах?`)) toAuthor({ action: 'ban' }, 'Заблокирован');
  });
  on('unban', () => toAuthor({ action: 'unban' }, 'Разблокирован'));
  on('moderator', () => toAuthor({ action: 'moderator' }, `${message.author} теперь модератор школы`));
  on('unmoderator', () => toAuthor({ action: 'unmoderator' }, 'Больше не модератор'));
}

/* ─────────────────────────── Жалобы ─────────────────────────── */

async function openReports(onClose) {
  const body = pushScreen('Жалобы');
  reopenOnRefresh(body, () => openReports(onClose));
  const draw = async () => {
    body.innerHTML = skeleton(3);
    let items;
    try {
      items = (await api('/api/chat/reports')).data.items;
    } catch (error) {
      body.innerHTML = errorCard(error);
      return;
    }
    if (!items.length) {
      body.innerHTML = '<div class="empty big">Жалоб нет 🎉</div>';
      return;
    }
    body.innerHTML = items.map((item, index) => `
      <div class="card chat-report">
        <div class="muted small">${esc(item.room.title)} · жалоб: ${item.reports}${item.hidden ? ' · скрыто' : ''}</div>
        <div class="chat-author">${authorLabel(item)}</div>
        <div class="chat-text">${esc(item.text ?? '')}</div>
        <button class="btn outline" data-jump-report="${index}">Перейти к сообщению</button>
        <div class="chat-report-actions">
          <button class="btn outline" data-r="${index}" data-a="dismiss">Всё в порядке</button>
          <button class="btn outline" data-r="${index}" data-a="hide">Скрыть</button>
          <button class="btn outline danger" data-r="${index}" data-a="delete">Удалить</button>
        </div>
      </div>`).join('');
    body.querySelectorAll('[data-jump-report]').forEach(button => button.addEventListener('click', () => {
      const item = items[Number(button.dataset.jumpReport)];
      openRoom(item.room, draw, item.id);
    }));
    body.querySelectorAll('[data-r]').forEach((button) => button.addEventListener('click', async () => {
      const item = items[Number(button.dataset.r)];
      if (await act(`/api/chat/messages/${item.id}/moderate`, { action: button.dataset.a })) draw();
    }));
  };
  draw();
  const watch = setInterval(() => {
    if (!body.isConnected) {
      clearInterval(watch);
      onClose?.();
    }
  }, 500);
}

/* ─────────────────────────── Новый опрос ─────────────────────────── */

function openPollEditor(room, onSent) {
  const sheet = openSheet('Опрос', `
    <input class="field" id="pollQuestion" maxlength="300" placeholder="Вопрос">
    <div id="pollOptions"></div>
    <button class="link" id="pollAdd">+ Вариант</button>
    <button class="btn" id="pollSend">Создать опрос</button>`);
  const options = sheet.querySelector('#pollOptions');
  const addOption = () => {
    if (options.children.length >= 6) return;
    const input = document.createElement('input');
    input.className = 'field';
    input.maxLength = 80;
    input.placeholder = `Вариант ${options.children.length + 1}`;
    options.appendChild(input);
    sheet.querySelector('#pollAdd').hidden = options.children.length >= 6;
  };
  addOption();
  addOption();
  sheet.querySelector('#pollAdd').addEventListener('click', addOption);
  sheet.querySelector('#pollSend').addEventListener('click', async () => {
    const question = sheet.querySelector('#pollQuestion').value.trim();
    const values = [...options.querySelectorAll('input')].map((input) => input.value.trim()).filter(Boolean);
    if (!question || values.length < 2) {
      notify('error');
      banner(!question ? 'Напиши вопрос' : 'Нужно хотя бы два варианта', true);
      return;
    }
    const data = await act(`/api/chat/rooms/${encodeURIComponent(room.id)}/messages`,
      { text: question, poll: { options: values } });
    if (data) {
      closeSheet();
      notify('success');
      onSent(data);
    }
  });
}
