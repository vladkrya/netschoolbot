// Общие анимации: вспышка новой оценки с конфетти, одометр среднего балла и
// перелёт элемента с одного экрана на другой. Всё молчит, если в системе
// включено «Уменьшить движение».
'use strict';

import { isoToday, stillMotion } from './core.js';
import { showConfetti } from './appearance.js';

export const reducedMotion = stillMotion;

/* ─────────────────────────── Новые оценки ─────────────────────────── */

// Какие оценки человек уже видел — чтобы вспыхивали только свежие. since —
// день, с которого ведётся учёт: старые оценки при первом запуске не
// должны все разом «прийти».
const SEEN_KEY = 'ns-seen-marks';
const SEEN_MAX = 3000;
let seen = null;

function loadSeen() {
  if (seen) return seen;
  try {
    const raw = JSON.parse(localStorage.getItem(SEEN_KEY) || 'null');
    if (raw?.since && Array.isArray(raw.keys)) seen = { since: raw.since, keys: new Set(raw.keys) };
  } catch {
    /* хранилище недоступно или испорчено — начнём учёт заново */
  }
  if (!seen) {
    seen = { since: isoToday(), keys: new Set() };
    saveSeen();
  }
  return seen;
}

function saveSeen() {
  try {
    const keys = [...seen.keys].slice(-SEEN_MAX);
    localStorage.setItem(SEEN_KEY, JSON.stringify({ since: seen.since, keys }));
  } catch {
    /* без хранилища вспышка повторится после перезагрузки — не страшно */
  }
}

export function markKey(mark, subject, date) {
  return [subject, mark.date || date, mark.type || mark.title || '', mark.mark, mark.weight ?? ''].join('|');
}

/** Оценки, которых человек ещё не видел, — по ключам markKey. Сразу же
 *  запоминаются увиденными: вспыхнуть оценка должна один раз. */
export function takeFresh(entries) {
  const store = loadSeen();
  const fresh = new Set();
  for (const { key, date } of entries) {
    if (store.keys.has(key)) continue;
    store.keys.add(key);
    if (date >= store.since) fresh.add(key);
  }
  if (entries.length) saveSeen();
  return fresh;
}

/** Вспышка у новых оценок внутри root (.mark-box.is-fresh); у пятёрки —
 *  ещё и конфетти. Двойка появляется мягко, без праздника. */
export function playFresh(root) {
  // Одна и та же оценка видна и в сводке дня, и в карточке урока —
  // конфетти одно, из сводки.
  const summary = root.querySelector('.ds-mark .is-fresh');
  root.querySelectorAll('.mark-box.is-fresh').forEach((box, index) => {
    const value = (box.firstChild?.textContent || '').trim();
    const party = !summary || box.closest('.ds-mark');
    box.classList.add(/^[12]/.test(value) ? 'mark-soft' : 'mark-flash');
    box.style.animationDelay = `${120 + index * 90}ms`;
    if (value === '5' && party && showConfetti() && !reducedMotion()) setTimeout(() => confetti(box), 300 + index * 90);
  });
}

let canvas = null;
let parts = [];
let frame = 0;

function confetti(origin) {
  const rect = origin.getBoundingClientRect();
  if (!rect.width) return;
  if (!canvas) {
    canvas = document.createElement('canvas');
    canvas.className = 'confetti';
    canvas.ariaHidden = 'true';
    document.body.append(canvas);
  }
  // Плотность не выше 1.5: на телефоне холст на весь экран в 3x тяжело
  // перерисовывать каждый кадр, а конфетти мелкие — разницы не видно.
  const ratio = Math.min(devicePixelRatio || 1, 1.5);
  canvas.width = innerWidth * ratio;
  canvas.height = innerHeight * ratio;
  const styles = getComputedStyle(document.documentElement);
  const colors = ['--g5', '--g4', '--primary', '--g3'].map((name) => styles.getPropertyValue(name).trim() || '#22a04b');
  for (let i = 0; i < 30; i += 1) {
    const angle = -Math.PI / 2 + (Math.random() - 0.5) * 2.2;
    const speed = 3 + Math.random() * 4;
    parts.push({
      x: rect.left + rect.width / 2, y: rect.top + rect.height / 2,
      vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
      turn: Math.random() * 6, spin: (Math.random() - 0.5) * 0.4,
      w: 4 + Math.random() * 4, h: 2 + Math.random() * 3, color: colors[i % colors.length], life: 1,
    });
  }
  if (!frame) frame = requestAnimationFrame(drawConfetti);
}

function drawConfetti() {
  const ctx = canvas.getContext('2d');
  const ratio = Math.min(devicePixelRatio || 1, 1.5);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  parts = parts.filter((p) => p.life > 0);
  for (const p of parts) {
    p.vy += 0.18;
    p.vx *= 0.98;
    p.x += p.vx;
    p.y += p.vy;
    p.turn += p.spin;
    p.life -= 0.014;
    ctx.save();
    ctx.globalAlpha = Math.max(0, Math.min(1, p.life * 1.5));
    ctx.translate(p.x * ratio, p.y * ratio);
    ctx.rotate(p.turn);
    ctx.fillStyle = p.color;
    ctx.fillRect((-p.w * ratio) / 2, (-p.h * ratio) / 2, p.w * ratio, p.h * ratio);
    ctx.restore();
  }
  frame = parts.length ? requestAnimationFrame(drawConfetti) : 0;
  if (!frame) ctx.clearRect(0, 0, canvas.width, canvas.height);
}

/* ─────────────────────────── Одометр ─────────────────────────── */

// Последний показанный средний по ключу (предмет и период) — между
// перерисовками и перезапусками. Сдвинулся — цифры прокручиваются.
const AVG_KEY = 'ns-shown-averages';
let shownAverages = null;

function averages() {
  if (shownAverages) return shownAverages;
  try {
    shownAverages = JSON.parse(localStorage.getItem(AVG_KEY) || '{}') || {};
  } catch {
    shownAverages = {};
  }
  return shownAverages;
}

/** Средние баллы внутри root.
 *
 *  data-odo="ключ" — цифры прокручиваются, как счётчик: от прошлого
 *  значения, если оно поменялось, а при входе во вкладку (entrance) — и
 *  без изменений, с нулей. data-count="ключ" (экран предмета) — число
 *  досчитывает от нуля до своего значения. С data-odo-trend рядом
 *  появляется стрелка, если средний сдвинулся с прошлого показа. */
export function playAverages(root, { entrance = false } = {}) {
  const store = averages();
  let changed = false;
  root.querySelectorAll('[data-odo], [data-count]').forEach((el) => {
    // Вкладку рисуют и заранее, в фоне: там прокрутку никто не увидит —
    // пусть отыграет, когда вкладку откроют.
    if (!el.getClientRects().length) return;
    const key = el.dataset.odo ?? el.dataset.count;
    const text = el.textContent.trim();
    const before = store[key];
    if (before !== text) {
      store[key] = text;
      changed = true;
    }
    const to = Number(text.replace(',', '.'));
    if (reducedMotion() || !Number.isFinite(to)) return;
    const from = before == null ? NaN : Number(before.replace(',', '.'));
    const moved = Number.isFinite(from) && before !== text;
    if (el.dataset.count != null) countUp(el, text);
    else if (moved) odometer(el, before, text);
    else if (entrance) odometer(el, text.replace(/\d/g, '0'), text);
    if (moved && el.dataset.odoTrend != null) trendChip(el, to - from);
  });
  if (!changed) return;
  try {
    localStorage.setItem(AVG_KEY, JSON.stringify(store));
  } catch {
    /* без хранилища прокрутка просто не повторится */
  }
}

/** Число досчитывает от нуля до text («4,57»): быстро в начале, плавно к концу. */
function countUp(el, text) {
  const target = Number(text.replace(',', '.'));
  const digits = (text.split(',')[1] || '').length;
  const start = performance.now();
  const duration = 900;
  const frame = (now) => {
    if (!el.isConnected) return;
    const t = Math.min(1, (now - start) / duration);
    el.textContent = t < 1 ? (target * (1 - (1 - t) ** 3)).toFixed(digits).replace('.', ',') : text;
    if (t < 1) requestAnimationFrame(frame);
  };
  el.textContent = (0).toFixed(digits).replace('.', ',');
  requestAnimationFrame(frame);
}

function odometer(el, from, to) {
  if (from.length !== to.length) return;
  const reel = (digit) => `<span class="odo-col"><span class="odo-reel" style="transform:translateY(${-digit}em)">${
    '0123456789'.split('').map((n) => `<span>${n}</span>`).join('')}</span></span>`;
  el.classList.add('odo');
  el.innerHTML = [...from].map((ch) => (/\d/.test(ch) ? reel(Number(ch)) : `<span>${ch}</span>`)).join('');
  // Кадр на то, чтобы старые цифры встали, — потом едут к новым.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    [...to].forEach((ch, index) => {
      const strip = el.children[index]?.querySelector('.odo-reel');
      if (strip) strip.style.transform = `translateY(${-Number(ch)}em)`;
    });
  }));
  setTimeout(() => {
    // Дальше это снова обычный текст: его читают и перекрашивают другие места.
    if (!el.isConnected) return;
    el.classList.remove('odo');
    el.textContent = to;
  }, 1100);
}

function trendChip(el, delta) {
  if (!delta) return;
  const chip = document.createElement('span');
  chip.className = `odo-trend ${delta > 0 ? 'up' : 'down'}`;
  chip.textContent = `${delta > 0 ? '↑' : '↓'} ${Math.abs(delta).toFixed(2).replace('.', ',')}`;
  el.after(chip);
  setTimeout(() => chip.classList.add('hide'), 2600);
  setTimeout(() => chip.remove(), 3000);
}

/* ─────────────────────────── Перелёт ─────────────────────────── */

/** Где target окажется, когда доиграют анимации появления вокруг него
 *  (экран урока въезжает, карточки поднимаются). Анимации на миг
 *  перематываются в конец, место замеряется один раз и время
 *  возвращается — так копии не нужно гнаться за целью каждый кадр:
 *  замер на каждом кадре заставлял телефон заново раскладывать страницу,
 *  и полёт подлагивал. */
function settledRect(target) {
  const moving = document.getAnimations().filter((animation) => {
    const element = animation.effect?.target;
    const end = animation.effect?.getComputedTiming().endTime;
    return element?.contains(target) && Number.isFinite(end) && animation.playState !== 'finished';
  });
  const saved = moving.map((animation) => animation.currentTime);
  moving.forEach((animation) => { animation.currentTime = animation.effect.getComputedTiming().endTime; });
  const rect = target.getBoundingClientRect();
  moving.forEach((animation, index) => { animation.currentTime = saved[index]; });
  return rect;
}

/** Копия target перелетает из прямоугольника rect на место target. Пока
 *  летит, сам target спрятан. Летит одна анимация transform — её ведёт
 *  видеокарта, без пересчёта страницы. */
export function flyTo(rect, target, duration = 460) {
  if (!target || reducedMotion() || !rect?.width) return;
  const end = settledRect(target);
  if (!end.width) return;
  const flyer = target.cloneNode(true);
  flyer.classList.add('flyer');
  Object.assign(flyer.style, { left: `${end.left}px`, top: `${end.top}px`, width: `${end.width}px`, height: `${end.height}px` });
  document.body.append(flyer);
  target.style.visibility = 'hidden';
  const animation = flyer.animate([
    { transform: `translate(${rect.left - end.left}px, ${rect.top - end.top}px) scale(${rect.width / end.width})` },
    { transform: 'none' },
  ], { duration, easing: 'cubic-bezier(.2, .8, .3, 1)' });
  const done = () => {
    flyer.remove();
    target.style.visibility = '';
  };
  animation.onfinish = done;
  animation.oncancel = done;
}
