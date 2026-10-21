// Обратный отсчёт на «Дне»: до каникул и, у 9-х и 11-х классов, до ОГЭ/ЕГЭ.
//
// Сервер отдаёт даты (/api/countdown), число дней считается здесь от
// сегодняшнего дня: ответ хранится полдня и после полуночи не отстаёт.
'use strict';

import { api, banner, dayMonth, esc, isoToday, isReadOnly, parseIso, plural, state } from './core.js';
import { tap } from './haptics.js';
import { getPref, setPref } from './prefs.js';

const ICON = { 'Осенние каникулы': '🍂', 'Зимние каникулы': '❄️', 'Весенние каникулы': '🌷', 'Летние каникулы': '☀️' };
const TO_LABEL = {
  'Осенние каникулы': 'осенних каникул', 'Зимние каникулы': 'зимних каникул',
  'Весенние каникулы': 'весенних каникул', 'Летние каникулы': 'летних каникул',
};

let loading = null;

export function countdownShown() {
  return getPref('countdown') !== 'hide';
}

export function saveCountdown(show) {
  setPref('countdown', show ? 'show' : 'hide');
  // «День» перерисуется (слушатель в app.js) и при включении сам загрузит даты.
  window.dispatchEvent(new CustomEvent('netschool:design'));
}

/** Загрузить даты один раз за сессию; `onReady` — перерисовать «День». */
export function loadCountdown(onReady) {
  if (state.countdown !== undefined || !countdownShown()) return;
  if (!loading) {
    loading = api('/api/countdown')
      .then(({ data }) => { state.countdown = data; })
      .catch(() => { state.countdown = null; })
      .finally(() => { loading = null; });
  }
  loading.then(() => onReady?.());
}

const daysBetween = (from, to) => Math.round((parseIso(to) - parseIso(from)) / 86400000);
const days = (count) => `${count} ${plural(count, 'день', 'дня', 'дней')}`;

/** Карточка для сегодняшнего дня; на другие дни отсчёт не нужен. */
export function countdownHtml() {
  const data = state.countdown;
  const today = isoToday();
  if (!data || state.date !== today || !countdownShown()) return '';
  const rows = [];
  const vacation = data.vacation;
  if (vacation) {
    const icon = ICON[vacation.label] || '🎒';
    if (vacation.now) {
      const left = daysBetween(today, vacation.end) + 1;
      rows.push(`<div class="cd-row"><span class="cd-ico">${icon}</span><span class="cd-text">
        <b>${esc(vacation.label)}</b> — ещё ${days(left)}
        <span class="cd-note">до ${esc(dayMonth(vacation.end))} включительно${data.vacation_exact ? '' : ' · примерно'}</span></span></div>`);
    } else {
      const left = daysBetween(today, vacation.start);
      rows.push(`<div class="cd-row"><span class="cd-ico">${icon}</span><span class="cd-text">
        До ${esc(TO_LABEL[vacation.label] || 'каникул')} <b>${days(left)}</b>
        <span class="cd-note">с ${esc(dayMonth(vacation.start))}${data.vacation_exact ? '' : ' · даты примерные'}</span></span></div>`);
    }
  }
  if (data.exam) {
    const left = daysBetween(today, data.exam.start);
    rows.push(`<div class="cd-row"><span class="cd-ico">🎓</span><span class="cd-text">
      До ${esc(data.exam.name)} ${data.exam.confirmed ? '' : '≈ '}<b>${days(left)}</b>
      <span class="cd-note">начало — ${esc(dayMonth(data.exam.start))}${data.exam.confirmed ? '' : ' · примерно, расписание ещё не утвердили'}</span></span></div>`);
  }
  if (!rows.length) return '';
  return `<div class="countdown-card">${rows.join('')}
    ${isReadOnly() ? '' : '<button class="cd-close" data-countdown-hide aria-label="Скрыть отсчёт">×</button>'}</div>`;
}

export function bindCountdown(container, rerender) {
  container.querySelector('[data-countdown-hide]')?.addEventListener('click', (event) => {
    event.stopPropagation();
    tap();
    saveCountdown(false);
    rerender();
    banner('Отсчёт скрыт. Вернуть — в настройках, «Отсчёт до каникул».', false, 3500);
  });
}
