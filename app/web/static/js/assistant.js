// ИИ-помощник: вопросы про приложение, оценки, задания и письма учителю.
//
// Сервер отвечает по данным из кэша приложения, поэтому перед первым
// вопросом подгружаем оценки, задания и неделю — иначе модели не о чем
// рассказывать. Переписка живёт, пока открыто приложение.
'use strict';

import { $, api, banner, esc, state } from './core.js';
import { notify, tap } from './haptics.js';
import { ICONS } from './icons.js';
import { openCompose } from './mail.js';
import { pushScreen } from './screens.js';

const SUGGESTIONS = [
  'Что задали на завтра?',
  'Сколько пятёрок нужно до «5» по алгебре?',
  'По каким предметам выходит тройка?',
  'Напиши письмо учителю, что я болею',
  'Как поставить PIN-код на вход?',
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

function parseLetter(block) {
  const lines = block.trim().split('\n');
  let subject = '';
  if (/^тема\s*:/i.test(lines[0] || '')) subject = lines.shift().replace(/^тема\s*:\s*/i, '').trim();
  return { subject, text: lines.join('\n').trim() };
}

function copy(text) {
  navigator.clipboard?.writeText(text).then(
    () => { notify('success'); banner('Скопировано', false, 1500); },
    () => notify('error'),
  );
}

function bubble(message) {
  const el = document.createElement('div');
  el.className = `ai-msg ${message.role}`;
  if (message.role === 'user') {
    el.textContent = message.content;
    return el;
  }
  const match = message.content.match(LETTER);
  const before = match ? message.content.slice(0, match.index).trim() : message.content;
  const after = match ? message.content.slice(match.index + match[0].length).trim() : '';
  el.innerHTML = `${before ? `<div class="ai-text">${esc(before)}</div>` : ''}
    ${match ? '<div class="ai-letter"><div class="ai-letter-subject"></div><div class="ai-letter-text"></div><div class="ai-letter-actions"><button class="chip" data-act="mail">Открыть в почте</button><button class="chip" data-act="copy-letter">Копировать</button></div></div>' : ''}
    ${after ? `<div class="ai-text">${esc(after)}</div>` : ''}
    ${match ? '' : '<button class="ai-copy" data-act="copy">Копировать</button>'}`;
  if (match) {
    const letter = parseLetter(match[1]);
    el.querySelector('.ai-letter-subject').textContent = letter.subject ? `Тема: ${letter.subject}` : '';
    el.querySelector('.ai-letter-text').textContent = letter.text;
    el.querySelector('[data-act="mail"]').addEventListener('click', () => { tap(); openCompose(letter); });
    el.querySelector('[data-act="copy-letter"]').addEventListener('click', () =>
      copy(letter.subject ? `${letter.subject}\n\n${letter.text}` : letter.text));
  } else {
    el.querySelector('[data-act="copy"]').addEventListener('click', () => copy(message.content));
  }
  return el;
}

export function openAssistant() {
  state.assistant ??= { messages: [], left: null };
  const chat = state.assistant;
  const body = pushScreen('Помощник', 'ИИ · может ошибаться, проверяйте');
  body.classList.add('ai-body');
  body.innerHTML = `
    <div class="ai-log" id="aiLog"></div>
    <div class="ai-intro" id="aiIntro">
      <div class="ai-hello">${ICONS.sparkles}<div>Спросите про оценки, задания, расписание или как что-то сделать в приложении. Могу написать письмо учителю.</div></div>
      <div class="chips ai-suggest">${SUGGESTIONS.map((text) => `<button class="chip" data-suggest="${esc(text)}">${esc(text)}</button>`).join('')}</div>
      <div class="muted small">Вопросы и оценки уходят в OpenRouter без имени ученика и школы. Решать домашку за вас помощник не будет — но объяснит, с чего начать.</div>
    </div>
    <form class="ai-input" id="aiForm">
      <textarea class="field" id="aiText" rows="1" maxlength="2000" placeholder="Ваш вопрос"></textarea>
      <button class="ai-send" id="aiSend" aria-label="Отправить">${ICONS.send}</button>
    </form>`;

  const log = $('aiLog');
  const input = $('aiText');
  const scroll = () => body.closest('.screen')?.scrollTo({ top: 1e6, behavior: 'smooth' });
  const render = () => {
    log.replaceChildren(...chat.messages.map(bubble));
    $('aiIntro').hidden = chat.messages.length > 0;
  };
  render();
  preload();

  const autosize = () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  };
  input.addEventListener('input', autosize);

  let busy = false;
  const send = async (text) => {
    text = text.trim();
    if (!text || busy) return;
    busy = true;
    tap();
    chat.messages.push({ role: 'user', content: text });
    input.value = '';
    autosize();
    render();
    const typing = document.createElement('div');
    typing.className = 'ai-msg assistant typing';
    typing.innerHTML = '<span></span><span></span><span></span>';
    log.appendChild(typing);
    scroll();
    $('aiSend').disabled = true;
    try {
      await preload();
      const { data } = await api('/api/assistant', {
        method: 'POST',
        body: JSON.stringify({ messages: chat.messages.slice(-12) }),
      });
      chat.messages.push({ role: 'assistant', content: data.reply });
      chat.left = data.left;
      notify('success');
    } catch (error) {
      // Вопрос без ответа убираем: иначе он ушёл бы в следующий запрос.
      chat.messages.pop();
      input.value = text;
      notify('error');
      banner(error.message, true);
    } finally {
      busy = false;
      $('aiSend').disabled = false;
      render();
      scroll();
    }
  };

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
}
