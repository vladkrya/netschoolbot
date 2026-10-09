// ИИ-помощник: вопросы про приложение, оценки, задания и письма учителю.
//
// Сервер отвечает по данным из кэша приложения, поэтому перед первым
// вопросом подгружаем оценки, задания и неделю — иначе модели не о чем
// рассказывать. Переписка живёт, пока открыто приложение.
//
// Письмо помощник только предлагает: черновик приходит в чат карточкой, и
// отправляет его человек сам — выбрав получателя и подтвердив.
'use strict';

import { $, api, banner, esc, state } from './core.js';
import { notify, select, tap } from './haptics.js';
import { ICONS } from './icons.js';
import { chooseRecipients, loadRecipients } from './mail.js';
import { pushScreen } from './screens.js';

const SUGGESTIONS = [
  'Что задали на завтра?',
  'Сколько пятёрок нужно до «5» по алгебре?',
  'По каким предметам выходит тройка?',
  'Напиши письмо учителю, что я болею',
  'Как поставить PIN-код на вход?',
];

const FIXES = [
  ['Короче', 'Сделай письмо короче'],
  ['Официальнее', 'Сделай письмо официальнее'],
  ['Мягче', 'Сделай письмо мягче и вежливее'],
];

const LETTER = /```\s*письмо\s*\n([\s\S]*?)```/i;

let preloaded = null;

/** Оценки, задания и неделя в кэше сервера — из них помощник и отвечает. */
function preload() {
  preloaded ??= Promise.allSettled([
    api('/api/marks').then(({ data }) => { state.grades ??= data; }),
    api('/api/homework'),
    api('/api/diary?week=0&window=current'),
  ]);
  return preloaded;
}

/** Текст ответа: модели любят **жирный** — показываем его, остальное как есть. */
function rich(text) {
  return esc(text).replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
}

const plain = (text) => text.replace(/\*\*([^*\n]+)\*\*/g, '$1');

/** «Кому», «Тема» и текст из блока ```письмо. */
function parseLetter(block) {
  const lines = block.trim().split('\n');
  const letter = { to: '', subject: '', text: '' };
  while (lines.length && /^(кому|тема)\s*:/i.test(lines[0])) {
    const [key, ...rest] = lines.shift().split(':');
    letter[/^кому/i.test(key.trim()) ? 'to' : 'subject'] = rest.join(':').trim();
  }
  letter.text = lines.join('\n').trim();
  return letter;
}

/** Черновик обратно в текст реплики: правки руками видит и модель. */
function letterBlock(letter) {
  const head = [letter.to && `Кому: ${letter.to}`, `Тема: ${letter.subject}`].filter(Boolean).join('\n');
  return `\`\`\`письмо\n${head}\n\n${letter.text}\n\`\`\``;
}

const words = (text) => String(text).toLowerCase().replace(/ё/g, 'е').match(/[a-zа-я]{2,}/g) || [];

/** Получатель по «Кому» из черновика: совпадение фамилии, учителя — первыми. */
function guessRecipient(list, hint) {
  const surname = words(hint).find((word) => word.length > 2);
  if (!surname) return null;
  const found = list.filter((r) => words(r.name).includes(surname));
  const teachers = found.filter((r) => r.category === 'Учителя');
  const pool = teachers.length ? teachers : found;
  return pool.length === 1 ? pool[0] : null;
}

function copy(text) {
  navigator.clipboard?.writeText(text).then(
    () => { notify('success'); banner('Скопировано', false, 1500); },
    () => notify('error'),
  );
}

export function openAssistant() {
  state.assistant ??= { messages: [], left: null };
  const chat = state.assistant;
  const body = pushScreen('Помощник', 'ИИ · может ошибаться, проверяйте');
  body.classList.add('ai-body');
  body.innerHTML = `
    <div class="ai-log" id="aiLog"></div>
    <div class="ai-intro" id="aiIntro">
      <div class="ai-hello ai-stagger" style="--i:0">${ICONS.sparkles}<div>Спросите про оценки, задания, расписание или как что-то сделать в приложении. Могу написать письмо учителю.</div></div>
      <div class="chips ai-suggest">${SUGGESTIONS.map((text, index) => `<button class="chip ai-stagger" style="--i:${index + 1}" data-suggest="${esc(text)}">${esc(text)}</button>`).join('')}</div>
      <div class="muted small ai-stagger" style="--i:${SUGGESTIONS.length + 1}">Вопросы и оценки уходят в OpenRouter без имени ученика и школы. Решать домашку за вас помощник не будет — но объяснит, с чего начать.</div>
    </div>
    <form class="ai-input" id="aiForm">
      <textarea class="field" id="aiText" rows="1" maxlength="2000" placeholder="Ваш вопрос"></textarea>
      <button class="ai-send" id="aiSend" aria-label="Отправить">${ICONS.send}</button>
    </form>`;

  const log = $('aiLog');
  const input = $('aiText');
  const screen = body.closest('.screen');
  const scroll = () => requestAnimationFrame(() => screen?.scrollTo({ top: screen.scrollHeight, behavior: 'smooth' }));
  const syncIntro = () => { $('aiIntro').hidden = chat.messages.length > 0; };
  const syncSend = () => $('aiSend').classList.toggle('ready', Boolean(input.value.trim()) && !busy);

  /* ── Карточка письма ── */

  function letterCard(message, letter) {
    const card = document.createElement('div');
    card.className = 'ai-letter';
    const chosen = letter.chosen ??= new Map();

    const draw = () => {
      const to = [...chosen.values()].map((r) => r.name).join(', ');
      if (letter.editing) {
        card.innerHTML = `
          <input class="field" data-f="subject" maxlength="200" placeholder="Тема письма" value="${esc(letter.subject)}">
          <textarea class="field" data-f="text" rows="7" maxlength="10000">${esc(letter.text)}</textarea>
          <div class="ai-letter-actions ai-panel">
            <button class="chip active" data-act="save">Сохранить</button>
            <button class="chip" data-act="cancel-edit">Отмена</button>
          </div>`;
      } else {
        card.innerHTML = `
          ${to || letter.to ? `<div class="ai-letter-meta">Кому: ${esc(to || letter.to)}</div>` : ''}
          <div class="ai-letter-subject">${letter.subject ? `Тема: ${esc(letter.subject)}` : '<span class="muted">Без темы</span>'}</div>
          <div class="ai-letter-text">${esc(letter.text)}</div>
          ${footer(to)}`;
      }
      bind();
    };

    const footer = (to) => {
      switch (letter.status) {
        case 'sent':
          return `<div class="ai-letter-status sent"><span class="ai-check">✓</span> Отправлено${to ? `: ${esc(to)}` : ''}</div>`;
        case 'dropped':
          return `<div class="ai-letter-status">Не отправлено <button class="link" data-act="restore">вернуть</button></div>`;
        case 'confirm':
        case 'sending':
          return `<div class="ai-panel">
              <button class="to-field" data-act="recipients">${to
                ? [...chosen.values()].map((r) => `<span class="to-chip">${esc(r.name)}</span>`).join('')
                : '<span class="muted">Выбрать получателя</span>'}</button>
              <div class="ai-letter-actions">
                <button class="chip active" data-act="send" ${!to || letter.status === 'sending' ? 'disabled' : ''}>${letter.status === 'sending' ? 'Отправляю…' : 'Подтвердить отправку'}</button>
                <button class="chip" data-act="back">Отмена</button>
              </div>
            </div>`;
        case 'fix':
          return `<div class="ai-letter-actions ai-panel">
              ${FIXES.map(([label], index) => `<button class="chip" data-fix="${index}">${label}</button>`).join('')}
              <button class="chip" data-act="edit">Вручную</button>
              <button class="chip" data-act="back">Отмена</button>
            </div>
            <div class="muted small ai-panel">Или напишите ниже, что поменять.</div>`;
        default:
          return `<div class="ai-letter-actions">
              <button class="chip active" data-act="confirm">${ICONS.send} Отправить</button>
              <button class="chip" data-act="fix">Подправить</button>
              <button class="chip" data-act="drop">Не отправлять</button>
              <button class="chip ghost" data-act="copy" aria-label="Копировать">Копировать</button>
            </div>`;
      }
    };

    const set = (status) => { letter.status = status; select(); draw(); };

    const bind = () => {
      const on = (act, handler) => card.querySelector(`[data-act="${act}"]`)?.addEventListener('click', handler);
      on('confirm', async () => {
        set('confirm');
        if (chosen.size || !letter.to) return;
        try {
          const found = guessRecipient(await loadRecipients(), letter.to);
          if (found && letter.status === 'confirm' && !chosen.size) {
            chosen.set(found.id, found);
            draw();
          }
        } catch { /* выберут руками */ }
      });
      on('recipients', () => chooseRecipients(chosen, draw));
      on('back', () => set('draft'));
      on('fix', () => { set('fix'); input.focus({ preventScroll: true }); });
      on('drop', () => set('dropped'));
      on('restore', () => set('draft'));
      on('copy', () => copy(letter.subject ? `${letter.subject}\n\n${letter.text}` : letter.text));
      on('edit', () => { letter.editing = true; select(); draw(); });
      on('cancel-edit', () => { letter.editing = false; letter.status = 'draft'; draw(); });
      on('save', () => {
        letter.subject = card.querySelector('[data-f="subject"]').value.trim();
        letter.text = card.querySelector('[data-f="text"]').value.trim();
        letter.editing = false;
        letter.status = 'draft';
        message.content = message.content.replace(LETTER, letterBlock(letter));
        notify('success');
        draw();
      });
      on('send', async () => {
        if (!letter.subject) {
          notify('error');
          banner('У письма нет темы — поправьте его вручную', true);
          return;
        }
        set('sending');
        try {
          await api('/api/mail/send', {
            method: 'POST',
            body: JSON.stringify({ to: [...chosen.keys()], subject: letter.subject, text: letter.text, notify: false }),
          });
          letter.status = 'sent';
          notify('success');
          // Во «Входящих» новое письмо появится после обновления.
          state.mail = null;
          draw();
        } catch (error) {
          letter.status = 'confirm';
          notify('error');
          banner(error.message, true);
          draw();
        }
      });
      card.querySelectorAll('[data-fix]').forEach((button) =>
        button.addEventListener('click', () => {
          letter.status = 'draft';
          draw();
          send(FIXES[Number(button.dataset.fix)][1]);
        }));
    };

    draw();
    return card;
  }

  /* ── Реплики ── */

  function bubble(message, animate) {
    const el = document.createElement('div');
    el.className = `ai-msg ${message.role}${animate ? ' enter' : ''}`;
    if (message.role === 'user') {
      el.textContent = message.content;
      return el;
    }
    const match = message.content.match(LETTER);
    const before = match ? message.content.slice(0, match.index).trim() : message.content;
    const after = match ? message.content.slice(match.index + match[0].length).trim() : '';
    if (before) el.insertAdjacentHTML('beforeend', `<div class="ai-text">${rich(before)}</div>`);
    if (match) {
      message.letter ??= { ...parseLetter(match[1]), status: 'draft' };
      el.classList.add('has-letter');
      el.appendChild(letterCard(message, message.letter));
    }
    if (after) el.insertAdjacentHTML('beforeend', `<div class="ai-text">${rich(after)}</div>`);
    if (!match) {
      const button = document.createElement('button');
      button.className = 'ai-copy';
      button.textContent = 'Копировать';
      button.addEventListener('click', () => copy(plain(message.content)));
      el.appendChild(button);
    }
    return el;
  }

  log.replaceChildren(...chat.messages.map((message) => bubble(message, false)));
  syncIntro();
  preload();

  const autosize = () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  };
  input.addEventListener('input', () => { autosize(); syncSend(); });

  let busy = false;
  async function send(text) {
    text = text.trim();
    if (!text || busy) return;
    busy = true;
    tap();
    const question = { role: 'user', content: text };
    chat.messages.push(question);
    const asked = bubble(question, true);
    log.appendChild(asked);
    input.value = '';
    autosize();
    syncSend();
    syncIntro();
    const typing = document.createElement('div');
    typing.className = 'ai-msg assistant typing enter';
    typing.innerHTML = '<span></span><span></span><span></span>';
    log.appendChild(typing);
    scroll();
    $('aiSend').disabled = true;
    try {
      await preload();
      const { data } = await api('/api/assistant', {
        method: 'POST',
        body: JSON.stringify({ messages: chat.messages.slice(-12).map(({ role, content }) => ({ role, content })) }),
      });
      const reply = { role: 'assistant', content: data.reply };
      chat.messages.push(reply);
      chat.left = data.left;
      typing.replaceWith(bubble(reply, true));
      notify('success');
    } catch (error) {
      // Вопрос без ответа убираем: иначе он ушёл бы в следующий запрос.
      chat.messages.pop();
      typing.remove();
      asked.remove();
      input.value = text;
      autosize();
      syncIntro();
      notify('error');
      banner(error.message, true);
    } finally {
      busy = false;
      $('aiSend').disabled = false;
      syncSend();
      scroll();
    }
  }

  $('aiForm').addEventListener('submit', (event) => {
    event.preventDefault();
    send(input.value);
  });
  input.addEventListener('keydown', (event) => {
    // Enter отправляет на компьютере; на телефоне клавиатура сама решает.
    if (event.key === 'Enter' && !event.shiftKey && !matchMedia('(pointer: coarse)').matches) {
      event.preventDefault();
      send(input.value);
    }
  });
  body.querySelectorAll('[data-suggest]').forEach((button) =>
    button.addEventListener('click', () => send(button.dataset.suggest)));
  if (chat.messages.length) scroll();
}
