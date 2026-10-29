// Общее для игр: рекорды, игровые фишки, колода, звуки, анимации, холст,
// жесты, стартовый и финальный экраны.
//
// Игры работают без сети: всё хранится в localStorage этого устройства,
// соперники — боты, звуки синтезируются на лету (без файлов). Модули игр
// качаются заранее (games.js, warmGames).
'use strict';

import { notify, select, tap } from '../haptics.js';

export { notify, select, tap };

function read(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key));
    return value ?? fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* приватный режим — до перезагрузки */ }
}

export const load = (key, fallback = null) => read(`ns-game-${key}`, fallback);
export const save = (key, value) => write(`ns-game-${key}`, value);

// ───────────── Рекорды ─────────────

const BEST_KEY = 'ns-games-best';

export function best(game) {
  return read(BEST_KEY, {})[game] || 0;
}

/** Рекорд как строка: «—», пока его нет. */
export const recordText = (game, format = String) => (best(game) ? format(best(game)) : '—');

/** Рекорд: больше — лучше (очки) или меньше — лучше (время). true — новый. */
export function saveBest(game, score, lower = false) {
  const all = read(BEST_KEY, {});
  const old = all[game] || 0;
  if (!score || (old && (lower ? score >= old : score <= old))) return false;
  all[game] = score;
  write(BEST_KEY, all);
  return true;
}

export function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// ───────────── Фишки ─────────────
// Слоты и блэкджек — на общие игровые фишки, не на деньги. Кончились —
// банк выдаёт новые.

const CHIPS_KEY = 'ns-games-chips';
export const START_CHIPS = 1000;

export function chips() {
  const value = read(CHIPS_KEY, START_CHIPS);
  return Number.isFinite(value) ? Math.max(0, Math.round(value)) : START_CHIPS;
}

export function addChips(amount) {
  const value = Math.max(0, chips() + Math.round(amount));
  write(CHIPS_KEY, value);
  return value;
}

export function refillChips() {
  write(CHIPS_KEY, START_CHIPS);
  return START_CHIPS;
}

// ───────────── Случайность ─────────────

export const rand = (n) => Math.floor(Math.random() * n);

export function shuffle(items) {
  for (let i = items.length - 1; i > 0; i -= 1) {
    const j = rand(i + 1);
    [items[i], items[j]] = [items[j], items[i]];
  }
  return items;
}

// ───────────── Звук ─────────────
// Короткие синтезированные звуки: файлы не нужны, работает без сети.
// Выключаются кнопкой в шапке игры, выбор общий для всех игр.

let audio = null;
let muted = read('ns-games-muted', false) === true;

function context() {
  if (!audio) {
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return null;
    audio = new Ctor();
  }
  if (audio.state === 'suspended') audio.resume().catch(() => {});
  return audio;
}

function tone(freq, duration, { type = 'sine', vol = 0.12, slide = 0, delay = 0 } = {}) {
  const ac = context();
  if (!ac) return;
  const at = ac.currentTime + delay;
  const osc = ac.createOscillator();
  const gain = ac.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, at);
  if (slide) osc.frequency.exponentialRampToValueAtTime(Math.max(30, freq + slide), at + duration);
  gain.gain.setValueAtTime(0.0001, at);
  gain.gain.exponentialRampToValueAtTime(vol, at + 0.008);
  gain.gain.exponentialRampToValueAtTime(0.0001, at + duration);
  osc.connect(gain).connect(ac.destination);
  osc.start(at);
  osc.stop(at + duration + 0.02);
}

function noise(duration, { vol = 0.15, freq = 2400, delay = 0, q = 0.8 } = {}) {
  const ac = context();
  if (!ac) return;
  const at = ac.currentTime + delay;
  const buffer = ac.createBuffer(1, Math.ceil(ac.sampleRate * duration), ac.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < data.length; i += 1) data[i] = (Math.random() * 2 - 1) * (1 - i / data.length) ** 2;
  const src = ac.createBufferSource();
  src.buffer = buffer;
  const filter = ac.createBiquadFilter();
  filter.type = 'bandpass';
  filter.frequency.value = freq;
  filter.Q.value = q;
  const gain = ac.createGain();
  gain.gain.value = vol;
  src.connect(filter).connect(gain).connect(ac.destination);
  src.start(at);
}

/** Колокольчик: основной тон с двумя быстро гаснущими обертонами. */
function bell(freq, duration, vol, delay = 0) {
  tone(freq, duration, { vol, delay });
  tone(freq * 2.01, duration * 0.55, { vol: vol * 0.35, delay });
  tone(freq * 2.76, duration * 0.3, { vol: vol * 0.15, delay });
}

const arp = (notes, step, opts) => notes.forEach((f, i) => tone(f, opts.duration || 0.16, { ...opts, delay: i * step }));

const SOUNDS = {
  tap: () => tone(700, 0.05, { type: 'triangle', vol: 0.07 }),
  click: () => noise(0.03, { vol: 0.25, freq: 3200, q: 2 }),
  card: () => noise(0.07, { vol: 0.35, freq: 1800 }),
  flip: () => noise(0.05, { vol: 0.25, freq: 3000 }),
  deal: () => noise(0.09, { vol: 0.3, freq: 1400 }),
  move: () => tone(260, 0.035, { type: 'square', vol: 0.025 }),
  rotate: () => tone(520, 0.05, { type: 'triangle', vol: 0.06, slide: 120 }),
  land: () => tone(150, 0.09, { type: 'sine', vol: 0.18, slide: -50 }),
  drop: () => { tone(300, 0.12, { type: 'sine', vol: 0.15, slide: -200 }); noise(0.08, { vol: 0.2, freq: 600 }); },
  eat: () => tone(480, 0.09, { type: 'square', vol: 0.05, slide: 420 }),
  line: () => arp([523, 659, 784], 0.06, { type: 'triangle', vol: 0.09 }),
  tetris: () => arp([523, 659, 784, 1047, 1319], 0.06, { type: 'triangle', vol: 0.1 }),
  coin: () => { tone(988, 0.07, { type: 'square', vol: 0.05 }); tone(1319, 0.18, { type: 'square', vol: 0.05, delay: 0.07 }); },
  win: () => arp([523, 659, 784, 1047, 784, 1047], 0.09, { type: 'triangle', vol: 0.1, duration: 0.22 }),
  lose: () => arp([392, 349, 311, 262], 0.14, { type: 'sawtooth', vol: 0.045, duration: 0.25 }),
  error: () => tone(150, 0.12, { type: 'square', vol: 0.05 }),
  good: () => arp([660, 990], 0.06, { type: 'triangle', vol: 0.07 }),
  tick: () => noise(0.02, { vol: 0.2, freq: 4200, q: 3 }),
  stop: () => { noise(0.05, { vol: 0.3, freq: 900 }); tone(180, 0.06, { type: 'square', vol: 0.04 }); },
  hammer: () => { noise(0.03, { vol: 0.5, freq: 3500, q: 3 }); noise(0.05, { vol: 0.3, freq: 1200, delay: 0.03 }); },
  bang: () => { noise(0.6, { vol: 0.9, freq: 500, q: 0.4 }); tone(90, 0.4, { type: 'sine', vol: 0.3, slide: -50 }); },
  spin: () => { for (let i = 0; i < 12; i += 1) noise(0.02, { vol: 0.18, freq: 3800, q: 3, delay: i * (0.03 + i * 0.006) }); },
  chip: () => { noise(0.03, { vol: 0.35, freq: 5000, q: 4 }); noise(0.03, { vol: 0.25, freq: 4200, q: 4, delay: 0.04 }); },
  level: () => arp([392, 523, 659, 784], 0.07, { type: 'square', vol: 0.05 }),
  // Block Blast: короткий «пик» при постановке, колокольчики при сгорании.
  bbPick: () => tone(520, 0.05, { type: 'triangle', vol: 0.06, slide: 120 }),
  bbPlace: () => { tone(360, 0.1, { vol: 0.24, slide: 190 }); noise(0.03, { vol: 0.16, freq: 1600 }); },
  bbBack: () => tone(320, 0.1, { vol: 0.12, slide: -140 }),
  bbClear: ({ lines = 1, combo = 1 } = {}) => {
    const up = 2 ** ((2 * (Math.min(combo, 8) - 1)) / 12);
    tone(990 * up, 0.14, { type: 'triangle', vol: 0.08 });
    [1, 1.26, 1.5, 2, 2.52].slice(0, Math.min(lines, 5)).forEach((ratio, i) => bell(392 * up * ratio, 0.9, 0.13, i * 0.075));
  },
  bbClean: () => [262, 392, 523, 659, 784, 1047].forEach((f, i) => bell(f, 1, 0.14, i * 0.09)),
  bbOver: () => [392, 330, 294, 262].forEach((f, i) => bell(f, 0.7, 0.12, i * 0.22)),
};

export function sfx(name, arg) {
  if (muted) return;
  try { SOUNDS[name]?.(arg); } catch { /* без звука */ }
}

export const isMuted = () => muted;
export function setMuted(value) {
  muted = value;
  write('ns-games-muted', value);
}

// ───────────── Карты ─────────────
// Карта — { r, s }: r — достоинство числом (туз — 1 в пасьянсе, 14 в
// остальных играх), s — масть.

export const SUITS = ['♠', '♥', '♦', '♣'];
export const isRed = (card) => card.s === '♥' || card.s === '♦';
const FACES = { 1: 'A', 11: 'J', 12: 'Q', 13: 'K', 14: 'A' };
export const rankLabel = (r) => FACES[r] || String(r);
const FIGURES = { 11: '♞', 12: '♛', 13: '♚' };
export const cardKey = (card) => `${card.r}${card.s}${card.copy || ''}`;

export function deck(from, to, copies = 1) {
  const cards = [];
  for (let copy = 0; copy < copies; copy += 1) {
    for (const s of SUITS) for (let r = from; r <= to; r += 1) cards.push(copies > 1 ? { r, s, copy } : { r, s });
  }
  return shuffle(cards);
}

/** Карта разметкой: уголки сверху и снизу, крупная масть или картинка
 *  посередине. key — data-k для анимации перелёта (flip). */
export function cardHtml(card, { attrs = '', cls = '', key = '' } = {}) {
  const k = key ? ` data-k="${key}"` : '';
  if (!card) return `<span class="pc empty ${cls}"${k} ${attrs}></span>`;
  if (card.down) return `<span class="pc back ${cls}"${k} ${attrs}></span>`;
  const label = rankLabel(card.r);
  const face = FIGURES[card.r];
  // Картинка — шахматная фигура в рамке; \uFE0E не даёт iOS сделать из неё эмодзи.
  const middle = face ? `<span class="pc-face"><span class="pc-fig">${face}\uFE0E</span><small>${card.s}</small></span>`
    : card.r === 1 || card.r === 14 ? `<span class="pc-mid ace">${card.s}</span>` : `<span class="pc-mid">${card.s}</span>`;
  return `<span class="pc ${isRed(card) ? 'red' : ''} ${cls}"${k} ${attrs}>
      <span class="pc-c tl"><b>${label}</b><i>${card.s}</i></span>${middle}
      <span class="pc-c br"><b>${label}</b><i>${card.s}</i></span></span>`;
}

// ───────────── Анимации ─────────────

const reduced = () => document.documentElement.classList.contains('no-motion')
  || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** Плавный перелёт элементов с data-k на новое место после перерисовки
 *  (FLIP). enterFrom(el) — откуда прилетают новые элементы (DOMRect или
 *  null — просто появиться). */
export function flip(root, mutate, { duration = 320, enterFrom = null, stagger = 0 } = {}) {
  const before = new Map();
  root.querySelectorAll('[data-k]').forEach((el) => before.set(el.dataset.k, el.getBoundingClientRect()));
  mutate();
  if (reduced()) return;
  let entered = 0;
  root.querySelectorAll('[data-k]').forEach((el) => {
    const now = el.getBoundingClientRect();
    const old = before.get(el.dataset.k) || enterFrom?.(el) || null;
    if (!old) {
      if (!before.has(el.dataset.k)) {
        el.animate([{ opacity: 0, scale: '0.85' }, { opacity: 1, scale: '1' }], { duration: 220, easing: 'ease-out' });
      }
      return;
    }
    const dx = old.left - now.left;
    const dy = old.top - now.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    const delay = before.has(el.dataset.k) ? 0 : (entered += 1) * stagger;
    el.animate([
      { translate: `${dx}px ${dy}px`, zIndex: 50 },
      { translate: '0 0', zIndex: 50 },
    ], { duration, delay, easing: 'cubic-bezier(.2, .85, .25, 1)', fill: 'backwards' });
  });
}

/** Всплывающая надпись над точкой (очки, «Бито», «+1»). */
export function popText(host, text, { x = '50%', y = '40%', cls = '' } = {}) {
  const el = document.createElement('div');
  el.className = `pop-text ${cls}`;
  el.textContent = text;
  el.style.left = typeof x === 'number' ? `${x}px` : x;
  el.style.top = typeof y === 'number' ? `${y}px` : y;
  host.append(el);
  el.addEventListener('animationend', () => el.remove());
  return el;
}

/** Встряска элемента — удар, проигрыш. */
export function quake(el, strength = 8) {
  if (!el?.animate || reduced()) return;
  const s = strength;
  el.animate([
    { translate: '0 0' }, { translate: `${-s}px ${s / 2}px` }, { translate: `${s}px ${-s / 2}px` },
    { translate: `${-s / 2}px ${s / 3}px` }, { translate: `${s / 3}px 0` }, { translate: '0 0' },
  ], { duration: 360, easing: 'ease-out' });
}

/** Короткое «дрожание» элемента — ход невозможен. */
export function shake(el) {
  notify('error');
  sfx('error');
  if (!el?.animate) return;
  el.animate([{ translate: '0 0' }, { translate: '-6px 0' }, { translate: '6px 0' }, { translate: '-3px 0' }, { translate: '0 0' }],
    { duration: 260 });
}

/** Обновить число с «подпрыгиванием» и плавным счётом. */
export function bump(el, value, { count = false } = {}) {
  if (!el) return;
  const target = Number(value);
  const from = Number(el.dataset.v ?? el.textContent);
  el.dataset.v = String(value);
  if (count && Number.isFinite(from) && Number.isFinite(target) && from !== target && !reduced()) {
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / 500);
      el.textContent = String(Math.round(from + (target - from) * (1 - (1 - t) ** 3)));
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  } else {
    el.textContent = String(value);
  }
  if (String(from) !== String(value)) {
    el.classList.remove('bump');
    void el.offsetWidth;
    el.classList.add('bump');
  }
}

/** Конфетти: поверх всего экрана или внутри host — тогда оно летит из-за
 *  карточки итога и не закрывает её надпись. */
export function confetti(amount = 140, host = null) {
  if (reduced()) return;
  const canvas = document.createElement('canvas');
  canvas.className = host ? 'confetti inside' : 'confetti';
  const w = host ? host.clientWidth || innerWidth : innerWidth;
  const h = host ? host.clientHeight || innerHeight : innerHeight;
  if (host) host.prepend(canvas);
  else document.body.append(canvas);
  const g = fitCanvas(canvas, w, h);
  const colors = ['#ff595e', '#ffca3a', '#8ac926', '#1982c4', '#6a4c93', '#ff924c', '#ffffff'];
  const bits = Array.from({ length: amount }, (_, i) => ({
    x: w / 2 + (Math.random() - 0.5) * 80,
    y: h * (host ? 0.3 : 0.45),
    vx: (Math.random() - 0.5) * 14,
    vy: -8 - Math.random() * 12,
    r: Math.random() * Math.PI,
    vr: (Math.random() - 0.5) * 0.4,
    size: 5 + Math.random() * 6,
    color: colors[i % colors.length],
  }));
  const born = performance.now();
  const tick = (now) => {
    const age = now - born;
    g.clearRect(0, 0, w, h);
    bits.forEach((b) => {
      b.vy += 0.38;
      b.vx *= 0.99;
      b.x += b.vx;
      b.y += b.vy;
      b.r += b.vr;
      g.save();
      g.translate(b.x, b.y);
      g.rotate(b.r);
      g.globalAlpha = Math.max(0, 1 - age / 2600);
      g.fillStyle = b.color;
      g.fillRect(-b.size / 2, -b.size / 4, b.size, b.size / 2);
      g.restore();
    });
    if (age < 2600) requestAnimationFrame(tick);
    else canvas.remove();
  };
  requestAnimationFrame(tick);
}

/** Частицы на холсте: взрыв точек, которые гаснут. Возвращает рисовалку. */
export function particles() {
  const list = [];
  return {
    burst(x, y, color, count = 14, speed = 3) {
      if (reduced()) return;
      for (let i = 0; i < count; i += 1) {
        const a = Math.random() * Math.PI * 2;
        const v = speed * (0.4 + Math.random());
        list.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, life: 1, color, size: 2 + Math.random() * 3 });
      }
    },
    draw(g, dt) {
      for (let i = list.length - 1; i >= 0; i -= 1) {
        const p = list[i];
        p.x += p.vx * dt * 60;
        p.y += p.vy * dt * 60;
        p.vy += 0.08 * dt * 60;
        p.life -= dt * 1.6;
        if (p.life <= 0) {
          list.splice(i, 1);
          continue;
        }
        g.globalAlpha = p.life;
        g.fillStyle = p.color;
        g.beginPath();
        g.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2);
        g.fill();
      }
      g.globalAlpha = 1;
    },
  };
}

// ───────────── Холст и жесты ─────────────

/** Холст под размер в CSS-пикселях с учётом плотности экрана. */
export function fitCanvas(canvas, width, height) {
  const ratio = Math.min(window.devicePixelRatio || 1, 3);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return ctx;
}

export const DIRS = {
  up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0],
};

/** Свайпы по элементу и стрелки (WASD) на клавиатуре — направление. */
export function onDirection(el, signal, handler, { minSwipe = 16 } = {}) {
  let start = null;
  el.addEventListener('pointerdown', (event) => {
    start = { x: event.clientX, y: event.clientY };
  }, { signal });
  // Направление меняется ещё во время движения пальца: ждать отпускания —
  // змейка успевала въехать в стену.
  el.addEventListener('pointermove', (event) => {
    if (!start) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    if (Math.max(Math.abs(dx), Math.abs(dy)) < minSwipe) return;
    handler(Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : (dy > 0 ? 'down' : 'up'));
    start = { x: event.clientX, y: event.clientY };
  }, { signal });
  const stop = () => { start = null; };
  el.addEventListener('pointerup', stop, { signal });
  el.addEventListener('pointercancel', stop, { signal });
  onKeys(signal, {
    ArrowUp: () => handler('up'), KeyW: () => handler('up'),
    ArrowDown: () => handler('down'), KeyS: () => handler('down'),
    ArrowLeft: () => handler('left'), KeyA: () => handler('left'),
    ArrowRight: () => handler('right'), KeyD: () => handler('right'),
  });
}

/** Джойстик под пальцем: касание в любом месте area ставит основание, ведение
 *  пальца задаёт направление — угол в радианах (0 — вправо, по часовой).
 *  Палец ушёл дальше края — основание тянется за ним, поэтому развернуться
 *  можно, не отрывая пальца. На клавиатуре — стрелки и WASD, включая
 *  диагонали. Кнопки внутри area работают как обычно. */
export function joystick(area, signal, onAngle, { radius = 46, dead = 10 } = {}) {
  const base = document.createElement('div');
  base.className = 'joy';
  base.innerHTML = '<i></i>';
  base.hidden = true;
  area.append(base);
  const knob = base.firstElementChild;
  area.classList.add('joy-area');
  let touch = null;
  const place = () => {
    const rect = area.getBoundingClientRect();
    base.style.left = `${touch.ox - rect.left}px`;
    base.style.top = `${touch.oy - rect.top}px`;
  };
  area.addEventListener('pointerdown', (event) => {
    if (touch || event.target.closest('button, .game-over, .seg')) return;
    touch = { id: event.pointerId, ox: event.clientX, oy: event.clientY };
    area.setPointerCapture?.(event.pointerId);
    place();
    knob.style.translate = '0 0';
    base.hidden = false;
  }, { signal });
  area.addEventListener('pointermove', (event) => {
    if (!touch || event.pointerId !== touch.id) return;
    let dx = event.clientX - touch.ox;
    let dy = event.clientY - touch.oy;
    const dist = Math.hypot(dx, dy);
    if (dist > radius) {
      // Основание едет за пальцем.
      touch.ox = event.clientX - (dx / dist) * radius;
      touch.oy = event.clientY - (dy / dist) * radius;
      dx = event.clientX - touch.ox;
      dy = event.clientY - touch.oy;
      place();
    }
    knob.style.translate = `${dx}px ${dy}px`;
    if (Math.hypot(dx, dy) >= dead) onAngle(Math.atan2(dy, dx));
  }, { signal });
  const stop = (event) => {
    if (!touch || event.pointerId !== touch.id) return;
    touch = null;
    base.hidden = true;
  };
  area.addEventListener('pointerup', stop, { signal });
  area.addEventListener('pointercancel', stop, { signal });
  const keys = new Set();
  const fromKeys = () => {
    const x = (keys.has('right') ? 1 : 0) - (keys.has('left') ? 1 : 0);
    const y = (keys.has('down') ? 1 : 0) - (keys.has('up') ? 1 : 0);
    if (x || y) onAngle(Math.atan2(y, x));
  };
  const KEYS = {
    ArrowUp: 'up', KeyW: 'up', ArrowDown: 'down', KeyS: 'down', ArrowLeft: 'left', KeyA: 'left', ArrowRight: 'right', KeyD: 'right',
  };
  document.addEventListener('keydown', (event) => {
    const key = KEYS[event.code];
    if (!key) return;
    event.preventDefault();
    keys.add(key);
    fromKeys();
  }, { signal });
  document.addEventListener('keyup', (event) => { keys.delete(KEYS[event.code]); }, { signal });
}

/** Угол джойстика — одно из четырёх направлений (для змейки). */
export function angleToDir(angle) {
  const a = ((angle * 180) / Math.PI + 360) % 360;
  if (a >= 315 || a < 45) return 'right';
  if (a < 135) return 'down';
  if (a < 225) return 'left';
  return 'up';
}

/** Клавиши на время игры: по event.code. */
export function onKeys(signal, map) {
  document.addEventListener('keydown', (event) => {
    const action = map[event.code];
    if (!action || event.target.closest?.('input, textarea')) return;
    event.preventDefault();
    action(event);
  }, { signal });
}

/** Кадры, пока игра открыта и приложение на экране. step(dt в секундах). */
export function frames(ctx, step) {
  let last = performance.now();
  const tick = (now) => {
    if (ctx.signal.aborted) return;
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (!document.hidden) step(dt, now);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/** Пауза, которую отменяет закрытие игры: боты «думают» не мгновенно. */
export function wait(ctx, ms) {
  return new Promise((resolve, reject) => {
    if (ctx.signal.aborted) {
      reject(new Error('closed'));
      return;
    }
    const timer = setTimeout(resolve, ms);
    ctx.signal.addEventListener('abort', () => { clearTimeout(timer); reject(new Error('closed')); }, { once: true });
  });
}

// ───────────── Экраны игры ─────────────

/** Карточка поверх поля. icon — крупный эмодзи сверху; win — конфетти. */
export function overlay(host, html, actions = [], { icon = '', win = false, lose = false } = {}) {
  host.querySelector(':scope > .game-over')?.remove();
  const box = document.createElement('div');
  box.className = `game-over${win ? ' win' : ''}${lose ? ' lose' : ''}`;
  box.innerHTML = `<div class="game-over-card">${icon ? `<div class="game-over-icon">${icon}</div>` : ''}${html}
      <div class="game-over-actions">${actions.map((action, i) =>
        `<button class="game-btn ${action.outline ? 'ghost' : ''}" data-i="${i}">${action.label}</button>`).join('')}</div></div>`;
  box.addEventListener('click', (event) => {
    const button = event.target.closest('[data-i]');
    if (!button) return;
    tap();
    sfx('tap');
    box.classList.add('leaving');
    setTimeout(() => box.remove(), 180);
    actions[Number(button.dataset.i)].run();
  });
  host.append(box);
  if (win) {
    sfx('win');
    notify('success');
    confetti(140, box);
  } else if (lose) {
    sfx('lose');
    notify('error');
  }
  return box;
}

/** Вопрос поверх поля: «да» — true, «отмена» — false. */
export function ask(host, title, text, yes = 'Да') {
  return new Promise((resolve) => {
    overlay(host, `<div class="game-over-title">${title}</div><div class="game-over-text">${text}</div>`,
      [{ label: yes, run: () => resolve(true) }, { label: 'Отмена', outline: true, run: () => resolve(false) }], { icon: '🤔' });
  });
}

/** Начать заново, не спросив, можно только партию, в которой ещё ничего не
 *  сделано: иначе переспрашиваем — ход нечаянного касания не стоит. */
export const askRestart = (host) => ask(host, 'Начать заново?', 'Текущая партия пропадёт.', 'Начать заново');

/** Стартовый экран игры: крупная иконка, название, правила, рекорд. */
export function intro(host, { icon, title, text, record = '', button = 'Играть', run, extra = '' }) {
  return overlay(host, `<div class="game-over-title big">${title}</div>
      <div class="game-over-text">${text}</div>${record ? `<div class="game-record">🏆 ${record}</div>` : ''}${extra}`,
  [{ label: button, run }], { icon });
}

/** Строка показателей над полем: [[ключ, подпись, значение], …]. */
export function hud(items) {
  return `<div class="hud">${items.map(([key, label, value]) =>
    `<div class="hud-pill"><span>${label}</span><b data-${key}>${value}</b></div>`).join('')}</div>`;
}

/** Выбор из вариантов: [[значение, подпись], …]. */
export function chipsRow(name, options, current) {
  return `<div class="seg" data-choice="${name}" style="--n:${options.length};--i:${Math.max(0, options.findIndex(([v]) => v === current))}">
      <span class="seg-pill" aria-hidden="true"></span>${options.map(([value, label]) =>
    `<button class="${value === current ? 'on' : ''}" data-value="${value}">${label}</button>`).join('')}</div>`;
}

/** Выбор варианта. guard() — можно ли переключиться (true или Promise с
 *  ответом): подсветка переезжает только после согласия. */
export function bindChips(root, name, onPick, { guard = null } = {}) {
  const row = root.querySelector(`[data-choice="${name}"]`);
  row?.addEventListener('click', async (event) => {
    const chip = event.target.closest('[data-value]');
    if (!chip || chip.classList.contains('on')) return;
    if (guard && !(await guard())) return;
    select();
    sfx('tap');
    const buttons = [...row.querySelectorAll('[data-value]')];
    buttons.forEach((other) => other.classList.toggle('on', other === chip));
    row.style.setProperty('--i', buttons.indexOf(chip));
    onPick(chip.dataset.value);
  });
}

/** Цвет из темы приложения — для рисования на холсте. */
export function themeColor(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** Светлее (amount > 0) или темнее цвет #rrggbb. */
export function shade(hex, amount) {
  const value = parseInt(hex.slice(1), 16);
  const target = amount > 0 ? 255 : 0;
  return `#${[value >> 16, (value >> 8) & 255, value & 255]
    .map((c) => Math.round(c + (target - c) * Math.abs(amount)).toString(16).padStart(2, '0')).join('')}`;
}
