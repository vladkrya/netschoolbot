// «Д/З»: домашние задания карточками с поиском, как во вкладке приложения.
//
// Отметка «выполнено» хранится на сервере и общая с /dz в боте.
'use strict';

import {
  $, addDays, api, dayMonth, errorCard, esc, isoToday, observedTime, showFailure, showLoading, skeleton, state,
  WEEKDAYS_FULL, weekdayIndex,
} from './core.js';
import { ICONS } from './icons.js';
import { homeworkTimes, isAutoDone, isDoneItem, loadDone, openHomework, saveDone } from './details.js';
import { select, tap } from './haptics.js';
import { dueButtonHtml, loadDue, openDue } from './due.js';
import { openSearch } from './search.js';
import {
  bindStreaks, isTestWork, loadNotes, noteWhen, notesAsItems, openNoteEditor, setNoteDone, streaks, streaksHtml,
} from './notes.js';

let query = '';
// all — с сегодняшнего дня, todo — только невыполненные, past — прошедшие.
let filter = 'all';

export async function loadHomework(force = false) {
  dueChecked = false;
  const container = $('homeworkContent');
  const past = filter === 'past';
  const slot = past ? 'homeworkPast' : 'homework';
  if (!state[slot] || force) {
    showLoading(container, skeleton());
    try {
      const params = [past ? 'past=1' : '', force ? 'refresh=1' : ''].filter(Boolean).join('&');
      const [{ data }] = await Promise.all([
        api(`/api/homework${params ? `?${params}` : ''}`, {
          onFresh: (fresh) => { state[slot] = fresh.data; },
        }),
        loadDone(),
        loadNotes(force),
      ]);
      state[slot] = data;
    } catch (error) {
      showFailure(container, error, errorCard(error, 'hwRetry'));
      $('hwRetry')?.addEventListener('click', () => loadHomework(true));
      return;
    }
  } else if (!state.hwDone || !state.notes) {
    await Promise.all([state.hwDone ? null : loadDone(), loadNotes()]);
  }
  render();
}

function visibleItems() {
  const today = isoToday();
  const needle = query.trim().toLowerCase();
  const done = state.hwDone || new Set();
  const source = filter === 'past' ? state.homeworkPast : state.homework;
  // Свои задачи — вперемешку со школьными, после них в пределах дня.
  let items = [...source.items, ...notesAsItems()].sort((a, b) =>
    a.date.localeCompare(b.date) || Number(Boolean(a.own)) - Number(Boolean(b.own)));
  if (filter === 'past') {
    items = items.filter((item) => item.date < today).reverse();
  } else {
    // Прошедшее здесь не показываем: домашка нужна на «сейчас и вперёд».
    items = items.filter((item) => item.date >= today);
    if (filter === 'todo') items = items.filter((item) => !itemDone(item, done));
  }
  return items.filter((item) => !needle || `${item.subject} ${item.text}`.toLowerCase().includes(needle));
}

// Галочка рисуется штрихом, когда отметку ставят (app.css, .hw-check path).
const CHECK = '<svg viewBox="0 0 14 14" aria-hidden="true"><path d="M2.5 7.5l3 3 6-7"/></svg>';

const itemDone = (item, done) => (item.own ? item.done : isDoneItem(item, done));

/** Карточка своей задачи: та же форма, что у школьного задания, с пометкой «Своё». */
function ownCard(item, index) {
  return `<div class="card hw-card own ${item.done ? 'done' : ''}" data-hw="${index}">
      <div class="hw-top">
        <div class="hw-subject">${esc(item.subject)} <span class="hw-tag own">Своё</span></div>
        <button class="note-edit" data-edit="${index}" aria-label="Изменить задачу">${ICONS.pencil}</button>
        <button class="hw-check ${item.done ? 'on' : ''}" data-done="${index}" aria-pressed="${item.done}"
          aria-label="${item.done ? 'Снять отметку' : 'Отметить выполненным'}">${CHECK}</button>
      </div>
      <div class="hw-text"><span class="hw-strike">${esc(item.text)}</span></div>
      ${item.time ? `<div class="hw-observed note-when">${esc(noteWhen(item))}</div>` : ''}
      <div class="hw-foot"><span></span><span class="muted small">${esc(dayMonth(item.date))}</span></div>
    </div>`;
}

function subjectNames() {
  return [...new Set((state.homework?.items || []).map((item) => item.subject))].sort((a, b) => a.localeCompare(b, 'ru'));
}

function render() {
  const items = visibleItems();
  const done = state.hwDone || new Set();
  const titles = { all: 'Домашние задания', todo: 'Не сделано', past: 'Прошедшие задания' };
  $('hwCount').textContent = `${titles[filter]} (${items.length})`;
  $('hwFilters').querySelectorAll('[data-filter]').forEach((chip) =>
    chip.classList.toggle('active', chip.dataset.filter === filter));

  const container = $('homeworkContent');
  const top = filter === 'past' || query.trim() ? ''
    : streaksHtml(streaks(state.grades, state.homework?.items, done, state.notes || []))
      + '<button class="hw-add" data-add-note>＋ Своя задача</button>';
  // «Должен сдать» — сверху и только когда что-то висит (due.js).
  const due = filter === 'past' || query.trim() ? '' : `<div id="hwDue">${dueButtonHtml()}</div>`;
  // Поле ищет по загруженным неделям, всё остальное — «Поиск за год».
  const yearSearch = query.trim().length >= 2
    ? `<button class="btn outline hw-year-search" data-year-search>${ICONS.search} Искать «${esc(query.trim())}» за весь год</button>` : '';
  if (!items.length) {
    const empty = query.trim() ? 'Ничего не найдено' : filter === 'todo' ? 'Всё сделано 🎉' : 'Заданий нет 🎉';
    container.innerHTML = `${due}${top}<div class="empty big">${empty}</div>${yearSearch}`;
    bindTop(container);
    return;
  }

  const perDay = new Map();
  for (const item of items) perDay.set(item.date, (perDay.get(item.date) || 0) + 1);
  container.innerHTML = due + top + items
    .map((item, index) => {
      const isDone = itemDone(item, done);
      // Заголовок дня перед первым его заданием (виден только в новом дизайне).
      const heading = item.date !== items[index - 1]?.date
        ? `<div class="hw-day"><span>${esc(dayHeading(item.date))}</span><span class="hw-day-count">${perDay.get(item.date)}</span></div>`
        : '';
      if (item.own) return heading + ownCard(item, index);
      const test = isTestWork(item);
      return `${heading}<div class="card hw-card ${isDone ? 'done' : ''} ${test ? 'test' : ''}" data-hw="${index}">
          <div class="hw-top">
            <div class="hw-subject">${esc(item.subject)}${test ? ` <span class="hw-tag test">${esc(item.type || 'Проверочная')}${Number(item.weight) > 1 ? ` · вес ${esc(item.weight)}` : ''}</span>` : ''}</div>
            ${isAutoDone(item) ? `<span class="hw-check on auto" title="${item.auto_done ? 'Задание не предусмотрено' : 'Работа на уроке'} — отмечено само">✓</span>`
              : item.key ? `<button class="hw-check ${isDone ? 'on' : ''}" data-done="${index}" aria-pressed="${isDone}"
              aria-label="${isDone ? 'Снять отметку' : 'Отметить выполненным'}">${CHECK}</button>` : ''}
          </div>
          <div class="hw-text"><span class="hw-strike">${esc(item.text) || '—'}</span></div>
          ${homeworkTimes(item, observedTime).map((line) => `<div class="hw-observed">${esc(line)}</div>`).join('')}
          <div class="hw-foot">
            <span><span class="hw-badge">${item.classwork ? 'Урок' : 'Д/З'}</span>${item.attachments.length ? `<span class="hw-badge clip">${ICONS.clip}</span>` : ''}${item.comment ? `<span class="hw-badge clip" title="Есть комментарий к заданию">${ICONS.chat}</span>` : ''}</span>
            <span class="muted small">${esc(dayMonth(item.date))}</span>
          </div>
        </div>`;
    })
    .join('') + yearSearch;
  container.querySelectorAll('[data-hw]').forEach((card) =>
    card.addEventListener('click', async (event) => {
      const item = items[Number(card.dataset.hw)];
      const check = event.target.closest('[data-done]');
      if (item.own && event.target.closest('[data-edit]')) {
        const note = (state.notes || []).find((n) => n.id === item.id);
        if (note) openNoteEditor(subjectNames(), () => render(), { note });
        return;
      }
      // Свою задачу отмечает нажатие в любом месте — заливка тогда из центра.
      const box = check || (item.own ? card.querySelector('[data-done]') : null);
      if (box) fillFrom(box, check ? event : {});
      if (item.own) {
        // Своя задача не открывается отдельным экраном: нажатие — отметка.
        const next = !item.done;
        paintCard(card.querySelector('[data-done]'), next);
        if (!await setNoteDone(item, next)) paintCard(card.querySelector('[data-done]'), !next);
        else if (filter === 'todo') render();
        return;
      }
      if (check) toggleDone(item, check);
      else openHomework(item);
    })
  );
  bindTop(container);
}

function bindTop(container) {
  bindStreaks(container);
  container.querySelector('[data-add-note]')?.addEventListener('click', () => openNoteEditor(subjectNames(), () => render()));
  container.querySelector('[data-due]')?.addEventListener('click', () => openDue());
  if (container.querySelector('#hwDue')) refreshDue();
  container.querySelector('[data-year-search]')?.addEventListener('click', () => openSearch(query.trim()));
}

// Пересчёт раз в открытие вкладки и после отметок «сделано».
let dueChecked = false;
let dueLoading = null;
function refreshDue() {
  if (dueChecked || dueLoading) return;
  dueLoading = loadDue()
    .then(() => {
      dueChecked = true;
      const slot = $('hwDue');
      if (!slot) return;
      slot.innerHTML = dueButtonHtml();
      slot.querySelector('[data-due]')?.addEventListener('click', () => openDue());
    })
    .catch(() => {})
    .finally(() => { dueLoading = null; });
}

function dayHeading(iso) {
  const today = isoToday();
  const near = { [today]: 'Сегодня', [addDays(today, 1)]: 'Завтра', [addDays(today, -1)]: 'Вчера' }[iso];
  return `${near || WEEKDAYS_FULL[weekdayIndex(iso)]}, ${dayMonth(iso)}`;
}

/** Заливка галочки расходится от точки нажатия (app.css, .hw-check.ripple). */
function fillFrom(button, event) {
  const rect = button.getBoundingClientRect();
  button.style.setProperty('--x', `${event.clientX ? event.clientX - rect.left : rect.width / 2}px`);
  button.style.setProperty('--y', `${event.clientY ? event.clientY - rect.top : rect.height / 2}px`);
  button.classList.add('ripple');
}

/** Галочка на месте, без перерисовки списка: иначе карточки заново «въезжают». */
function paintCard(button, isDone) {
  button.classList.toggle('on', isDone);
  button.setAttribute('aria-pressed', String(isDone));
  button.setAttribute('aria-label', isDone ? 'Снять отметку' : 'Отметить выполненным');
  button.closest('.hw-card')?.classList.toggle('done', isDone);
}

function toggleDone(item, button) {
  // Сразу на экране, сервер — следом: галочка не должна ждать сети.
  // В «Не сделано» выполненное уходит из списка — тут перерисовка нужна.
  saveDone(item, !isDoneItem(item), (value) => (filter === 'todo' ? render() : paintCard(button, value)), 'list');
}

export function initHomework() {
  // Отметили на экране урока или задания — галочки в списке должны совпасть.
  window.addEventListener('netschool:hwdone', (event) => {
    const source = filter === 'past' ? state.homeworkPast : state.homework;
    dueChecked = false;
    if (event.detail?.source !== 'list' && source) render();
    else if ($('hwDue')) refreshDue();
  });
  $('hwSearch').addEventListener('input', (event) => {
    query = event.target.value;
    if (state.homework) render();
  });
  $('hwFilters').addEventListener('click', (event) => {
    const chip = event.target.closest('[data-filter]');
    if (!chip || chip.dataset.filter === filter) return;
    select();
    filter = chip.dataset.filter;
    loadHomework();
  });
}
