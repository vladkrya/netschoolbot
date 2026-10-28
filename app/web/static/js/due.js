// «Должен сдать»: всё, что висит, одним списком — точки (работа не сдана в
// срок), двойки текущего периода, которые можно исправить, и задания
// последних двух недель, не отмеченные сделанными.
//
// Собирается из того, что приложение и так загружает: оценки года
// (/api/marks), прошедшие задания (/api/homework?past=1) и отметки «сделано».
'use strict';

import { addDays, api, DUTY_MARK, dayMonth, errorCard, esc, isoToday, markValue, plural, skeleton, state } from './core.js';
import { isAutoDone, isDoneItem, loadDone, markBox, openHomework, openMark } from './details.js';
import { markScreen, pushScreen, reopenOnRefresh } from './screens.js';
import { tap } from './haptics.js';
import { getPref, setPref } from './prefs.js';

// Задания старше этого уже никто не сдаёт — в списке только свежие.
const HOMEWORK_DAYS = 14;

/** С какого дня считаем несделанные задания: позже из двух — «Очистить» или день
 *  первой отметки. Раньше этого дня человек ещё не вёл отметки, и всё прошлое
 *  висело бы долгом. */
export function dueCutoff() {
  return [getPref('dueFrom') || '', state.hwDoneSince || ''].reduce((a, b) => (a > b ? a : b), '');
}

function sections(marksData, past, done) {
  const today = isoToday();
  const current = marksData.current_quarter;
  const debts = [];
  const twos = [];
  for (const subject of marksData.subjects || []) {
    for (const mark of subject.marks || []) {
      if (String(mark.mark || '').trim() === DUTY_MARK) debts.push({ subject: subject.subject, mark });
      else if (markValue(mark.mark) === 2 && mark.quarter === current) twos.push({ subject: subject.subject, mark });
    }
  }
  const since = addDays(today, -HOMEWORK_DAYS);
  // Галочки «сделано» ставят не все: без единой отметки неотмеченное — не долг,
  // иначе кнопка висела бы у всех всегда.
  const from = dueCutoff();
  const homework = done?.size ? (past?.items || []).filter((item) => item.date >= since && item.date >= from && item.date < today
    && !item.classwork && !isAutoDone(item) && item.key && !isDoneItem(item)) : [];
  const byDate = (a, b) => (b.mark?.date || b.date).localeCompare(a.mark?.date || a.date);
  return { debts: debts.sort(byDate), twos: twos.sort(byDate), homework: homework.sort(byDate) };
}

function markRow(entry, index, group) {
  const { subject, mark } = entry;
  return `<button class="due-row" data-${group}="${index}">
      ${markBox(mark.mark)}
      <span class="due-main"><b>${esc(subject)}</b>
        <span class="muted small">${esc(mark.type || mark.title || 'Работа')} · ${esc(dayMonth(mark.date))}</span></span>
    </button>`;
}

function homeworkRow(item, index) {
  return `<button class="due-row" data-hw="${index}">
      <span class="due-badge">Д/З</span>
      <span class="due-main"><b>${esc(item.subject)}</b>
        <span class="muted small due-text">${esc(item.text || '—')}</span>
        <span class="muted small">на ${esc(dayMonth(item.date))}</span></span>
    </button>`;
}

/** Всё, что висит; запросы те же, что у вкладок, и отвечают из кэша. */
export async function loadDue() {
  const [marks, homework] = await Promise.all([
    state.totals && !state.totals.archived ? { data: state.totals } : api('/api/marks'),
    state.homeworkPast ? { data: state.homeworkPast } : api('/api/homework?past=1'),
    loadDone(),
  ]);
  const found = sections(marks.data, homework.data, state.hwDone);
  state.due = found;
  return found;
}

const dueTotal = (found) => found.debts.length + found.twos.length + found.homework.length;

/** Отдельная кнопка над заданиями — только когда что-то висит. */
export function dueButtonHtml(found = state.due) {
  if (!found || !dueTotal(found)) return '';
  const parts = [
    found.debts.length && `${found.debts.length} ${plural(found.debts.length, 'точка', 'точки', 'точек')}`,
    found.twos.length && `${found.twos.length} ${plural(found.twos.length, 'двойка', 'двойки', 'двоек')}`,
    found.homework.length && `${found.homework.length} ${plural(found.homework.length, 'задание', 'задания', 'заданий')}`,
  ].filter(Boolean);
  return `<button class="due-button" data-due><span class="db-ico">📌</span>
      <span class="db-main"><b>Должен сдать</b><span class="muted small">${esc(parts.join(', '))}</span></span>
      <span class="db-arrow" aria-hidden="true">›</span></button>`;
}

export async function openDue() {
  tap();
  const body = pushScreen('Должен сдать');
  markScreen(body, 'due');
  reopenOnRefresh(body, openDue);
  body.innerHTML = skeleton(4);
  let found;
  try {
    found = await loadDue();
  } catch (error) {
    body.innerHTML = errorCard(error);
    return;
  }
  const paint = (found) => {
    const total = dueTotal(found);
    if (!total) {
      body.innerHTML = '<div class="empty big">Долгов нет 🎉</div>';
      return;
    }
    body.innerHTML = `
      ${found.debts.length ? `<div class="card due-card"><div class="card-title">Точки — не сдано в срок
        <span class="due-count">${found.debts.length}</span></div>
        <div class="muted small due-hint">Пока работа не сдана, точка считается в среднем как «2».</div>
        ${found.debts.map((entry, i) => markRow(entry, i, 'debt')).join('')}</div>` : ''}
      ${found.twos.length ? `<div class="card due-card"><div class="card-title">Двойки этой четверти
        <span class="due-count">${found.twos.length}</span></div>
        <div class="muted small due-hint">Их обычно можно исправить — спросите учителя.</div>
        ${found.twos.map((entry, i) => markRow(entry, i, 'two')).join('')}</div>` : ''}
      ${found.homework.length ? `<div class="card due-card"><div class="card-title">Не отмечено сделанным
        <span class="due-count">${found.homework.length}</span></div>
        <div class="muted small due-hint">Задания за ${HOMEWORK_DAYS} ${plural(HOMEWORK_DAYS, 'день', 'дня', 'дней')}${dueCutoff() ? `, не раньше ${esc(dayMonth(dueCutoff()))}` : ''}. Сделали — отметьте в задании.</div>
        ${found.homework.map(homeworkRow).join('')}
        <button class="btn outline due-clear" id="dueClear">Очистить список</button>
        <div class="muted small due-hint">Старые задания перестанут считаться долгом: учёт пойдёт с сегодняшнего дня.</div></div>` : ''}`;
    body.querySelectorAll('[data-debt]').forEach((row) => row.addEventListener('click', () => {
      const { subject, mark } = found.debts[Number(row.dataset.debt)];
      openMark(mark, subject);
    }));
    body.querySelectorAll('[data-two]').forEach((row) => row.addEventListener('click', () => {
      const { subject, mark } = found.twos[Number(row.dataset.two)];
      openMark(mark, subject);
    }));
    body.querySelector('#dueClear')?.addEventListener('click', async () => {
      tap();
      setPref('dueFrom', isoToday());
      paint(await loadDue());
      // Кнопка «Должен сдать» над списком заданий считает то же — обновим её.
      window.dispatchEvent(new CustomEvent('netschool:hwdone', { detail: { source: 'list' } }));
    });
    body.querySelectorAll('[data-hw]').forEach((row) => row.addEventListener('click', () => {
      openHomework(found.homework[Number(row.dataset.hw)]);
    }));
  };
  paint(found);
}

