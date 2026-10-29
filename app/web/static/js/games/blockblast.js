// Block Blast: поле 8×8 и три фигуры. Фигуру перетаскивают на поле (поворачивать
// нельзя); заполненные строки и столбцы сгорают. Подряд сгоревшие линии дают
// серию с повышенными очками. Партия кончается, когда ни одна из трёх фигур
// не помещается. Начатая партия сохраняется. Рекламы нет.
'use strict';

import {
  ask, best, bump, fitCanvas, frames, hud, load, overlay, particles, popText, rand, save, saveBest, sfx, shade, tap,
} from './kit.js';

const N = 8;
const COLORS = ['#ff4d5e', '#ff9a2e', '#ffd23f', '#4cd964', '#2fd4e0', '#3d7bff', '#b45cf5', '#ff6bb5'];

// Фигуры в виде строк: «#» — блок. Поворотов нет, как в оригинале, поэтому
// горизонтальные и вертикальные палочки, углы и «зигзаги» перечислены отдельно.
const RAW = [
  ['#'],
  ['##'], ['#', '#'],
  ['###'], ['#', '#', '#'],
  ['####'], ['#', '#', '#', '#'],
  ['#####'], ['#', '#', '#', '#', '#'],
  ['##', '##'],
  ['###', '###'], ['##', '##', '##'],
  ['###', '###', '###'],
  ['#.', '##'], ['.#', '##'], ['##', '#.'], ['##', '.#'],
  ['#..', '#..', '###'], ['..#', '..#', '###'], ['###', '#..', '#..'], ['###', '..#', '..#'],
  ['#.', '#.', '##'], ['.#', '.#', '##'], ['##', '#.', '#.'], ['##', '.#', '.#'],
  ['#..', '###'], ['..#', '###'], ['###', '#..'], ['###', '..#'],
  ['.#.', '###'], ['###', '.#.'], ['#.', '##', '#.'], ['.#', '##', '.#'],
  ['.##', '##.'], ['##.', '.##'], ['#.', '##', '.#'], ['.#', '##', '#.'],
];
export const SHAPES = RAW.map((rows) => rows.flatMap((row, y) => [...row].map((ch, x) => (ch === '#' ? [x, y] : null)).filter(Boolean)));

const size = (cells) => [Math.max(...cells.map(([x]) => x)) + 1, Math.max(...cells.map(([, y]) => y)) + 1];

export const emptyBoard = () => Array.from({ length: N }, () => Array(N).fill(null));

export function fits(board, cells, gx, gy) {
  return cells.every(([x, y]) => {
    const bx = gx + x;
    const by = gy + y;
    return bx >= 0 && bx < N && by >= 0 && by < N && !board[by][bx];
  });
}

export const canPlaceAnywhere = (board, cells) => {
  for (let gy = 0; gy < N; gy += 1) {
    for (let gx = 0; gx < N; gx += 1) if (fits(board, cells, gx, gy)) return true;
  }
  return false;
};

/** Полные строки и столбцы доски: { rows: [...], cols: [...] }. */
export function fullLines(board) {
  const rows = [];
  const cols = [];
  for (let i = 0; i < N; i += 1) {
    if (board[i].every(Boolean)) rows.push(i);
    if (board.every((row) => row[i])) cols.push(i);
  }
  return { rows, cols };
}

/** Три новые фигуры; хотя бы одна из них обязательно помещается на доску. */
export function dealPieces(board) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const pieces = Array.from({ length: 3 }, () => ({ shape: rand(SHAPES.length), color: COLORS[rand(COLORS.length)] }));
    if (pieces.some((piece) => canPlaceAnywhere(board, SHAPES[piece.shape]))) return pieces;
  }
  return [{ shape: 0, color: COLORS[0] }, { shape: 0, color: COLORS[1] }, { shape: 0, color: COLORS[2] }];
}

/** Очки за ход: блоки фигуры, линии и серия подряд сгоревших ходов. */
export const moveScore = (cells, lines, streak) => cells + (lines ? lines * 10 * (lines + streak) : 0);

function block(g, x, y, s, color, alpha = 1) {
  g.globalAlpha = alpha;
  g.fillStyle = shade(color, -0.35);
  g.beginPath();
  g.roundRect(x + 1, y + 1, s - 2, s - 2, s * 0.18);
  g.fill();
  const grad = g.createLinearGradient(x, y, x, y + s);
  grad.addColorStop(0, shade(color, 0.3));
  grad.addColorStop(1, color);
  g.fillStyle = grad;
  g.beginPath();
  g.roundRect(x + 1, y + 1, s - 2, s - s * 0.12 - 1, s * 0.18);
  g.fill();
  g.fillStyle = 'rgba(255,255,255,.3)';
  g.beginPath();
  g.roundRect(x + s * 0.16, y + s * 0.13, s * 0.42, s * 0.11, s * 0.05);
  g.fill();
  g.globalAlpha = 1;
}

const WORDS = ['', '', 'Отлично!', 'Супер!', 'Невероятно!', 'Потрясающе!'];

export function start(root, ctx) {
  root.innerHTML = `<div class="game blockblast">
      ${hud([['score', 'Очки', 0], ['best', '🏆 Рекорд', '—']])}
      <div class="game-stage bb-stage"><canvas class="game-canvas bb-canvas" data-board></canvas></div>
      <button class="game-btn ghost small" data-new>Новая игра</button></div>`;
  const stage = root.querySelector('.game-stage');
  const canvas = root.querySelector('[data-board]');
  const width = Math.floor(Math.min(root.clientWidth || 360, 460, (innerHeight - 210) * 0.72));
  const cell = Math.floor(width / N);
  const bw = cell * N;
  const slot = Math.floor(width / 3);
  const trayScale = Math.min(cell * 0.62, (slot - 14) / 5);
  const trayTop = bw + 18;
  const height = Math.ceil(trayTop + trayScale * 5 + 10);
  const g = fitCanvas(canvas, width, height);
  const bx = Math.floor((width - bw) / 2);
  const fx = particles();

  let board;
  let pieces;
  let score = 0;
  let streak = 0;
  let over = false;
  let drag = null;
  // Сгоревшие клетки, которые ещё гаснут на экране.
  let fading = [];
  let flash = 0;

  const showBest = () => { root.querySelector('[data-best]').textContent = String(best('blockblast') || '—'); };
  const persist = () => save('blockblast', over ? null : { board, pieces, score, streak });

  function newGame() {
    stage.querySelector('.game-over')?.remove();
    board = emptyBoard();
    pieces = dealPieces(board);
    score = 0;
    streak = 0;
    over = false;
    drag = null;
    fading = [];
    root.querySelector('[data-score]').textContent = '0';
    persist();
  }

  const piece = (i) => (pieces[i] ? SHAPES[pieces[i].shape] : null);
  // Центр слота в лотке.
  const slotCenter = (i) => [slot * i + slot / 2 + (width - slot * 3) / 2, trayTop + trayScale * 2.5];

  /** Клетка поля под перетаскиваемой фигурой или null, если фигура не встаёт. */
  function target() {
    if (!drag) return null;
    const cells = piece(drag.i);
    const [w, h] = size(cells);
    const gx = Math.round((drag.x - (w * cell) / 2 - bx) / cell);
    const gy = Math.round((drag.y - h * cell - cell * 1.4) / cell);
    return fits(board, cells, gx, gy) ? { gx, gy } : null;
  }

  function place(i, gx, gy) {
    const cells = piece(i);
    const color = pieces[i].color;
    cells.forEach(([x, y]) => { board[gy + y][gx + x] = color; });
    pieces[i] = null;
    const { rows, cols } = fullLines(board);
    const lines = rows.length + cols.length;
    const gain = moveScore(cells.length, lines, streak);
    if (lines) {
      const burn = new Set();
      rows.forEach((r) => { for (let c = 0; c < N; c += 1) burn.add(r * N + c); });
      cols.forEach((c) => { for (let r = 0; r < N; r += 1) burn.add(r * N + c); });
      burn.forEach((k) => {
        const r = Math.floor(k / N);
        const c = k % N;
        fading.push({ r, c, color: board[r][c], t: 0 });
        fx.burst(bx + c * cell + cell / 2, r * cell + cell / 2, board[r][c], 4, 3);
        board[r][c] = null;
      });
      streak += 1;
      flash = 1;
      sfx(lines > 1 ? 'win' : 'good');
      tap('medium');
      popText(stage, `+${gain}${WORDS[Math.min(lines, 5)] ? `<br>${WORDS[Math.min(lines, 5)]}` : ''}`, { x: '50%', y: '30%' });
    } else {
      streak = 0;
      sfx('tap');
      tap('light');
    }
    if (board.every((row) => row.every((v) => !v))) {
      score += 200;
      popText(stage, 'Чистое поле! +200', { x: '50%', y: '45%' });
    }
    score += gain;
    const scoreEl = root.querySelector('[data-score]');
    scoreEl.textContent = String(score);
    bump(scoreEl, score);
    if (pieces.every((p) => !p)) pieces = dealPieces(board);
    if (!pieces.some((p, k) => p && canPlaceAnywhere(board, piece(k)))) finish();
    else persist();
  }

  function finish() {
    over = true;
    const record = saveBest('blockblast', score);
    persist();
    showBest();
    setTimeout(() => {
      if (ctx.signal.aborted) return;
      overlay(stage, `<div class="game-over-title">${record ? 'Новый рекорд!' : 'Ходов больше нет'}</div>
          <div class="game-over-text">Очки: <b>${score}</b></div>`,
      [{ label: 'Играть ещё', run: newGame }], { icon: record ? '🏆' : '💥', lose: !record, win: record });
    }, 500);
  }

  // ───────────── Жесты ─────────────

  const point = (event) => {
    const rect = canvas.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top];
  };
  canvas.addEventListener('pointerdown', (event) => {
    if (over || drag) return;
    const [x, y] = point(event);
    if (y < trayTop - 12) return;
    const i = Math.min(2, Math.max(0, Math.floor((x - (width - slot * 3) / 2) / slot)));
    if (!pieces[i]) return;
    canvas.setPointerCapture(event.pointerId);
    drag = { i, x, y, id: event.pointerId };
    tap('light');
    sfx('tap');
  });
  canvas.addEventListener('pointermove', (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    [drag.x, drag.y] = point(event);
  });
  const release = (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const spot = event.type === 'pointerup' ? target() : null;
    const { i } = drag;
    drag = null;
    if (spot) place(i, spot.gx, spot.gy);
  };
  canvas.addEventListener('pointerup', release);
  canvas.addEventListener('pointercancel', release);

  root.querySelector('[data-new]').addEventListener('click', async () => {
    tap();
    sfx('tap');
    const started = !over && (score > 0 || board.some((row) => row.some(Boolean)));
    if (started && !(await ask(stage, 'Начать заново?', 'Текущая партия пропадёт.', 'Начать заново'))) return;
    newGame();
  });

  // ───────────── Рисование ─────────────

  function drawPiece(i, cx, cy, s, alpha = 1) {
    const cells = piece(i);
    const [w, h] = size(cells);
    cells.forEach(([x, y]) => block(g, cx - (w * s) / 2 + x * s, cy - (h * s) / 2 + y * s, s, pieces[i].color, alpha));
  }

  frames(ctx, (dt) => {
    g.clearRect(0, 0, width, height);
    // Доска.
    g.fillStyle = '#16205a';
    g.beginPath();
    g.roundRect(bx - 5, -5, bw + 10, bw + 10, 14);
    g.fill();
    for (let r = 0; r < N; r += 1) {
      for (let c = 0; c < N; c += 1) {
        g.fillStyle = (Math.floor(r / 4) + Math.floor(c / 4)) % 2 ? '#26337a' : '#2c3a86';
        g.beginPath();
        g.roundRect(bx + c * cell + 1.5, r * cell + 1.5, cell - 3, cell - 3, cell * 0.14);
        g.fill();
      }
    }
    // Что сгорит, если отпустить сейчас.
    const spot = target();
    let preview = null;
    if (spot) {
      const cells = piece(drag.i);
      const copy = board.map((row) => [...row]);
      cells.forEach(([x, y]) => { copy[spot.gy + y][spot.gx + x] = pieces[drag.i].color; });
      preview = fullLines(copy);
      const glow = new Set();
      preview.rows.forEach((r) => { for (let c = 0; c < N; c += 1) glow.add(r * N + c); });
      preview.cols.forEach((c) => { for (let r = 0; r < N; r += 1) glow.add(r * N + c); });
      g.fillStyle = 'rgba(255,255,255,.18)';
      glow.forEach((k) => {
        g.beginPath();
        g.roundRect(bx + (k % N) * cell + 1.5, Math.floor(k / N) * cell + 1.5, cell - 3, cell - 3, cell * 0.14);
        g.fill();
      });
    }
    const glowing = (r, c) => preview && (preview.rows.includes(r) || preview.cols.includes(c));
    for (let r = 0; r < N; r += 1) {
      for (let c = 0; c < N; c += 1) {
        if (board[r][c]) block(g, bx + c * cell, r * cell, cell, glowing(r, c) ? shade(board[r][c], 0.35) : board[r][c]);
      }
    }
    if (spot) {
      piece(drag.i).forEach(([x, y]) => block(g, bx + (spot.gx + x) * cell, (spot.gy + y) * cell, cell, pieces[drag.i].color, 0.45));
    }
    // Сгорающие клетки уменьшаются и гаснут.
    fading = fading.filter((f) => {
      f.t += dt * 3.2;
      if (f.t >= 1) return false;
      const s = cell * (1 - f.t * 0.6);
      g.globalAlpha = 1 - f.t;
      block(g, bx + f.c * cell + (cell - s) / 2, f.r * cell + (cell - s) / 2, s, shade(f.color, 0.4));
      g.globalAlpha = 1;
      return true;
    });
    if (flash > 0) {
      g.fillStyle = `rgba(255,255,255,${flash * 0.25})`;
      g.beginPath();
      g.roundRect(bx, 0, bw, bw, 10);
      g.fill();
      flash = Math.max(0, flash - dt * 4);
    }
    fx.draw(g, dt);
    // Лоток.
    for (let i = 0; i < 3; i += 1) {
      if (!pieces[i] || (drag && drag.i === i)) continue;
      const [cx, cy] = slotCenter(i);
      const playable = canPlaceAnywhere(board, piece(i));
      drawPiece(i, cx, cy, trayScale, playable ? 1 : 0.35);
    }
    // Фигура в руке: выше пальца, чтобы её было видно.
    if (drag) {
      const [, h] = size(piece(drag.i));
      drawPiece(drag.i, drag.x, drag.y - h * cell * 0.5 - cell * 1.4, cell);
    }
  });

  showBest();
  const saved = load('blockblast');
  if (saved?.board?.length === N && saved.pieces?.length === 3) {
    ({ board, pieces, score, streak } = saved);
    root.querySelector('[data-score]').textContent = String(score);
  } else {
    newGame();
  }
}
