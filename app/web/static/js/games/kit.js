// Общее для игр: рекорды, игровые фишки, колода, холст, жесты.
//
// Игры работают без сети: всё хранится в localStorage этого устройства,
// соперники — боты. Модули игр качаются заранее (games.js, warmGames).
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

// ───────────── Карты ─────────────
// Карта — { r, s }: r — достоинство числом (туз — 1 в пасьянсе, 14 в
// остальных играх), s — масть.

export const SUITS = ['♠', '♥', '♦', '♣'];
export const isRed = (card) => card.s === '♥' || card.s === '♦';
const FACES = { 1: 'A', 11: 'J', 12: 'Q', 13: 'K', 14: 'A' };
export const rankLabel = (r) => FACES[r] || String(r);

export function deck(from, to, copies = 1) {
  const cards = [];
  for (let copy = 0; copy < copies; copy += 1) {
    for (const s of SUITS) for (let r = from; r <= to; r += 1) cards.push({ r, s });
  }
  return shuffle(cards);
}

/** Карта разметкой. attrs — дополнительные атрибуты (data-*, class). */
export function cardHtml(card, { attrs = '', cls = '' } = {}) {
  if (!card) return `<span class="pc empty ${cls}" ${attrs}></span>`;
  if (card.down) return `<span class="pc back ${cls}" ${attrs}></span>`;
  return `<span class="pc ${isRed(card) ? 'red' : ''} ${cls}" ${attrs}>
      <b>${rankLabel(card.r)}</b><i>${card.s}</i><em>${card.s}</em></span>`;
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
export function onDirection(el, signal, handler, { minSwipe = 18 } = {}) {
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
    const timer = setTimeout(resolve, ms);
    ctx.signal.addEventListener('abort', () => { clearTimeout(timer); reject(new Error('closed')); }, { once: true });
  });
}

/** Карточка поверх поля: начало игры, проигрыш, победа. */
export function overlay(host, html, actions = []) {
  host.querySelector(':scope > .game-over')?.remove();
  const box = document.createElement('div');
  box.className = 'game-over';
  box.innerHTML = `<div class="game-over-card">${html}
      <div class="game-over-actions">${actions.map((action, i) =>
        `<button class="btn ${action.outline ? 'outline' : ''}" data-i="${i}">${action.label}</button>`).join('')}</div></div>`;
  box.addEventListener('click', (event) => {
    const button = event.target.closest('[data-i]');
    if (!button) return;
    tap();
    box.remove();
    actions[Number(button.dataset.i)].run();
  });
  host.append(box);
  return box;
}

/** Выбор из вариантов чипами: [[значение, подпись], …]. */
export function chipsRow(name, options, current) {
  return `<div class="chips game-chips" data-choice="${name}">${options.map(([value, label]) =>
    `<button class="chip ${value === current ? 'active' : ''}" data-value="${value}">${label}</button>`).join('')}</div>`;
}

export function bindChips(root, name, onPick) {
  const row = root.querySelector(`[data-choice="${name}"]`);
  row?.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-value]');
    if (!chip) return;
    select();
    row.querySelectorAll('.chip').forEach((other) => other.classList.toggle('active', other === chip));
    onPick(chip.dataset.value);
  });
}

/** Цвет из темы приложения — для рисования на холсте. */
export function themeColor(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** Короткое «дрожание» элемента — ход невозможен. */
export function shake(el) {
  if (!el?.animate) return;
  notify('error');
  el.animate([{ transform: 'translateX(0)' }, { transform: 'translateX(-5px)' }, { transform: 'translateX(5px)' },
    { transform: 'translateX(0)' }], { duration: 220 });
}
