// «Сообщения»: школьная почта карточками и объявления под колокольчиком.
'use strict';

import {
  $, api, attachmentsHtml, banner, errorCard, esc, escRich, shortDate, skeleton, state,
} from './core.js';
import { notify, select } from './haptics.js';
import { ICONS } from './icons.js';
import { popScreen, pushScreen } from './screens.js';
import { closeSheet, openSheet } from './sheet.js';

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

/* ─────────────────────────── Новое письмо ─────────────────────────── */

let recipients = null;

export async function openCompose() {
  const chosen = new Map();
  const body = pushScreen('Новое письмо');
  body.innerHTML = `
    <div class="card compose">
      <div class="detail-label">Кому</div>
      <button class="to-field" id="composeTo"><span class="muted">Выбрать получателя</span></button>
      <div class="detail-label">Тема</div>
      <input class="field" id="composeSubject" maxlength="200" placeholder="Тема письма">
      <div class="detail-label">Сообщение</div>
      <textarea class="field" id="composeText" rows="8" maxlength="10000" placeholder="Текст письма"></textarea>
      <button class="btn" id="composeSend">${ICONS.send} Отправить</button>
    </div>`;

  const renderTo = () => {
    $('composeTo').innerHTML = chosen.size
      ? [...chosen.values()].map((r) => `<span class="to-chip">${esc(r.name)}</span>`).join('')
      : '<span class="muted">Выбрать получателя</span>';
  };

  $('composeTo').addEventListener('click', async () => {
    const sheet = openSheet('Получатели', skeleton(5));
    try {
      if (!recipients) recipients = (await api('/api/mail/recipients')).data.items;
    } catch (error) {
      sheet.innerHTML = errorCard(error);
      return;
    }
    const draw = (query = '') => {
      const needle = query.trim().toLowerCase();
      const list = recipients.filter((r) => !needle || `${r.name} ${r.organization}`.toLowerCase().includes(needle));
      $('recipientList').innerHTML = list
        .map((r) => `<button class="recipient ${chosen.has(r.id) ? 'on' : ''}" data-id="${esc(r.id)}">
            <span class="avatar small" style="background:${avatarColor(r.name)}">${esc(r.name.trim()[0] || '?')}</span>
            <span class="recipient-main"><span>${esc(r.name)}</span><span class="muted small">${esc(r.organization)}</span></span>
            <span class="check">${chosen.has(r.id) ? '✓' : ''}</span>
          </button>`)
        .join('') || '<div class="empty">Никого не нашлось</div>';
      $('recipientList').querySelectorAll('[data-id]').forEach((row) =>
        row.addEventListener('click', () => {
          select();
          const recipient = recipients.find((r) => String(r.id) === row.dataset.id);
          if (chosen.has(recipient.id)) chosen.delete(recipient.id);
          else chosen.set(recipient.id, recipient);
          draw($('recipientSearch').value);
          renderTo();
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
  });

  $('composeSend').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const subject = $('composeSubject').value.trim();
    const text = $('composeText').value.trim();
    if (!chosen.size || !subject) {
      notify('error');
      banner(!chosen.size ? 'Выберите получателя' : 'Укажите тему письма', true);
      return;
    }
    button.disabled = true;
    button.classList.add('loading');
    try {
      await api('/api/mail/send', {
        method: 'POST',
        body: JSON.stringify({ to: [...chosen.keys()], subject, text }),
      });
      notify('success');
      popScreen();
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
