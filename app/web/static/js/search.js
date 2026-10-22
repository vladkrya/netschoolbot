// Поиск по домашним заданиям и почте за весь учебный год (/api/search).
// Поле во вкладке «Задания» ищет только по загруженным неделям — отсюда
// видно всё с 1 сентября.
'use strict';

import { api, dayMonth, esc, errorCard, observedTime, shortDate, skeleton } from './core.js';
import { openHomework } from './details.js';
import { openMessage } from './mail.js';
import { markScreen, pushScreen } from './screens.js';
import { tap } from './haptics.js';

export function openSearch(initial = '') {
  tap();
  const body = pushScreen('Поиск за год');
  markScreen(body, 'search', [initial], () => [body.querySelector('#yearSearch')?.value || '']);
  body.innerHTML = `<label class="search search-year"><input id="yearSearch" type="search" placeholder="Задание, тема, письмо или учитель"
      autocomplete="off" enterkeyhint="search" value="${esc(initial)}"></label>
    <div class="muted small search-note">Ищет задания и письма с 1 сентября. Первый поиск может занять до минуты.</div>
    <div id="yearResults"></div>`;
  const input = body.querySelector('#yearSearch');
  const results = body.querySelector('#yearResults');
  let timer = null;
  let version = 0;
  let retry = null;

  // again — повтор, пока сервер собирает год: поле и прежние находки не трогаем.
  const run = async (again = false) => {
    const query = input.value.trim();
    const mine = again ? version : ++version;
    clearTimeout(retry);
    if (query.length < 2) {
      results.innerHTML = '';
      return;
    }
    if (!again) results.innerHTML = skeleton(3);
    let data;
    try {
      ({ data } = await api(`/api/search?q=${encodeURIComponent(query)}`));
    } catch (error) {
      if (mine === version) results.innerHTML = errorCard(error);
      return;
    }
    if (mine !== version) return;
    const homework = data.homework || [];
    const mail = data.mail || [];
    const failed = new Set(data.failed || []);
    const pending = new Set(data.pending || []);
    // Год ещё собирается (первый поиск) — спросим снова, пока экран открыт.
    if (pending.size) retry = setTimeout(() => { if (body.isConnected && mine === version) run(true); }, 3000);
    results.innerHTML = `
      ${homework.length ? `<div class="drawer-sub">Задания · ${homework.length}${homework.length >= 50 ? '+' : ''}</div>
        ${homework.map((item, i) => `<button class="card search-hit" data-hw="${i}">
          <div class="sh-top"><b>${esc(item.subject)}</b><span class="muted small">${esc(dayMonth(item.date))}</span></div>
          <div class="sh-text">${esc(item.text || '—')}</div></button>`).join('')}` : ''}
      ${mail.length ? `<div class="drawer-sub">Письма · ${mail.length}</div>
        ${mail.map((message, i) => `<button class="card search-hit" data-mail="${i}">
          <div class="sh-top"><b>${esc(message.subject)}</b>
            <span class="muted small">${esc(observedTime(message.sent) || shortDate(String(message.sent).slice(0, 10)))}</span></div>
          <div class="sh-text muted small">${esc(message.author)}</div></button>`).join('')}` : ''}
      ${pending.size ? `<div class="card search-pending"><span class="spinner" aria-hidden="true"></span>
        Собираю ${pending.has('homework') ? 'задания' : 'письма'} за год — это один раз, до минуты. Результаты появятся сами.</div>` : ''}
      ${!homework.length && !mail.length && !pending.size ? '<div class="empty big">Ничего не нашлось</div>' : ''}
      ${failed.size ? `<div class="muted small search-note">${failed.has('mail') ? 'Почта' : 'Задания'} сейчас не загрузились — «Сетевой город» не ответил.</div>` : ''}`;
    results.querySelectorAll('[data-hw]').forEach((card) => card.addEventListener('click', () => {
      tap();
      openHomework(homework[Number(card.dataset.hw)]);
    }));
    results.querySelectorAll('[data-mail]').forEach((card) => card.addEventListener('click', () => {
      tap();
      openMessage(mail[Number(card.dataset.mail)].id);
    }));
  };

  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(run, 450);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    clearTimeout(timer);
    input.blur();
    run();
  });
  if (initial) run();
  else setTimeout(() => input.focus(), 300);
}
