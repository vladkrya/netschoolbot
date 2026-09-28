// «Сообщения»: школьная почта карточками и объявления под колокольчиком.
'use strict';

import {
  $, api, attachmentsHtml, errorCard, esc, escRich, shortDate, skeleton, state,
} from './core.js';
import { pushScreen } from './screens.js';

const AVATAR_COLORS = ['#a7b83a', '#4a9fd8', '#d8844a', '#9a6ad8', '#d84a6a', '#3ab89a'];

function avatarColor(name) {
  let hash = 0;
  for (const char of String(name)) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

export async function loadMail(force = false) {
  const container = $('mailContent');
  if (!state.mail || force) {
    container.innerHTML = skeleton();
    try {
      const { data } = await api(`/api/mail${force ? '?refresh=1' : ''}`);
      state.mail = data;
    } catch (error) {
      container.innerHTML = errorCard(error, 'mailRetry');
      $('mailRetry')?.addEventListener('click', () => loadMail(true));
      return;
    }
  }
  render();
}

function render() {
  const messages = state.mail.messages || [];
  const container = $('mailContent');
  if (!messages.length) {
    container.innerHTML = '<div class="empty big">Писем нет</div>';
    return;
  }
  container.innerHTML = messages.map(card).join('');
  container.querySelectorAll('[data-mail]').forEach((element) =>
    element.addEventListener('click', () => openMessage(element.dataset.mail))
  );
}

function card(message) {
  const author = message.author || 'Без отправителя';
  return `<div class="card mail-card" data-mail="${esc(message.id)}">
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
      <div class="detail">
        <h2>${esc(data.subject || 'Без темы')}</h2>
        <div class="detail-row"><span class="detail-label">От кого</span><span>${esc(data.author)}</span></div>
        ${data.sent ? `<div class="detail-row"><span class="detail-label">Дата</span><span>${esc(shortDate(data.sent.slice(0, 10)))}</span></div>` : ''}
        <div class="detail-text">${escRich(data.text) || '<span class="muted">Пустое письмо</span>'}</div>
        ${attachmentsHtml(data.attachments)}
      </div>`;
    // Письмо прочитано — отметка в списке без перезагрузки.
    const message = (state.mail?.messages || []).find((item) => String(item.id) === String(id));
    if (message && !message.read) {
      message.read = true;
      render();
    }
  } catch (error) {
    body.innerHTML = errorCard(error);
  }
}

export async function openAnnouncements() {
  const body = pushScreen('Объявления');
  body.innerHTML = skeleton(4);
  try {
    const { data } = await api('/api/announcements');
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
