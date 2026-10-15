// «Сообщения»: школьная почта карточками и объявления под колокольчиком.
'use strict';

import {
  $, api, attachmentsHtml, banner, errorCard, esc, escRich, observedTime, shortDate, showFailure, showLoading, skeleton, state, stillMotion,
} from './core.js';
import { notify, select } from './haptics.js';
import { ICONS } from './icons.js';
import { popScreen, pushScreen } from './screens.js';
import { closeSheet, openSheet } from './sheet.js';
import { setMailBadge } from './tabs.js';

const AVATAR_COLORS = ['#a7b83a', '#4a9fd8', '#d8844a', '#9a6ad8', '#d84a6a', '#3ab89a'];

function avatarColor(name) {
  let hash = 0;
  for (const char of String(name)) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

// Новый дизайн: во вкладке два раздела — письма и объявления школы.
let mode = 'mail';
// Поиск и фильтры писем: всё на месте, по уже загруженному списку.
let query = '';
let filter = 'all'; // all | unread | read
let newestFirst = true;

export async function loadMail(force = false) {
  const container = $('mailContent');
  if (mode !== 'chat') delete container.dataset.chatView;
  // Новое письмо, поиск и фильтры — только в разделе писем.
  $('composeBtn').hidden = mode !== 'mail';
  $('mailTools').hidden = mode !== 'mail';
  if (mode === 'ann') {
    await renderAnnouncements(container, force);
    return;
  }
  if (mode === 'chat') {
    const { renderChats } = await import('./chat.js');
    await renderChats(container, force);
    return;
  }
  if (!state.mail || force) {
    showLoading(container, skeleton());
    try {
      const { data } = await api(`/api/mail${force ? '?refresh=1' : ''}`, {
        onFresh: (fresh) => { state.mail = fresh.data; },
      });
      state.mail = data;
    } catch (error) {
      showFailure(container, error, errorCard(error, 'mailRetry'));
      $('mailRetry')?.addEventListener('click', () => loadMail(true));
      return;
    }
  }
  render();
}

// Письма, которые уже были в списке: новое въезжает сверху, остальные
// плавно сдвигаются вниз. При первом показе ничего не въезжает.
let shownIds = null;

function render() {
  const messages = state.mail.messages || [];
  // Непрочитанные — числом у значка «Почты» в нижней панели.
  setMailBadge('mail', messages.filter((message) => message.read === false).length);
  const container = $('mailContent');
  // Чаты рисуются в этом же списке и могли оставить «тихий» режим.
  container.classList.remove('quiet');
  $('mailFilters').querySelectorAll('[data-filter]').forEach((chip) =>
    chip.classList.toggle('active', chip.dataset.filter === filter));
  $('mailSort').textContent = newestFirst ? '↓ Новые' : '↑ Старые';
  if (!messages.length) {
    container.innerHTML = '<div class="empty big">Писем нет</div>';
    return;
  }
  const visible = visibleMessages(messages);
  if (!visible.length) {
    const empty = query.trim() ? 'Ничего не найдено'
      : filter === 'unread' ? 'Непрочитанных писем нет' : 'Прочитанных писем нет';
    container.innerHTML = `<div class="empty big">${empty}</div>`;
    return;
  }
  const before = new Map([...container.querySelectorAll('[data-mail]')]
    .map((element) => [element.dataset.mail, element.getBoundingClientRect().top]));
  container.innerHTML = visible.map(card).join('');
  container.querySelectorAll('[data-mail]').forEach((element) =>
    element.addEventListener('click', () => openMessage(element.dataset.mail))
  );
  const still = stillMotion();
  if (shownIds && !still) {
    let arrived = 0;
    container.querySelectorAll('[data-mail]').forEach((element) => {
      const id = element.dataset.mail;
      if (!shownIds.has(id)) {
        element.classList.add('arrive');
        element.style.animationDelay = `${arrived++ * 70}ms`;
      } else if (before.has(id)) {
        const shift = before.get(id) - element.getBoundingClientRect().top;
        if (shift) element.animate([{ transform: `translateY(${shift}px)` }, { transform: 'none' }], { duration: 380, easing: 'cubic-bezier(.2, .8, .3, 1)' });
      }
    });
  }
  shownIds = new Set([...(shownIds || []), ...messages.map((message) => String(message.id))]);
}

/** Письмо прочитано: в списке оно плавно теряет жирный шрифт и точку, без
 *  перерисовки. Под фильтром «Непрочитанные» оно уходит — там перерисовка. */
function markRead(message) {
  message.read = true;
  const element = [...$('mailContent').querySelectorAll('[data-mail]')].find((item) => item.dataset.mail === String(message.id));
  if (!element || filter === 'unread' || query.trim()) {
    render();
    return;
  }
  setMailBadge('mail', (state.mail.messages || []).filter((item) => item.read === false).length);
  element.classList.remove('unread');
  const dot = element.querySelector('.unread-dot');
  if (dot) {
    dot.classList.add('gone');
    setTimeout(() => dot.remove(), 400);
  }
}

function visibleMessages(messages) {
  const needle = query.trim().toLowerCase();
  const list = messages.filter((message) =>
    (filter === 'all' || (filter === 'unread' ? message.read === false : message.read !== false))
    && (!needle || `${message.subject || ''} ${message.author || ''}`.toLowerCase().includes(needle)));
  // Сервер отдаёт новые сверху; сортируем сами, чтобы не зависеть от этого.
  const time = (message) => Date.parse(message.sent || '') || 0;
  return list.sort((a, b) => (newestFirst ? time(b) - time(a) : time(a) - time(b)));
}

function card(message) {
  const author = message.author || 'Без отправителя';
  return `<div class="card mail-card${message.read === false ? ' unread' : ''}" data-mail="${esc(message.id)}">
      <span class="avatar" style="background:${avatarColor(author)}">${esc(author.trim()[0] || '?')}</span>
      <div class="mail-main">
        <div class="mail-author">${message.read ? '' : '<span class="unread-dot"></span>'}${esc(author)}</div>
        <div class="mail-subject">${esc(message.subject)}</div>
      </div>
      <span class="mail-date">${message.sent ? esc(shortDate(message.sent.slice(0, 10))) : ''}</span>
    </div>`;
}

async function openMessage(id) {
  const body = pushScreen('Письмо');
  body.innerHTML = skeleton(4);
  try {
    const { data } = await api(`/api/mail/${encodeURIComponent(id)}`);
    body.innerHTML = `
      <div class="detail mail-detail">
        <div class="mail-envelope">
          <div class="detail-label">Тема:</div><h2>${esc(data.subject || 'Без темы')}</h2>
          <dl class="mail-meta"><dt>От кого</dt><dd>${esc(data.author || 'Без отправителя')}</dd>
          ${data.sent ? `<dt>Дата</dt><dd>${esc(observedTime(data.sent) || shortDate(data.sent.slice(0, 10)))}</dd>` : ''}</dl>
        </div>
        <div class="detail-text mail-message-text">${escRich(data.text) || '<span class="muted">Пустое письмо</span>'}</div>
        ${attachmentsHtml(data.attachments)}
      </div>`;
    // Письмо прочитано — отметка в списке без перезагрузки.
    const message = (state.mail?.messages || []).find((item) => String(item.id) === String(id));
    if (message && !message.read) markRead(message);
  } catch (error) {
    body.innerHTML = errorCard(error);
  }
}

export async function openAnnouncements() {
  await renderAnnouncements(pushScreen('Объявления'));
}

async function renderAnnouncements(body, force = false) {
  body.innerHTML = skeleton(4);
  try {
    const { data } = await api(`/api/announcements${force ? '?refresh=1' : ''}`);
    if (body.id === 'mailContent' && mode !== 'ann') return;
    const items = data.items || [];
    if (!items.length) {
      body.innerHTML = '<div class="empty big">Объявлений нет</div>';
      return;
    }
    body.innerHTML = items
      .map(
        (item, index) => `<div class="card ann-card" data-ann="${index}">
            <div class="ann-title">${esc(item.title)}</div>
            <div class="ann-text">${escRich(item.text)}</div>
            ${attachmentsHtml(item.attachments)}
            <div class="ann-foot"><span>${esc(item.author)}</span><span>${item.posted ? esc(shortDate(item.posted.slice(0, 10))) : ''}</span></div>
          </div>`
      )
      .join('');
    // Длинный текст свёрнут, по нажатию раскрывается целиком.
    body.querySelectorAll('[data-ann]').forEach((element) =>
      element.addEventListener('click', (event) => {
        if (event.target.closest('a')) return;
        element.classList.toggle('open');
      })
    );
  } catch (error) {
    body.innerHTML = errorCard(error);
  }
}

export function selectChats() {
  mode = 'chat';
  const seg = $('mailSeg');
  seg.querySelectorAll('[data-mail-mode]').forEach((item) =>
    item.classList.toggle('on', item.dataset.mailMode === 'chat'));
  seg.style.setProperty('--i', 1);
}

export function initMail() {
  const seg = $('mailSeg');
  import('./chat-live.js').then(({ initChatLive }) => initChatLive());
  const refreshMailBadge = async () => {
    if (document.hidden) return;
    try {
      const { data } = await api('/api/mail');
      setMailBadge('mail', (data.messages || []).filter(message => message.read === false).length);
    } catch { /* сеть вернётся — обновим */ }
  };
  refreshMailBadge();
  setInterval(refreshMailBadge, 60000);
  seg.addEventListener('click', (event) => {
    const button = event.target.closest('[data-mail-mode]');
    if (!button || button.dataset.mailMode === mode) return;
    select();
    mode = button.dataset.mailMode;
    seg.querySelectorAll('[data-mail-mode]').forEach((item) => item.classList.toggle('on', item === button));
    seg.style.setProperty('--i', ['mail', 'chat', 'ann'].indexOf(mode));
    loadMail();
  });

  $('mailSearch').addEventListener('input', (event) => {
    query = event.target.value;
    if (state.mail) render();
  });
  $('mailFilters').addEventListener('click', (event) => {
    const chip = event.target.closest('[data-filter]');
    if (!chip || chip.dataset.filter === filter) return;
    select();
    filter = chip.dataset.filter;
    if (state.mail) render();
  });
  $('mailSort').addEventListener('click', () => {
    select();
    newestFirst = !newestFirst;
    if (state.mail) render();
  });
}

/* ─────────────────────────── Новое письмо ─────────────────────────── */

let recipients = null;

/** Адресная книга школы: загружается один раз за сеанс. */
export async function loadRecipients() {
  if (!recipients) recipients = (await api('/api/mail/recipients')).data.items;
  return recipients;
}

// Те же пределы, что на сервере (api.py): лишнее отсекаем сразу, без загрузки.
const MAX_FILES = 5;
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_TOTAL_BYTES = 15 * 1024 * 1024;

function fileSize(bytes) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} МБ` : `${Math.max(1, Math.round(bytes / 1024))} КБ`;
}

/** Вложения к письму работают только в мобильном API. */
async function canAttach() {
  try {
    if (!state.profile) state.profile = (await api('/api/profile')).data;
    return state.profile.api_mode === 'mobile';
  } catch {
    return false;
  }
}

/** Новое письмо. `draft` — готовое из черновика помощника: получатели
 *  (`to`), тема и текст; `onSent` — сообщить, что письмо ушло. */
export async function openCompose(draft = {}) {
  const chosen = new Map((draft.to || []).map((recipient) => [recipient.id, recipient]));
  const files = [];
  const attach = await canAttach();
  const body = pushScreen('Новое письмо');
  body.innerHTML = `
    <div class="card compose">
      <div class="detail-label">Кому</div>
      <button class="to-field" id="composeTo"><span class="muted">Выбрать получателя</span></button>
      <div class="detail-label">Тема</div>
      <input class="field" id="composeSubject" maxlength="200" placeholder="Тема письма" value="${esc(draft.subject || '')}">
      <div class="detail-label">Сообщение</div>
      <textarea class="field" id="composeText" rows="8" maxlength="10000" placeholder="Текст письма">${esc(draft.text || '')}</textarea>
      <label class="switch-row compose-notify"><span>Уведомить о прочтении</span><input type="checkbox" class="switch" id="composeNotify"></label>
      ${attach ? `<div class="detail-label">Вложения</div>
        <div id="composeFiles" class="compose-files"></div>
        <label class="btn secondary attach-btn">📎 Прикрепить файл
          <input type="file" id="composeFile" multiple hidden></label>` : ''}
      <button class="btn" id="composeSend">${ICONS.send} Отправить</button>
    </div>`;

  const renderTo = () => {
    $('composeTo').innerHTML = chosen.size
      ? [...chosen.values()].map((r) => `<span class="to-chip">${esc(r.name)}</span>`).join('')
      : '<span class="muted">Выбрать получателя</span>';
  };
  renderTo();

  const renderFiles = () => {
    const list = $('composeFiles');
    if (!list) return;
    list.innerHTML = files
      .map((file, index) => `<div class="compose-file"><span class="compose-file-name">${esc(file.name)}</span>
          <span class="muted small">${fileSize(file.size)}</span>
          <button class="compose-file-x" data-drop="${index}" aria-label="Убрать файл">✕</button></div>`)
      .join('');
    list.querySelectorAll('[data-drop]').forEach((button) =>
      button.addEventListener('click', () => {
        files.splice(Number(button.dataset.drop), 1);
        renderFiles();
      })
    );
  };

  $('composeFile')?.addEventListener('change', (event) => {
    for (const file of event.target.files) {
      const total = files.reduce((sum, item) => sum + item.size, 0) + file.size;
      const problem = files.length >= MAX_FILES ? `Не больше ${MAX_FILES} файлов`
        : file.size === 0 ? `«${file.name}» пустой`
        : file.size > MAX_FILE_BYTES ? `«${file.name}» больше ${MAX_FILE_BYTES / 1024 / 1024} МБ`
        : total > MAX_TOTAL_BYTES ? `Файлы больше ${MAX_TOTAL_BYTES / 1024 / 1024} МБ в сумме` : '';
      if (problem) {
        notify('error');
        banner(problem, true);
        continue;
      }
      files.push(file);
    }
    event.target.value = '';
    renderFiles();
  });

  $('composeTo').addEventListener('click', () => chooseRecipients(chosen, renderTo));

  $('composeSend').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const subject = $('composeSubject').value.trim();
    const text = $('composeText').value.trim();
    const notifyRead = $('composeNotify').checked;
    if (!chosen.size || !subject) {
      notify('error');
      banner(!chosen.size ? 'Выберите получателя' : 'Укажите тему письма', true);
      return;
    }
    button.disabled = true;
    button.classList.add('loading');
    try {
      let payload;
      if (files.length) {
        // С файлами — multipart; без них остаётся прежний JSON.
        payload = new FormData();
        [...chosen.keys()].forEach((id) => payload.append('to', id));
        payload.append('subject', subject);
        payload.append('text', text);
        payload.append('notify', String(notifyRead));
        files.forEach((file) => payload.append('file', file, file.name));
      } else {
        payload = JSON.stringify({ to: [...chosen.keys()], subject, text, notify: notifyRead });
      }
      await api('/api/mail/send', { method: 'POST', body: payload });
      notify('success');
      popScreen();
      draft.onSent?.([...chosen.values()]);
      banner('Письмо отправлено ✓');
      setTimeout(() => banner(''), 3000);
    } catch (error) {
      notify('error');
      banner(error.message, true);
      button.disabled = false;
      button.classList.remove('loading');
    }
  });
}

/** Шторка выбора получателей. `chosen` — Map id → получатель, меняется на месте. */
export async function chooseRecipients(chosen, onChange) {
  const sheet = openSheet('Получатели', skeleton(5));
  try {
    await loadRecipients();
  } catch (error) {
    sheet.innerHTML = errorCard(error);
    return;
  }
  const draw = (query = '') => {
    const needle = query.trim().toLowerCase();
    const list = recipients.filter((r) => !needle || `${r.name} ${r.organization}`.toLowerCase().includes(needle));
    const categories = ['Администрация', 'Учителя', 'Родители', 'Ученики', 'Другие'];
    $('recipientList').innerHTML = categories.map((category) => {
      const group = list.filter((r) => (r.category || 'Другие') === category).sort((a, b) => a.name.localeCompare(b.name, 'ru'));
      if (!group.length) return '';
      return `<details class="recipient-group"${needle ? ' open' : ''}><summary>${category}<span class="muted small">${group.length}</span></summary>` + group
      .map((r) => `<button class="recipient ${chosen.has(r.id) ? 'on' : ''}" data-id="${esc(r.id)}">
          <span class="avatar small" style="background:${avatarColor(r.name)}">${esc(r.name.trim()[0] || '?')}</span>
          <span class="recipient-main"><span>${esc(r.name)}</span><span class="muted small">${esc(r.organization)}</span></span>
          <span class="check">${chosen.has(r.id) ? '✓' : ''}</span>
        </button>`)
      .join('') + '</details>';
    }).join('') || '<div class="empty">Никого не нашлось</div>';
    $('recipientList').querySelectorAll('[data-id]').forEach((row) =>
      row.addEventListener('click', () => {
        select();
        const recipient = recipients.find((r) => String(r.id) === row.dataset.id);
        if (chosen.has(recipient.id)) chosen.delete(recipient.id);
        else chosen.set(recipient.id, recipient);
        const opened = [...$('recipientList').querySelectorAll('details[open]')].map((group) => group.querySelector('summary').textContent);
        draw($('recipientSearch').value);
        $('recipientList').querySelectorAll('details').forEach((group) => { group.open = opened.includes(group.querySelector('summary').textContent); });
        onChange();
      })
    );
  };
  sheet.innerHTML = `
    <input class="field" id="recipientSearch" type="search" placeholder="Поиск по имени или предмету">
    <div id="recipientList"></div>
    <button class="btn" id="recipientDone">Готово</button>`;
  $('recipientSearch').addEventListener('input', (event) => draw(event.target.value));
  $('recipientDone').addEventListener('click', closeSheet);
  draw();
}
