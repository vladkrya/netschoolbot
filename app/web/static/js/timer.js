// Таймер урока над «Днём»: «До конца урока 12:34» с секундами, следующий
// урок и кабинет. На перемене — «До начала урока». Когда время вышло,
// карточка показывает «Урок окончен ✓», красиво сворачивается и, если дальше
// перемена или урок, раскрывается уже с новым отсчётом.
//
// Живёт в своём контейнере (#lessonTimer), а не в списке уроков: «День»
// перерисовывается по своему таймеру, и анимация не должна обрываться.
'use strict';

import { base, demo, esc, lessonNo, stillMotion } from './core.js';
import { notify } from './haptics.js';
import { getPref } from './prefs.js';

// До начала урока отсчёт показывается не раньше чем за столько минут — и
// перед первым, и после длинного окна (свой урок ночью, а дальше уроки с утра).
const BEFORE_FIRST_MIN = 60;
// Сколько держится «Урок окончен» или «Урок начался», прежде чем карточка
// свернётся, и пауза перед следующим отсчётом.
const DONE_HOLD_MS = 4000;
const NEXT_PAUSE_MS = 500;
// Сколько стоит «00:00» перед «Урок окончен».
const ZERO_HOLD_MS = 900;
// Длительность сворачивания и раскрытия (как в CSS).
const COLLAPSE_MS = 520;
// Последние секунды — заметнее: перед уроком красным, перед концом урока
// зелёным (app.css).
const URGENT_S = 15;
// Часы сверяются с сервером не чаще чем раз в столько; ответ дольше
// MAX_RTT_MS не годится — погрешность выйдет больше, чем расхождение.
const CLOCK_SYNC_MS = 10 * 60000;
const MAX_RTT_MS = 3000;
// Замеров за сверку: берём самый быстрый — у него меньше всего погрешность.
const CLOCK_SAMPLES = 3;
// Расхождение меньше этого — погрешность замера, а не неточные часы: точный
// телефон (автоматическое время) считает по своим, иначе поправка в доли
// секунды уводила «00» от часов на экране.
const CLOCK_TRUST_MS = 1000;
// Сервер не сверился с NTP — его часы сами могут спешить на секунды, и
// верим им, только когда телефон расходится с ними заметно.
const CLOCK_TRUST_UNSYNCED_MS = 10000;
// Сдвиг звонка в настройках: школьный звонок звенит на несколько секунд
// позже или раньше ровного времени.
export const BELL_SHIFTS = [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

let lessons = [];
let active = false;
let shownKey = '';
let busy = false;
let timer = 0;
let lastDigits = '';
// День сменился (листание): карточка уезжает и въезжает вместе с уроками,
// а не сворачивается и раскрывается — иначе уроки прыгали посреди листания.
let instant = false;
// Насколько часы сервера впереди часов телефона, мс. Часы телефонов
// расходятся на десятки секунд, и у одного класса на двух телефонах урок
// «кончался» в разное время.
let clockOffset = 0;
let clockSyncedAt = 0;

const nowMs = () => Date.now() + clockOffset;
const clock = () => new Date(nowMs());

/** Сейчас по часам, сверенным с сервером, — по ним же статусы уроков. */
export const syncedNow = clock;

/** Один замер: середина запроса — момент ответа сервера. */
async function sampleClock() {
  const sent = Date.now();
  const response = await fetch(`${base}/time`, { cache: 'no-store' });
  const { now, synced } = await response.json();
  const received = Date.now();
  if (!Number.isFinite(now) || received - sent > MAX_RTT_MS) return null;
  return { rtt: received - sent, offset: now * 1000 - (sent + received) / 2, synced: synced !== false };
}

/** Сверить часы с сервером. Первый запрос часто медленный (соединение ещё
 *  устанавливается) и смещён, поэтому замеров несколько. */
async function syncClock() {
  if (demo || Date.now() - clockSyncedAt < CLOCK_SYNC_MS) return;
  clockSyncedAt = Date.now();
  try {
    let best = null;
    for (let i = 0; i < CLOCK_SAMPLES; i += 1) {
      const sample = await sampleClock();
      if (sample && (!best || sample.rtt < best.rtt)) best = sample;
    }
    if (!best) return;
    const trust = best.synced ? CLOCK_TRUST_MS : CLOCK_TRUST_UNSYNCED_MS;
    clockOffset = Math.abs(best.offset) < trust ? 0 : Math.round(best.offset);
  } catch {
    // Без сети считаем по часам телефона и попробуем позже.
    clockSyncedAt = 0;
  }
}

/** Сдвиг звонка из настроек, с. */
export function bellShift() {
  const value = Number(getPref('bellShift'));
  return BELL_SHIFTS.includes(value) ? value : 0;
}

/** Сколько мс до следующей смены статусов уроков: граница минуты плюс
 *  сдвиг звонка, по сверенным часам, с запасом на опоздание таймера. */
export function msToBell() {
  return 60000 - ((nowMs() - bellShift() * 1000) % 60000 + 60000) % 60000 + 50;
}

function at(time, base, shift = 0) {
  const [h, m] = String(time || '').split(':').map(Number);
  if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
  const date = new Date(base);
  date.setHours(h, m, shift, 0);
  return date;
}

/** Что сейчас: урок, перемена перед уроком или ничего. shift — сдвиг
 *  звонка, с: и начало, и конец урока наступают на столько позже. */
export function timerPhase(list, now = new Date(), shift = 0) {
  const items = list
    .map((lesson) => ({ lesson, start: at(lesson.start, now, shift), end: at(lesson.end, now, shift) }))
    .filter((item) => item.start && item.end && item.end > item.start)
    .sort((a, b) => a.start - b.start);
  for (let i = 0; i < items.length; i += 1) {
    const { lesson, start, end } = items[i];
    const next = items[i + 1]?.lesson || null;
    if (now >= start && now < end) {
      return { kind: 'lesson', key: `lesson:${i}:${lesson.subject}`, lesson, next, from: start, until: end };
    }
    if (now < start) {
      const earliest = new Date(start.getTime() - BEFORE_FIRST_MIN * 60000);
      const prevEnd = i ? items[i - 1].end : earliest;
      const from = prevEnd > earliest ? prevEnd : earliest;
      if (now < from) return null;
      return { kind: 'break', key: `break:${i}:${lesson.subject}`, lesson, next: lesson, from, until: start, first: i === 0 || prevEnd < earliest };
    }
  }
  return null;
}

function clockText(seconds) {
  const s = Math.max(0, seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(h ? 2 : 2, '0');
  return `${h ? `${h}:` : ''}${mm}:${String(sec).padStart(2, '0')}`;
}

function nextText(phase) {
  if (phase.kind === 'break') {
    const room = phase.lesson.room ? ` · каб. ${phase.lesson.room}` : '';
    return `${lessonNo(phase.lesson) ? `${lessonNo(phase.lesson)} урок — ` : ''}${phase.lesson.subject}${room}`;
  }
  if (!phase.next) return 'Последний урок на сегодня';
  const room = phase.next.room ? ` · каб. ${phase.next.room}` : '';
  return `Дальше — ${phase.next.subject}${room}`;
}

function cardHtml(phase) {
  const isLesson = phase.kind === 'lesson';
  const title = isLesson
    ? `Идёт ${lessonNo(phase.lesson) ? `${lessonNo(phase.lesson)} урок` : 'урок'} · ${phase.lesson.subject}`
    : phase.first ? 'Скоро уроки' : 'Перемена';
  return `<div class="lt-inner"><div class="lt-card ${isLesson ? '' : 'break'}">
      <div class="lt-head"><span class="lt-dot"></span><span class="lt-title">${esc(title)}</span></div>
      <div class="lt-main">
        <span class="lt-label">${isLesson ? 'До конца урока' : 'До начала урока'}</span>
        <span class="lt-clock" aria-live="off"></span>
      </div>
      <div class="lt-bar"><span></span></div>
      <div class="lt-next">${esc(nextText(phase))}</div>
      <div class="lt-done" aria-hidden="true"><span class="lt-check">✓</span>
        <span>${isLesson ? 'Урок окончен' : 'Урок начался'}</span>
        <span class="lt-bats"><i></i><i></i><i></i><i></i></span></div>
    </div></div>`;
}

/** Цифры по одной: изменившаяся «перекатывается» сверху вниз. */
function paintClock(box, text) {
  const card = box.querySelector('.lt-card');
  const clock = box.querySelector('.lt-clock');
  if (!clock) return;
  if (clock.childElementCount !== text.length) {
    clock.innerHTML = [...text].map((ch) => `<span class="${ch === ':' ? 'lt-colon' : 'lt-digit'}">${ch}</span>`).join('');
  } else {
    [...text].forEach((ch, index) => {
      if (lastDigits[index] === ch) return;
      const cell = clock.children[index];
      cell.textContent = ch;
      cell.classList.remove('roll');
      void cell.offsetWidth;
      cell.classList.add('roll');
    });
  }
  // Двоеточие мигает в тот же кадр, что перекатываются цифры.
  if (text !== lastDigits) {
    for (const colon of clock.querySelectorAll('.lt-colon')) {
      colon.classList.remove('beat');
      void colon.offsetWidth;
      colon.classList.add('beat');
    }
  }
  lastDigits = text;
  card?.classList.toggle('urgent', text.length <= 5 && text <= `00:${String(URGENT_S).padStart(2, '0')}`);
  syncBeat(clock);
}

/** Пульс последних секунд — в такт смене секунд: CSS-анимация шла от
 *  появления карточки, со сдвигом на долю секунды. */
function syncBeat(clock) {
  clock.style.animationDelay = `-${nowMs() % 1000}ms`;
}

function paint(box, phase, now = clock()) {
  const left = Math.ceil((phase.until - now) / 1000);
  paintClock(box, clockText(left));
  const total = phase.until - phase.from;
  const percent = total > 0 ? Math.min(100, Math.max(0, (now - phase.from) / total * 100)) : 0;
  const bar = box.querySelector('.lt-bar span');
  if (bar) bar.style.width = `${percent}%`;
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function show(box, phase, now = false) {
  box.innerHTML = cardHtml(phase);
  lastDigits = '';
  paint(box, phase);
  shownKey = phase.key;
  box.hidden = false;
  box.classList.remove('collapsed');
  if (now) return;
  box.classList.add('collapsed');
  void box.offsetWidth;
  box.classList.remove('collapsed');
}

function hideNow(box) {
  box.hidden = true;
  box.innerHTML = '';
  box.classList.remove('collapsed');
  shownKey = '';
}

/** «00:00» — постоять, затем «Урок окончен» и подержать. */
async function celebrate(box) {
  const card = box.querySelector('.lt-card');
  // «00:00» успевает постоять, и только потом — «Урок окончен»: иначе
  // ноль мелькал на долю секунды и карточка менялась рывком.
  paintClock(box, clockText(0));
  const bar = box.querySelector('.lt-bar span');
  if (bar) bar.style.width = '100%';
  await wait(ZERO_HOLD_MS);
  card?.classList.add('done');
  notify('success');
  await wait(DONE_HOLD_MS);
}

async function collapse(box) {
  box.classList.add('collapsed');
  await wait(COLLAPSE_MS);
  box.hidden = true;
  box.innerHTML = '';
  shownKey = '';
}

/** Карточка перетекает в следующий отсчёт на месте — без сворачивания и
 *  раскрытия: иначе уроки под ней подпрыгивали вверх и съезжали обратно. */
async function morph(box, phase) {
  const fade = (el, from, to, ms) => (el?.animate && !stillMotion()
    ? el.animate([{ opacity: from }, { opacity: to }], { duration: ms, easing: 'ease', fill: 'forwards' }).finished
    : Promise.resolve());
  await fade(box.querySelector('.lt-inner'), 1, 0, 260);
  box.innerHTML = cardHtml(phase);
  lastDigits = '';
  paint(box, phase);
  shownKey = phase.key;
  await fade(box.querySelector('.lt-inner'), 0, 1, 320);
}

async function tick() {
  const box = document.getElementById('lessonTimer');
  if (!box || busy) return;
  const daySwitch = instant;
  instant = false;
  const phase = active ? timerPhase(lessons, clock(), bellShift()) : null;
  const key = phase?.key || '';
  if (key === shownKey) {
    if (phase) paint(box, phase);
    return;
  }
  if (shownKey) {
    // Ушли на другой день — карточка уезжает вместе с уроками, сразу.
    if (!active || daySwitch) {
      hideNow(box);
      if (!active) return;
    } else {
      busy = true;
      try {
        // Отсчёт дошёл до нуля: «Урок окончен», затем — следующий отсчёт
        // на том же месте, а если дальше ничего — карточка сворачивается.
        await celebrate(box);
        const next = active ? timerPhase(lessons, clock(), bellShift()) : null;
        if (next) {
          await wait(NEXT_PAUSE_MS);
          await morph(box, next);
          return;
        }
        await collapse(box);
      } finally {
        busy = false;
      }
      return;
    }
  }
  const fresh = active ? timerPhase(lessons, clock(), bellShift()) : null;
  if (fresh) await show(box, fresh, daySwitch);
}

/** Следующий тик — сразу после смены секунды. Раньше был setInterval с
 *  произвольным сдвигом: цифры менялись с опозданием до секунды, а «00:00»
 *  приходило вовремя (по смене статуса урока), и 00:01 висело полсекунды. */
function schedule() {
  timer = setTimeout(() => {
    schedule();
    tick();
  }, 1000 - (nowMs() % 1000) + 20);
}

/** День перерисован: уроки сегодняшнего дня или null (другой день, нет уроков). */
export function updateTimer(list, isToday) {
  lessons = Array.isArray(list) ? list : [];
  // Перешли на другой день — карточка уходит или появляется сразу, без
  // «Урок окончен» и без сворачивания.
  const next = Boolean(isToday && lessons.length);
  if (next !== active) instant = true;
  active = next;
  if (!timer) {
    schedule();
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) return;
      tick();
      syncClock().then(tick);
    });
  }
  tick();
  syncClock().then(tick);
}
