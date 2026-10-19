// Тетрис: поле 10×20, «мешок» из семи фигур, три следующие фигуры и запас
// (фигуру можно отложить). Касание поля — поворот, ведение пальцем вбок —
// сдвиг, резкий свайп вниз — сбросить, вверх — отложить. Кнопки снизу
// дублируют жесты. Собранные линии вспыхивают и рассыпаются.
'use strict';

import {
  best, bump, fitCanvas, frames, hud, intro, notify, onKeys, overlay, particles, popText, quake, saveBest, sfx, shade,
  shuffle, tap,
} from './kit.js';

const W = 10;
const H = 20;
export const SHAPES = {
  I: { color: '#2fd4e0', cells: [[0, 1], [1, 1], [2, 1], [3, 1]] },
  O: { color: '#ffd23f', cells: [[1, 0], [2, 0], [1, 1], [2, 1]] },
  T: { color: '#b45cf5', cells: [[1, 0], [0, 1], [1, 1], [2, 1]] },
  S: { color: '#4cd964', cells: [[1, 0], [2, 0], [0, 1], [1, 1]] },
  Z: { color: '#ff4d5e', cells: [[0, 0], [1, 0], [1, 1], [2, 1]] },
  J: { color: '#3d7bff', cells: [[0, 0], [0, 1], [1, 1], [2, 1]] },
  L: { color: '#ff9a2e', cells: [[2, 0], [0, 1], [1, 1], [2, 1]] },
};
const LINE_SCORE = [0, 100, 300, 500, 800];
const LINE_WORDS = ['', '', 'Двойная!', 'Тройная!', 'TETRIS!'];
const CLEAR_MS = 260;

/** Поворот по часовой внутри квадрата фигуры (4×4 у I, 3×3 у остальных). */
export function rotate(cells, kind) {
  if (kind === 'O') return cells;
  const n = kind === 'I' ? 4 : 3;
  return cells.map(([x, y]) => [n - 1 - y, x]);
}

/** Заполненные строки убираются, сверху добавляются пустые. */
export function clearLines(board) {
  const kept = board.filter((row) => row.some((cell) => !cell));
  const cleared = board.length - kept.length;
  while (kept.length < board.length) kept.unshift(Array(board[0].length).fill(null));
  return { board: kept, cleared };
}

/** Блок с объёмом: светлая кромка сверху-слева, тень снизу-справа. */
function block(g, x, y, s, color, alpha = 1) {
  g.globalAlpha = alpha;
  const r = s * 0.16;
  g.fillStyle = shade(color, -0.35);
  g.beginPath();
  g.roundRect(x + 1, y + 1, s - 2, s - 2, r);
  g.fill();
  const grad = g.createLinearGradient(x, y, x + s, y + s);
  grad.addColorStop(0, shade(color, 0.3));
  grad.addColorStop(1, color);
  g.fillStyle = grad;
  g.beginPath();
  g.roundRect(x + 1, y + 1, s - 2, s - s * 0.14 - 1, r);
  g.fill();
  g.fillStyle = 'rgba(255,255,255,.35)';
  g.beginPath();
  g.roundRect(x + s * 0.18, y + s * 0.14, s * 0.4, s * 0.12, s * 0.06);
  g.fill();
  g.globalAlpha = 1;
}

export function start(root, ctx) {
  root.innerHTML = `<div class="game">
      ${hud([['score', 'Очки', 0], ['lines', 'Линии', 0], ['level', 'Уровень', 1]])}
      <div class="tetris-wrap">
        <div class="game-stage tetris-stage"><canvas class="game-canvas" data-board></canvas></div>
        <div class="tetris-side">
          <div class="tetris-box"><span>Дальше</span><canvas data-next></canvas></div>
          <div class="tetris-box hold" data-act="hold"><span>Запас</span><canvas data-hold></canvas></div>
          <div class="tetris-box"><span>Рекорд</span><b data-best>${best('tetris')}</b></div>
        </div>
      </div>
      <div class="tetris-pad game-pad">
        <button class="t-hold" data-act="hold" aria-label="Отложить фигуру">⇄<small>запас</small></button>
        <button class="t-rotate" data-act="rotate" aria-label="Повернуть">⟳<small>повернуть</small></button>
        <button class="t-drop" data-act="drop" aria-label="Сбросить">⤓<small>сброс</small></button>
        <button class="t-left" data-act="left" aria-label="Влево"><i class="arrow l"></i></button>
        <button class="t-down" data-act="down" aria-label="Вниз"><i class="arrow d"></i></button>
        <button class="t-right" data-act="right" aria-label="Вправо"><i class="arrow r"></i></button>
      </div></div>`;
  const stage = root.querySelector('.game-stage');
  const canvas = root.querySelector('[data-board]');
  // Поле рядом с боковой колонкой (68px), кнопки снизу — два ряда по 66px.
  const available = Math.min((root.querySelector('.tetris-wrap').clientWidth || 340) - 76, 340);
  const cell = Math.floor(Math.min(available / W, (innerHeight - 330) / H, 34));
  const bw = cell * W;
  const bh = cell * H;
  const g = fitCanvas(canvas, bw, bh);
  const next = fitCanvas(root.querySelector('[data-next]'), 52, 130);
  const hold = fitCanvas(root.querySelector('[data-hold]'), 52, 40);
  // Касание окошка «Запас» — тоже отложить фигуру.
  root.querySelector('.tetris-box.hold').addEventListener('click', () => swapHold());
  const fx = particles();

  let board;
  let piece;
  let bag = [];
  let queue = [];
  let held = null;
  let canHold = true;
  let score = 0;
  let lines = 0;
  let level = 1;
  let running = false;
  let acc = 0;
  let clearing = null;
  let dropTrail = null;

  const take = () => {
    if (!bag.length) bag = shuffle(Object.keys(SHAPES));
    return bag.pop();
  };
  const fits = (cells, px, py) => cells.every(([x, y]) => {
    const bx = px + x;
    const by = py + y;
    return bx >= 0 && bx < W && by < H && (by < 0 || !board[by][bx]);
  });

  function spawn(kind) {
    while (queue.length < 4) queue.push(take());
    kind = kind || queue.shift();
    piece = { kind, cells: SHAPES[kind].cells.map((c) => [...c]), x: 3, y: kind === 'I' ? -1 : 0 };
    if (!fits(piece.cells, piece.x, piece.y)) gameOver();
  }

  const interval = () => Math.max(0.06, 0.8 * 0.86 ** (level - 1));

  function lock() {
    piece.cells.forEach(([x, y]) => {
      if (piece.y + y >= 0) board[piece.y + y][piece.x + x] = SHAPES[piece.kind].color;
    });
    canHold = true;
    const full = board.map((row, y) => (row.every(Boolean) ? y : -1)).filter((y) => y >= 0);
    if (full.length) {
      // Линии вспыхивают, рассыпаются искрами — и только потом исчезают.
      clearing = { rows: full, at: performance.now() };
      full.forEach((y) => board[y].forEach((color, x) => fx.burst((x + 0.5) * cell, (y + 0.5) * cell, color, 3, 2.5)));
      const gained = LINE_SCORE[full.length] * level;
      score += gained;
      lines += full.length;
      const newLevel = Math.floor(lines / 10) + 1;
      sfx(full.length >= 4 ? 'tetris' : 'line');
      notify(full.length >= 4 ? 'success' : 'warning');
      popText(stage, LINE_WORDS[full.length] || `+${gained}`, { y: (full[0] / H) * 100 + '%', cls: full.length >= 4 ? 'gold' : '' });
      if (full.length >= 4) quake(stage, 6);
      if (newLevel > level) {
        level = newLevel;
        setTimeout(() => { popText(stage, `Уровень ${level}`, { y: '30%', cls: 'gold' }); sfx('level'); }, 350);
      }
      piece = null;
      setTimeout(() => {
        if (!running) return;
        board = clearLines(board).board;
        clearing = null;
        spawn();
      }, CLEAR_MS);
    } else {
      sfx('land');
      tap('soft');
      spawn();
    }
    bump(root.querySelector('[data-score]'), score, { count: true });
    bump(root.querySelector('[data-lines]'), lines);
    bump(root.querySelector('[data-level]'), level);
  }

  function move(dx, dy) {
    if (!running || !piece) return false;
    if (fits(piece.cells, piece.x + dx, piece.y + dy)) {
      piece.x += dx;
      piece.y += dy;
      if (dx) sfx('move');
      return true;
    }
    if (dy) lock();
    return false;
  }

  function turn() {
    if (!running || !piece) return;
    const cells = rotate(piece.cells, piece.kind);
    // Простые «отскоки» от стен и соседей.
    for (const [kx, ky] of [[0, 0], [-1, 0], [1, 0], [-2, 0], [2, 0], [0, -1]]) {
      if (fits(cells, piece.x + kx, piece.y + ky)) {
        piece.cells = cells;
        piece.x += kx;
        piece.y += ky;
        sfx('rotate');
        tap('soft');
        return;
      }
    }
  }

  function drop() {
    if (!running || !piece) return;
    const from = piece.y;
    while (fits(piece.cells, piece.x, piece.y + 1)) piece.y += 1;
    score += (piece.y - from) * 2;
    dropTrail = { cells: piece.cells.map(([x, y]) => [piece.x + x, from + y, piece.y + y]), color: SHAPES[piece.kind].color, life: 1 };
    sfx('drop');
    quake(stage, 3);
    lock();
  }

  function swapHold() {
    if (!running || !piece || !canHold) return;
    const kind = piece.kind;
    canHold = false;
    sfx('rotate');
    tap('light');
    if (held) spawn(held);
    else spawn();
    held = kind;
  }

  function gameOver() {
    running = false;
    piece = null;
    const record = saveBest('tetris', score);
    bump(root.querySelector('[data-best]'), best('tetris'));
    quake(stage, 8);
    overlay(stage, `<div class="game-over-title">${record ? 'Новый рекорд!' : 'Конец игры'}</div>
        <div class="game-over-text">Очки: <b>${score}</b> · линии: <b>${lines}</b> · уровень: <b>${level}</b></div>`,
    [{ label: 'Ещё раз', run: begin }], { icon: record ? '🏆' : '🧱', win: record, lose: !record });
  }

  function preview(c2d, kind, x, y, s) {
    const shape = SHAPES[kind];
    const xs = shape.cells.map(([cx]) => cx);
    const ys = shape.cells.map(([, cy]) => cy);
    const w = (Math.max(...xs) - Math.min(...xs) + 1) * s;
    const h = (Math.max(...ys) - Math.min(...ys) + 1) * s;
    shape.cells.forEach(([cx, cy]) => block(c2d, x - w / 2 + (cx - Math.min(...xs)) * s, y - h / 2 + (cy - Math.min(...ys)) * s, s, shape.color));
  }

  function draw(dt) {
    const grad = g.createLinearGradient(0, 0, 0, bh);
    grad.addColorStop(0, '#1b1640');
    grad.addColorStop(1, '#0d0b22');
    g.fillStyle = grad;
    g.fillRect(0, 0, bw, bh);
    g.strokeStyle = 'rgba(255,255,255,.05)';
    g.lineWidth = 1;
    for (let x = 1; x < W; x += 1) { g.beginPath(); g.moveTo(x * cell + 0.5, 0); g.lineTo(x * cell + 0.5, bh); g.stroke(); }
    for (let y = 1; y < H; y += 1) { g.beginPath(); g.moveTo(0, y * cell + 0.5); g.lineTo(bw, y * cell + 0.5); g.stroke(); }
    const flashing = clearing ? Math.min(1, (performance.now() - clearing.at) / CLEAR_MS) : 0;
    board.forEach((row, y) => row.forEach((color, x) => {
      if (!color) return;
      if (clearing?.rows.includes(y)) {
        // Линия белеет и сжимается к середине.
        const shrink = flashing * cell * 0.5;
        g.fillStyle = `rgba(255,255,255,${1 - flashing * 0.6})`;
        g.fillRect(x * cell + 1, y * cell + 1 + shrink, cell - 2, Math.max(0, cell - 2 - shrink * 2));
      } else {
        block(g, x * cell, y * cell, cell, color);
      }
    }));
    if (dropTrail && dropTrail.life > 0) {
      dropTrail.cells.forEach(([x, from, to]) => {
        const grd = g.createLinearGradient(0, from * cell, 0, to * cell);
        grd.addColorStop(0, 'rgba(255,255,255,0)');
        grd.addColorStop(1, `rgba(255,255,255,${0.25 * dropTrail.life})`);
        g.fillStyle = grd;
        g.fillRect(x * cell + 2, from * cell, cell - 4, (to - from) * cell);
      });
      dropTrail.life -= dt * 4;
    }
    if (piece && running) {
      // Тень — куда фигура упадёт.
      let ghost = piece.y;
      while (fits(piece.cells, piece.x, ghost + 1)) ghost += 1;
      g.strokeStyle = SHAPES[piece.kind].color;
      g.globalAlpha = 0.5;
      g.lineWidth = 1.5;
      piece.cells.forEach(([x, y]) => {
        g.beginPath();
        g.roundRect((piece.x + x) * cell + 2, (ghost + y) * cell + 2, cell - 4, cell - 4, cell * 0.15);
        g.stroke();
      });
      g.globalAlpha = 1;
      piece.cells.forEach(([x, y]) => block(g, (piece.x + x) * cell, (piece.y + y) * cell, cell, SHAPES[piece.kind].color));
    }
    fx.draw(g, dt);
    next.clearRect(0, 0, 52, 130);
    queue.slice(0, 3).forEach((kind, i) => preview(next, kind, 26, 22 + i * 42, i ? 10 : 12));
    hold.clearRect(0, 0, 52, 40);
    if (held) {
      hold.globalAlpha = canHold ? 1 : 0.35;
      preview(hold, held, 26, 20, 11);
      hold.globalAlpha = 1;
    }
  }

  function begin() {
    board = Array.from({ length: H }, () => Array(W).fill(null));
    bag = [];
    queue = [];
    held = null;
    canHold = true;
    clearing = null;
    score = 0;
    lines = 0;
    level = 1;
    acc = 0;
    ['score', 'lines'].forEach((key) => bump(root.querySelector(`[data-${key}]`), 0));
    bump(root.querySelector('[data-level]'), 1);
    running = true;
    spawn();
  }

  // Жесты на поле: касание — поворот, ведение вбок — сдвиг по клеткам,
  // быстрый свайп вниз — сброс, вверх — отложить.
  let touch = null;
  canvas.addEventListener('pointerdown', (event) => {
    touch = { x: event.clientX, y: event.clientY, startY: event.clientY, t: performance.now(), moved: false };
  }, { signal: ctx.signal });
  canvas.addEventListener('pointermove', (event) => {
    if (!touch) return;
    const dx = event.clientX - touch.x;
    if (Math.abs(dx) >= cell * 0.9) {
      move(Math.sign(dx), 0);
      touch.x += Math.sign(dx) * cell * 0.9;
      touch.moved = true;
    }
    const dy = event.clientY - touch.y;
    if (dy >= cell && performance.now() - touch.t > 180) {
      if (move(0, 1)) score += 1;
      touch.y += cell;
      touch.moved = true;
    }
  }, { signal: ctx.signal });
  canvas.addEventListener('pointerup', (event) => {
    if (!touch) return;
    const dy = event.clientY - touch.startY;
    const fast = performance.now() - touch.t < 260;
    if (fast && dy > cell * 2.5) drop();
    else if (fast && dy < -cell * 2) swapHold();
    else if (!touch.moved && Math.abs(dy) < 10) turn();
    touch = null;
  }, { signal: ctx.signal });
  canvas.addEventListener('pointercancel', () => { touch = null; }, { signal: ctx.signal });

  const actions = {
    left: () => move(-1, 0),
    right: () => move(1, 0),
    rotate: turn,
    down: () => { if (move(0, 1)) score += 1; },
    drop,
    hold: swapHold,
  };
  // Удержание кнопки сдвига повторяет ход.
  let repeat = 0;
  const pad = root.querySelector('.tetris-pad');
  pad.addEventListener('pointerdown', (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    event.preventDefault();
    tap('soft');
    const act = actions[button.dataset.act];
    act();
    if (['left', 'right', 'down'].includes(button.dataset.act)) {
      clearInterval(repeat);
      const started = Date.now();
      repeat = setInterval(() => { if (Date.now() - started > 200) act(); }, 55);
    }
  }, { signal: ctx.signal });
  const stopRepeat = () => clearInterval(repeat);
  ['pointerup', 'pointercancel', 'pointerleave'].forEach((type) => pad.addEventListener(type, stopRepeat, { signal: ctx.signal }));
  ctx.signal.addEventListener('abort', stopRepeat);
  onKeys(ctx.signal, {
    ArrowLeft: actions.left, ArrowRight: actions.right, ArrowUp: turn, ArrowDown: actions.down, Space: drop, KeyC: swapHold,
  });

  frames(ctx, (dt) => {
    if (running && piece && !clearing) {
      acc += dt;
      if (acc >= interval()) {
        acc = 0;
        move(0, 1);
      }
    }
    draw(dt);
  });
  board = Array.from({ length: H }, () => Array(W).fill(null));
  intro(stage, {
    icon: '🧱', title: 'Тетрис',
    text: 'Касание — поворот, вбок — сдвиг, свайп вниз — сбросить, вверх — отложить фигуру.',
    record: best('tetris') ? `Рекорд: ${best('tetris')}` : '', run: begin,
  });
}
