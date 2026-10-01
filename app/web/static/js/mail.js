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
      const { data } = await api(`/api/mail${force ? '?refresh=1' : ''}`, {
        onFresh: (fresh) => { state.mail = fresh.data; },
      });
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

export async function openCompose() {
  const chosen = new Map();
  const files = [];
  const attach = await canAttach();
  const body = pushScreen('Новое письмо');
  body.innerHTML = `
    <div class="card compose">
      <div class="detail-label">Кому</div>
      <button class="to-field" id="composeTo"><span class="muted">Выбрать получателя</span></button>
      <div class="detail-label">Тема</div>
      <input class="field" id="composeSubject" maxlength="200" placeholder="Тема письма">
      <div class="detail-label">Сообщение</div>
      <textarea class="field" id="composeText" rows="8" maxlength="10000" placeholder="Текст письма"></textarea>
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
      let payload;
      if (files.length) {
        // С файлами — multipart; без них остаётся прежний JSON.
        payload = new FormData();
        [...chosen.keys()].forEach((id) => payload.append('to', id));
        payload.append('subject', subject);
        payload.append('text', text);
        files.forEach((file) => payload.append('file', file, file.name));
      } else {
        payload = JSON.stringify({ to: [...chosen.keys()], subject, text });
      }
      await api('/api/mail/send', { method: 'POST', body: payload });
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
