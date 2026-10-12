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
  const reports = overview.moderator ? `
    <button class="card chat-room chat-reports" id="chatReports">
      <span class="chat-room-icon">⚠️</span>
      <span class="chat-room-main"><span class="chat-room-title">Жалобы</span>
        <span class="chat-room-last">${overview.reports ? `Ждут решения: ${overview.reports}` : 'Новых нет'}</span></span>
      ${overview.reports ? `<span class="nav-badge chat-unread">${overview.reports}</span>` : ''}
    </button>` : '';
  const joinHint = overview.admin && !overview.joined
    ? '<button class="card chat-join-hint" id="chatJoinSelf">Вступить, чтобы писать и от своего имени ›</button>' : '';
  container.innerHTML = identity + reports + joinHint + overview.rooms.map((room, index) => `
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
  container.querySelector('#chatReports')?.addEventListener('click', () => openReports(() => renderChats(container, true)));
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
  const text = escRich(message.text).replace(/(^|\s)@([A-Za-zА-Яа-яЁё]{2,30})/g, '$1<b class="chat-mention">@$2</b>');
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
  const bell = document.createElement('button');
  bell.className = 'screen-back chat-bell';
  bell.setAttribute('aria-label', 'Уведомления о чате');
  screen.querySelector('.screen-head').appendChild(bell);
  const paintBell = (muted) => {
    bell.textContent = muted ? '🔕' : '🔔';
    bell.dataset.muted = muted ? '1' : '';
  };
  paintBell(room.muted);
  bell.addEventListener('click', async () => {
    const muted = !bell.dataset.muted;
    paintBell(muted);
    select();
    try {
      await api(`/api/chat/rooms/${encodeURIComponent(room.id)}/mute`, { method: 'POST', body: JSON.stringify({ muted }) });
      room.muted = muted;
      banner(muted ? 'Уведомления об упоминаниях выключены' : 'Уведомления об упоминаниях включены');
      setTimeout(() => banner(''), 2000);
    } catch (error) {
      paintBell(!muted);
      banner(error.message, true);
    }
  });
  body.innerHTML = `
    <button class="chat-pinned" hidden></button>
    <div class="chat-log"><div class="chat-older" hidden>Загрузка…</div><div class="chat-list"></div></div>
    <div class="chat-replying" hidden><span></span><button type="button" aria-label="Не отвечать">✕</button></div>
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
      moderator = data.moderator;
      admin = data.admin;
      paintBell(data.muted);
      showPinned(data.pinned);
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
      showPinned(data.pinned);
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
  const update = (message) => {
    messages.set(message.id, message);
    redraw();
  };
  pinnedBar.addEventListener('click', () => {
    list.querySelector(`[data-id="${pinnedBar.dataset.jump}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
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
      list.querySelector(`[data-id="${jump.dataset.jump}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      return;
    }
    if (event.target.closest('a')) return;
    const element = event.target.closest('[data-id]');
    if (!element) return;
    const message = messages.get(Number(element.dataset.id));
    if (message) openActions(message, { setReply, update, moderator, admin, room, mention });
  });

  const timer = setInterval(poll, POLL_MS);
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

function openActions(message, { setReply, update, moderator, admin, room, mention }) {
  tap();
  const alive = message.text != null;
  const canReact = !message.deleted;
  const author = !message.mine && !message.admin;
  const button = (act, label, extra = '') => `<button class="btn outline ${extra}" data-act="${act}">${label}</button>`;
  const sheet = openSheet('', `
    ${canReact ? `<div class="chat-react-row">${(overview?.reactions || ['👍', '❤️', '😂', '😮', '😢', '🔥'])
      .map((emoji) => `<button class="chat-react-pick ${message.my_reactions?.includes(emoji) ? 'on' : ''}"
        data-pick="${emoji}">${emoji}</button>`).join('')}</div>` : ''}
    <div class="chat-actions">
      ${alive ? button('reply', 'Ответить') : ''}
      ${!message.mine ? button('mention', `Упомянуть ${esc(message.admin ? 'admin' : message.author)}`) : ''}
      ${alive ? button('copy', 'Скопировать') : ''}
      ${author && alive ? button('report', 'Пожаловаться', 'danger') : ''}
      ${(message.mine || moderator) && !message.deleted ? button('delete', 'Удалить', 'danger') : ''}
      ${moderator && !message.deleted ? (message.hidden ? button('unhide', 'Показать снова') : button('hide', 'Скрыть')) : ''}
      ${moderator && alive ? (message.pinned ? button('unpin', 'Открепить') : button('pin', 'Закрепить')) : ''}
      ${moderator && author ? button('mute60', 'Запретить писать на 1 час') + button('mute1440', 'Запретить писать на сутки') : ''}
      ${admin && author ? button('ban', 'Заблокировать в чатах', 'danger') + button('unban', 'Разблокировать') : ''}
      ${admin && author && room.kind !== 'global' ? button('moderator', 'Сделать модератором школы') + button('unmoderator', 'Снять модератора') : ''}
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
        <div class="chat-report-actions">
          <button class="btn outline" data-r="${index}" data-a="dismiss">Всё в порядке</button>
          <button class="btn outline" data-r="${index}" data-a="hide">Скрыть</button>
          <button class="btn outline danger" data-r="${index}" data-a="delete">Удалить</button>
        </div>
      </div>`).join('');
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
