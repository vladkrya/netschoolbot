// Тетрис: поле 10×20, «мешок» из семи фигур, следующая фигура видна.
// Касание поля — поворот, ведение пальцем вбок — сдвиг, резкий свайп вниз —
// сбросить фигуру. Кнопки снизу дублируют жесты.
'use strict';

import { best, fitCanvas, frames, notify, onKeys, overlay, saveBest, shuffle, tap, themeColor } from './kit.js';

const W = 10;
const H = 20;
export const SHAPES = {
  I: { color: '#3cc7d6', cells: [[0, 1], [1, 1], [2, 1], [3, 1]] },
  O: { color: '#f2c94c', cells: [[1, 0], [2, 0], [1, 1], [2, 1]] },
  T: { color: '#a46ee0', cells: [[1, 0], [0, 1], [1, 1], [2, 1]] },
  S: { color: '#5cc26a', cells: [[1, 0], [2, 0], [0, 1], [1, 1]] },
  Z: { color: '#e5484d', cells: [[0, 0], [1, 0], [1, 1], [2, 1]] },
  J: { color: '#3b6fc4', cells: [[0, 0], [0, 1], [1, 1], [2, 1]] },
  L: { color: '#f08a24', cells: [[2, 0], [0, 1], [1, 1], [2, 1]] },
};
const LINE_SCORE = [0, 100, 300, 500, 800];

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

export function start(root, ctx) {
  root.innerHTML = `<div class="game">
      <div class="game-stats"><span>Счёт: <b data-score>0</b></span><span>Линии: <b data-lines>0</b></span><span>Уровень: <b data-level>1</b></span></div>
      <div class="tetris-wrap">
        <div class="game-stage"><canvas class="game-canvas" data-board></canvas></div>
        <div class="tetris-side"><div class="muted small">Дальше</div><canvas data-next></canvas>
          <div class="muted small">Рекорд</div><b data-best>${best('tetris')}</b></div>
      </div>
      <div class="game-pad">
        <button data-act="left" aria-label="Влево">◀</button>
        <button data-act="rotate" aria-label="Повернуть">⟳</button>
        <button data-act="down" aria-label="Вниз">▼</button>
        <button data-act="drop" aria-label="Сбросить">⤓</button>
        <button data-act="right" aria-label="Вправо">▶</button>
      </div></div>`;
  const stage = root.querySelector('.game-stage');
  const canvas = root.querySelector('[data-board]');
  const available = Math.min((root.clientWidth || 340) - 90, 300);
  const cell = Math.floor(Math.min(available / W, (innerHeight - 330) / H, 30));
  const g = fitCanvas(canvas, cell * W, cell * H);
  const next = fitCanvas(root.querySelector('[data-next]'), 64, 48);
  const colors = { bg: themeColor('--card', '#fff'), grid: themeColor('--border', '#ddd') };

  let board;
  let piece;
  let bag = [];
  let queue = [];
  let score = 0;
  let lines = 0;
  let level = 1;
  let running = false;
  let acc = 0;

  const take = () => {
    if (!bag.length) bag = shuffle(Object.keys(SHAPES));
    return bag.pop();
  };
  const fits = (cells, px, py) => cells.every(([x, y]) => {
    const bx = px + x;
    const by = py + y;
    return bx >= 0 && bx < W && by < H && (by < 0 || !board[by][bx]);
  });

  function spawn() {
    while (queue.length < 2) queue.push(take());
    const kind = queue.shift();
    piece = { kind, cells: SHAPES[kind].cells.map((c) => [...c]), x: 3, y: kind === 'I' ? -1 : 0 };
    if (!fits(piece.cells, piece.x, piece.y)) gameOver();
  }

  const interval = () => Math.max(0.08, 0.8 - (level - 1) * 0.07);

  function lock() {
    piece.cells.forEach(([x, y]) => {
      if (piece.y + y >= 0) board[piece.y + y][piece.x + x] = SHAPES[piece.kind].color;
    });
    const result = clearLines(board);
    board = result.board;
    if (result.cleared) {
      lines += result.cleared;
      score += LINE_SCORE[result.cleared] * level;
      level = Math.floor(lines / 10) + 1;
      notify(result.cleared >= 4 ? 'success' : 'warning');
    } else {
      tap('soft');
    }
    root.querySelector('[data-score]').textContent = String(score);
    root.querySelector('[data-lines]').textContent = String(lines);
    root.querySelector('[data-level]').textContent = String(level);
    spawn();
  }

  function move(dx, dy) {
    if (!running) return false;
    if (fits(piece.cells, piece.x + dx, piece.y + dy)) {
      piece.x += dx;
      piece.y += dy;
      return true;
    }
    if (dy) lock();
    return false;
  }

  function turn() {
    if (!running) return;
    const cells = rotate(piece.cells, piece.kind);
    // Простые «отскоки» от стен и соседей.
    for (const kick of [0, -1, 1, -2, 2]) {
      if (fits(cells, piece.x + kick, piece.y)) {
        piece.cells = cells;
        piece.x += kick;
        tap('soft');
        return;
      }
    }
  }

  function drop() {
    if (!running) return;
    let fell = 0;
    while (fits(piece.cells, piece.x, piece.y + 1)) {
      piece.y += 1;
      fell += 1;
    }
    score += fell * 2;
    lock();
  }

  function gameOver() {
    running = false;
    notify('error');
    const record = saveBest('tetris', score);
    if (record) root.querySelector('[data-best]').textContent = String(score);
    overlay(stage, `<div class="game-over-title">${record ? 'Новый рекорд!' : 'Конец игры'}</div>
        <div class="muted">Очки: ${score} · линии: ${lines}</div>`, [{ label: 'Ещё раз', run: begin }]);
  }

  function block(ctx2d, x, y, size, color, alpha = 1) {
    ctx2d.globalAlpha = alpha;
    ctx2d.fillStyle = color;
    ctx2d.beginPath();
    ctx2d.roundRect(x + 1, y + 1, size - 2, size - 2, size * 0.18);
    ctx2d.fill();
    ctx2d.globalAlpha = 1;
  }

  function draw() {
    g.fillStyle = colors.bg;
    g.fillRect(0, 0, cell * W, cell * H);
    g.strokeStyle = colors.grid;
    g.globalAlpha = 0.3;
    for (let x = 1; x < W; x += 1) { g.beginPath(); g.moveTo(x * cell, 0); g.lineTo(x * cell, cell * H); g.stroke(); }
    for (let y = 1; y < H; y += 1) { g.beginPath(); g.moveTo(0, y * cell); g.lineTo(cell * W, y * cell); g.stroke(); }
    g.globalAlpha = 1;
    board.forEach((row, y) => row.forEach((color, x) => color && block(g, x * cell, y * cell, cell, color)));
    if (piece && running) {
      // Тень — куда фигура упадёт.
      let ghost = piece.y;
      while (fits(piece.cells, piece.x, ghost + 1)) ghost += 1;
      piece.cells.forEach(([x, y]) => block(g, (piece.x + x) * cell, (ghost + y) * cell, cell, SHAPES[piece.kind].color, 0.22));
      piece.cells.forEach(([x, y]) => block(g, (piece.x + x) * cell, (piece.y + y) * cell, cell, SHAPES[piece.kind].color));
    }
    next.clearRect(0, 0, 64, 48);
    if (queue[0]) {
      const shape = SHAPES[queue[0]];
      shape.cells.forEach(([x, y]) => block(next, 6 + x * 13, 10 + y * 13, 13, shape.color));
    }
  }

  function begin() {
    board = Array.from({ length: H }, () => Array(W).fill(null));
    bag = [];
    queue = [];
    score = 0;
    lines = 0;
    level = 1;
    acc = 0;
    ['score', 'lines'].forEach((key) => { root.querySelector(`[data-${key}]`).textContent = '0'; });
    root.querySelector('[data-level]').textContent = '1';
    running = true;
    spawn();
  }

  // Жесты на поле: касание — поворот, ведение вбок — сдвиг по клеткам,
  // быстрый свайп вниз — сброс.
  let touch = null;
  canvas.addEventListener('pointerdown', (event) => {
    touch = { x: event.clientX, y: event.clientY, startY: event.clientY, t: performance.now(), moved: false };
  }, { signal: ctx.signal });
  canvas.addEventListener('pointermove', (event) => {
    if (!touch) return;
    const dx = event.clientX - touch.x;
    if (Math.abs(dx) >= cell) {
      move(Math.sign(dx), 0);
      touch.x += Math.sign(dx) * cell;
      touch.moved = true;
    }
    const dy = event.clientY - touch.y;
    if (dy >= cell * 1.2 && performance.now() - touch.t > 160) {
      move(0, 1);
      touch.y += cell;
      touch.moved = true;
    }
  }, { signal: ctx.signal });
  canvas.addEventListener('pointerup', (event) => {
    if (!touch) return;
    const dy = event.clientY - touch.startY;
    const fast = performance.now() - touch.t < 250;
    if (fast && dy > cell * 2.5) drop();
    else if (!touch.moved && Math.abs(dy) < 10) turn();
    touch = null;
  }, { signal: ctx.signal });
  canvas.addEventListener('pointercancel', () => { touch = null; }, { signal: ctx.signal });

  const actions = {
    left: () => move(-1, 0), right: () => move(1, 0), rotate: turn, down: () => { if (move(0, 1)) score += 1; }, drop,
  };
  // Удержание кнопки сдвига повторяет ход.
  let repeat = 0;
  const pad = root.querySelector('.game-pad');
  pad.addEventListener('pointerdown', (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    event.preventDefault();
    const act = actions[button.dataset.act];
    act();
    if (['left', 'right', 'down'].includes(button.dataset.act)) {
      clearInterval(repeat);
      const started = Date.now();
      repeat = setInterval(() => { if (Date.now() - started > 220) act(); }, 60);
    }
  }, { signal: ctx.signal });
  const stopRepeat = () => clearInterval(repeat);
  ['pointerup', 'pointercancel', 'pointerleave'].forEach((type) => pad.addEventListener(type, stopRepeat, { signal: ctx.signal }));
  ctx.signal.addEventListener('abort', stopRepeat);
  onKeys(ctx.signal, {
    ArrowLeft: actions.left, ArrowRight: actions.right, ArrowUp: turn, ArrowDown: actions.down, Space: drop,
  });

  frames(ctx, (dt) => {
    if (running) {
      acc += dt;
      if (acc >= interval()) {
        acc = 0;
        move(0, 1);
      }
    }
    draw();
  });
  board = Array.from({ length: H }, () => Array(W).fill(null));
  overlay(stage, '<div class="game-over-title">Тетрис</div><div class="muted">Касание — поворот, вбок — сдвиг, свайп вниз — сброс.</div>',
    [{ label: 'Играть', run: begin }]);
}
