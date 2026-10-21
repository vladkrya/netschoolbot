// История оценок: что поставили, исправили и удалили.
'use strict';

import { api, banner, demo, errorCard, esc } from './core.js';
import { ICONS } from './icons.js';
import { markBox, openLesson } from './details.js';
import { pushScreen } from './screens.js';
import { notify, select, tap } from './haptics.js';
import { closeDrawer } from './profile.js';

const HISTORY_KINDS = { new: 'Поставлена', changed: 'Исправлена', deleted: 'Удалена' };
const HISTORY_FILTERS = [['all', 'Все'], ['new', 'Поставленные'], ['changed', 'Исправленные'], ['deleted', 'Удалённые']];
const dayFormat = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' });
const clockFormat = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });

/** «Сегодня», «Вчера» или «10 октября» — заголовок группы по дню, когда
 *  изменение заметили. */
function historyDay(date) {
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((today - day) / 86400000);
  return diff === 0 ? 'Сегодня' : diff === 1 ? 'Вчера' : dayFormat.format(date);
}

function historyItem(item, index, unseen) {
  const seen = new Date(item.seen);
  const date = item.date.split('-').reverse().slice(0, 2).join('.');
  const marks = item.kind === 'changed'
    ? `${markBox(item.old, 'old')}<span class="hi-arrow">${ICONS.right}</span>${markBox(item.new, 'fresh')}`
    : markBox(item.mark, item.kind === 'deleted' ? 'old' : 'fresh');
  // «Новая» — только пока запись не видели; потом остаётся «Поставлена» и время.
  const kind = unseen && item.kind === 'new' ? 'Новая' : HISTORY_KINDS[item.kind] || '';
  return `<div class="history-item hi-${esc(item.kind)}${unseen ? ' hi-unseen' : ''}" data-history="${esc(item.id)}" role="button" tabindex="0" style="--d:${Math.min(index, 12) * 35}ms">
      <div class="hi-main">
        <div class="hi-subject">${esc(item.subject)}</div>
        <div class="hi-title">${esc(item.title || 'Оценка')} · за ${esc(date)}</div>
        <div class="hi-meta"><span class="hi-kind">${kind}</span>${esc(clockFormat.format(seen))}</div>
      </div>
      <div class="hi-marks">${marks}</div>
    </div>`;
}

// Первая страница истории — и на устройстве: без сети её видно, даже если
// service worker нет (Telegram) или он её ещё не сохранил.
const HISTORY_KEY = 'ns-history';

// Последняя запись, которую уже показывали: всё, что старше, — не новое.
const HISTORY_SEEN_KEY = 'ns-history-seen';

function historySeenId() {
  try {
    const value = Number(localStorage.getItem(HISTORY_SEEN_KEY));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch { return null; }
}

function savedHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || 'null'); } catch { return null; }
}

/** История — заранее, после загрузки вкладок: без сети она откроется. */
export async function prefetchHistory() {
  if (demo) return;
  try {
    const { data } = await api('/api/history');
    localStorage.setItem(HISTORY_KEY, JSON.stringify(data));
  } catch { /* откроется с сетью */ }
}

/** Урок, на котором поставили оценку, — тем же экраном, что из «Дня».
 *  «Назад» возвращает в историю: экран урока ложится поверх неё. */
async function openHistoryLesson(item, card) {
  if (card.classList.contains('loading')) return;
  tap();
  card.classList.add('loading');
  try {
    const { dayData, ensureWeek } = await import('./day.js');
    await ensureWeek(item.date);
    // Пока грузилась неделя, историю могли закрыть.
    if (!card.isConnected) return;
    const lessons = (dayData(item.date)?.lessons || []).filter((lesson) => lesson.subject === item.subject);
    // Урок по номеру; номера нет (школа не указала) — первый урок предмета в тот день.
    const lesson = lessons.find((l) => item.lesson != null && l.number === item.lesson) || lessons[0];
    if (!lesson) {
      notify('error');
      banner('Этого урока в дневнике за тот день нет — возможно, его убрали из расписания', true, 3500);
      return;
    }
    openLesson(lesson, item.date);
  } catch (error) {
    notify('error');
    banner(error.message, true, 3500);
  } finally {
    card.classList.remove('loading');
  }
}

export async function openHistory() {
  closeDrawer();
  const screen = pushScreen('История оценок');
  screen.innerHTML = `<div class="history-skeleton">${'<div class="card"><div class="skeleton" style="width:50%"></div><div class="skeleton" style="width:75%"></div></div>'.repeat(5)}</div>`;
  const items = [];
  let filter = 'all';
  let more = true;
  let loading = false;
  // Отметку снимаем при следующем открытии, а не сразу: новые видно, пока
  // экран открыт. Отметки ещё не было — новыми считаем записи за сутки.
  const seenId = historySeenId();
  const dayAgo = Date.now() - 86400000;
  const unseen = (item) => (seenId == null ? new Date(item.seen) > dayAgo : item.id > seenId);

  const draw = () => {
    const shown = items.filter((item) => filter === 'all' || item.kind === filter);
    const count = (kind) => items.filter((item) => kind === 'all' || item.kind === kind).length;
    let lastDay = '';
    let index = 0;
    const list = shown.map((item) => {
      const day = historyDay(new Date(item.seen));
      const head = day !== lastDay ? `<div class="hi-day">${esc(day)}</div>` : '';
      lastDay = day;
      return head + historyItem(item, index++, unseen(item));
    }).join('');
    screen.innerHTML = `
      <div class="chips history-filters">${HISTORY_FILTERS.map(([key, label]) =>
        `<button class="chip ${key === filter ? 'active' : ''}" data-filter="${key}">${label}<span class="hi-count">${count(key)}</span></button>`).join('')}</div>
      <div class="history-list">${list || '<div class="empty muted">Таких изменений пока нет.</div>'}</div>
      ${more ? '<button class="btn outline hi-more" id="historyMore">Показать раньше</button>' : ''}`;
    screen.querySelectorAll('[data-filter]').forEach((chip) => chip.addEventListener('click', () => {
      if (chip.dataset.filter === filter) return;
      select();
      filter = chip.dataset.filter;
      draw();
    }));
    screen.querySelector('#historyMore')?.addEventListener('click', load);
    screen.querySelectorAll('[data-history]').forEach((card) => card.addEventListener('click', () => {
      const item = items.find((entry) => String(entry.id) === card.dataset.history);
      if (item) openHistoryLesson(item, card);
    }));
  };

  async function load() {
    if (loading) return;
    loading = true;
    const button = screen.querySelector('#historyMore');
    if (button) { button.disabled = true; button.textContent = 'Загружаем…'; }
    try {
      const before = items.length ? `?before=${items[items.length - 1].id}` : '';
      let data;
      try {
        ({ data } = await api(`/api/history${before}`));
        if (!before) {
          try { localStorage.setItem(HISTORY_KEY, JSON.stringify(data)); } catch { /* хранилище недоступно */ }
        }
      } catch (error) {
        const saved = !before && error.source === 'app' && savedHistory();
        if (!saved) throw error;
        data = saved;
      }
      items.push(...data.items);
      if (!demo && !before && data.items.length) {
        const top = Math.max(...data.items.map((item) => item.id));
        try { if (top > (seenId || 0)) localStorage.setItem(HISTORY_SEEN_KEY, String(top)); } catch { /* хранилище недоступно */ }
      }
      // Сервер отдаёт по 50: меньше — значит, старше ничего нет.
      more = data.items.length >= 50;
      if (!items.length) {
        screen.innerHTML = `<div class="empty history-empty">${ICONS.clock}<div>Пока пусто: изменения появятся после следующих проверок.</div></div>`;
        return;
      }
      draw();
    } catch (error) {
      if (!items.length) screen.innerHTML = errorCard(error);
      else banner(error.message, true);
    } finally {
      loading = false;
    }
  }
  await load();
}
