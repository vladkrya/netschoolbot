// «Расписание»: неделя карточками дней — время занятий, число уроков,
// раскрывающийся список и переход на день.
'use strict';

import {
  $, addDays, dayMonth, errorCard, esc, isoToday, mondayOf, showFailure, showLoading, skeleton, state,
  WEEKDAYS_FULL, weekOffsetOf,
} from './core.js';
import { dayData, ensureWeek, goToDay, isHiddenWeekend, openCalendar } from './day.js';
import { isNewDesign } from './appearance.js';
import { select, tap } from './haptics.js';

const expanded = new Set();
const expansionAnimations = new WeakMap();

function lessonsWord(count) {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return 'уроков';
  if (count % 10 === 1) return 'урок';
  if ([2, 3, 4].includes(count % 10)) return 'урока';
  return 'уроков';
}

function weekTitle(offset) {
  if (offset === 0) return 'Текущая';
  if (offset === 1) return 'Следующая';
  if (offset === -1) return 'Прошлая';
  return offset > 0 ? `Через ${offset} нед.` : `${-offset} нед. назад`;
}

export async function loadSchedule(force = false) {
  const monday = addDays(mondayOf(isoToday()), state.scheduleWeek * 7);
  $('schedLabel').textContent = weekTitle(state.scheduleWeek);
  $('schedSub').textContent = `${dayMonth(monday)} – ${dayMonth(addDays(monday, 6))}`;

  const container = $('scheduleContent');
  if (force || !dayData(monday)) showLoading(container, skeleton());
  try {
    await ensureWeek(monday, force);
  } catch (error) {
    showFailure(container, error, errorCard(error, 'schedRetry'));
    $('schedRetry')?.addEventListener('click', () => loadSchedule(true));
    return;
  }
  render(monday);
}

function render(monday) {
  const today = isoToday();
  const cards = [];
  for (let index = 0; index < 7; index += 1) {
    const iso = addDays(monday, index);
    const lessons = dayData(iso)?.lessons || [];
    // Субботу и воскресенье без уроков не показываем, если не включено
    // «Показывать выходные» в меню.
    if (isHiddenWeekend(iso)) continue;

    const first = lessons[0];
    const last = lessons[lessons.length - 1];
    const range = first && last && first.start && last.end ? `${first.start} — ${last.end}` : '';
    if (isNewDesign()) {
      cards.push(weekCard(iso, index, lessons, range, iso === today));
      continue;
    }
    const open = expanded.has(iso);
    cards.push(`<div class="card sched-card ${iso === today ? 'current' : ''}">
        <div class="sched-top">
          <div><div class="sched-day">${WEEKDAYS_FULL[index]}</div><div class="muted small">${esc(dayMonth(iso))}</div></div>
          <div class="sched-range">${esc(range)}</div>
        </div>
        ${lessons.length
          ? `<button class="sched-toggle" data-toggle="${iso}" aria-expanded="${open}" aria-controls="sched-${iso}">${lessons.length} ${lessonsWord(lessons.length)} <span class="sched-chevron" aria-hidden="true">▾</span></button>`
          : '<div class="muted small sched-toggle">Уроков нет</div>'}
        ${lessons.length ? `<div id="sched-${iso}" class="sched-collapse${open ? ' open' : ''}"${open ? '' : ' inert'}><div class="sched-collapse-inner"><div class="sched-list">
            ${lessons.map((l) => `<div class="sched-lesson"><span class="lc-num">${esc(l.number ?? '')}</span><span>${esc(l.subject)}${l.is_elective ? '<span class="elective-badge">Факультатив</span>' : ''}</span></div>`).join('')}
            <button class="btn outline" data-goto="${iso}">Перейти ко дню</button>
          </div></div></div>` : ''}
      </div>`);
  }

  const container = $('scheduleContent');
  container.innerHTML = cards.join('');
  container.querySelectorAll('[data-toggle]').forEach((button) =>
    button.addEventListener('click', () => {
      select();
      const iso = button.dataset.toggle;
      const open = !expanded.has(iso);
      if (open) expanded.add(iso);
      else expanded.delete(iso);
      button.setAttribute('aria-expanded', String(open));
      const panel = document.getElementById(button.getAttribute('aria-controls'));
      const from = panel.getBoundingClientRect().height;
      expansionAnimations.get(panel)?.cancel();
      panel.inert = !open;
      panel.classList.toggle('open', open);
      panel.style.height = open ? 'auto' : '0px';
      if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
        const to = open ? panel.scrollHeight : 0;
        const animation = panel.animate([{ height: `${from}px` }, { height: `${to}px` }], {
          duration: 240, easing: 'cubic-bezier(.2,.8,.3,1)',
        });
        expansionAnimations.set(panel, animation);
      }
    })
  );
  container.querySelectorAll('[data-goto]').forEach((button) =>
    button.addEventListener('click', () => {
      tap();
      window.dispatchEvent(new CustomEvent('netschool:tab', { detail: 'day' }));
      goToDay(button.dataset.goto);
    })
  );
}

/** Новый дизайн: день недели целиком, без раскрытия — уроки со временем.
 *  Нажатие на день открывает его во вкладке «День». */
function weekCard(iso, index, lessons, range, isToday) {
  return `<button class="card sched-card week-card ${isToday ? 'current' : ''}" data-goto="${iso}">
      <div class="sched-top">
        <div><div class="sched-day">${WEEKDAYS_FULL[index]}</div><div class="muted small">${esc(dayMonth(iso))}${isToday ? ' · сегодня' : ''}</div></div>
        <div class="sched-range">${esc(range.replace(' — ', '–'))}</div>
      </div>
      ${lessons.length ? `<div class="sched-list">${lessons.map((l) => `<div class="sched-lesson">
          <span class="lc-num">${esc(l.number ?? '')}</span>
          <span class="sl-subject">${esc(l.subject)}${l.is_elective ? '<span class="elective-badge">Факультатив</span>' : ''}</span>
          <span class="sl-time">${esc(l.start || '')}</span>
        </div>`).join('')}</div>` : '<div class="muted small sched-empty">Уроков нет</div>'}
    </button>`;
}

export function initSchedule() {
  $('schedPrev').addEventListener('click', () => {
    select();
    state.scheduleWeek -= 1;
    loadSchedule();
  });
  $('schedNext').addEventListener('click', () => {
    select();
    state.scheduleWeek += 1;
    loadSchedule();
  });
  $('schedTitle').addEventListener('click', () => {
    const monday = addDays(mondayOf(isoToday()), state.scheduleWeek * 7);
    openCalendar(monday, (iso) => {
      state.scheduleWeek = weekOffsetOf(iso);
      loadSchedule();
    }, { week: true });
  });
}
