// Block Blast: поле 8×8 и три фигуры. Фигуру перетаскивают на поле (поворачивать
// нельзя); заполненные строки и столбцы сгорают. Подряд сгоревшие линии дают
// серию с повышенными очками. Партия кончается, когда ни одна из трёх фигур
// не помещается. Начатая партия сохраняется. Рекламы нет.
'use strict';

import {
  ask, best, bump, fitCanvas, frames, load, overlay, particles, popText, rand, save, saveBest, sfx, shade, shuffle, tap,
} from './kit.js';

const N = 8;
// Палитры: первая снята с оригинала, при каждом чистом поле игра переходит
// к следующей. У фигуры один цвет на все блоки.
const PALETTES = [
  ['#f2c3c8', '#5a78d4', '#a062e0', '#b9c0f2', '#2f9bf0'],
  ['#ff7a7a', '#ffa94d', '#ffd43b', '#69db7c', '#38d9a9'],
  ['#4dabf7', '#9775fa', '#f783ac', '#63e6be', '#ffe066'],
  ['#e599f7', '#74c0fc', '#8ce99a', '#ffc078', '#ff8787'],
];

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

/** Поле после хода: фигура поставлена, полные линии сгорели. */
export function applyMove(board, cells, gx, gy) {
  const next = board.map((row) => [...row]);
  cells.forEach(([x, y]) => { next[gy + y][gx + x] = 'x'; });
  const { rows, cols } = fullLines(next);
  rows.forEach((r) => next[r].fill(null));
  cols.forEach((c) => next.forEach((row) => { row[c] = null; }));
  return { board: next, lines: rows.length + cols.length };
}

function placements(board, cells) {
  const list = [];
  for (let gy = 0; gy < N; gy += 1) {
    for (let gx = 0; gx < N; gx += 1) if (fits(board, cells, gx, gy)) list.push([gx, gy]);
  }
  return list;
}

/** Можно ли поставить все фигуры по очереди, в любом порядке (перебор ограничен). */
export function solvable(board, shapes) {
  if (!shapes.length) return true;
  return shapes.some((cells, i) => {
    const rest = shapes.filter((_, k) => k !== i);
    return shuffle(placements(board, cells)).slice(0, 14).some(([gx, gy]) => solvable(applyMove(board, cells, gx, gy).board, rest));
  });
}

/** Сжигает ли фигура хоть одну линию, если поставить её удачно. */
const clearsLine = (board, cells) => placements(board, cells).some(([gx, gy]) => applyMove(board, cells, gx, gy).lines > 0);

/** Три новые фигуры под текущее поле: все три можно поставить подряд, а когда
 *  поле заполнено — хотя бы одна сжигает линию. Совсем случайные наборы
 *  давали тупики там, где ход ещё можно было дать. */
export function dealPieces(board, palette = PALETTES[0]) {
  const filled = board.flat().filter(Boolean).length;
  const draw = () => Array.from({ length: 3 }, () => {
    const shape = rand(SHAPES.length);
    const color = palette[rand(palette.length)];
    return { shape, colors: SHAPES[shape].map(() => color) };
  });
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const pieces = draw();
    const shapes = pieces.map((piece) => SHAPES[piece.shape]);
    if (!solvable(board, shapes)) continue;
    if (filled >= 14 && attempt < 90 && !shapes.some((cells) => clearsLine(board, cells))) continue;
    return pieces;
  }
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const pieces = draw();
    if (pieces.some((piece) => canPlaceAnywhere(board, SHAPES[piece.shape]))) return pieces;
  }
  return palette.slice(0, 3).map((color) => ({ shape: 0, colors: [color] }));
}

/** Очки за ход: блоки фигуры, линии и серия подряд сгоревших ходов. */
export const moveScore = (cells, lines, streak) => cells + (lines ? lines * 10 * (lines + streak) : 0);

/** Квадрат с фаской: светлые верх и левая грань, тёмные низ и правая. */
function block(g, x, y, s, color, alpha = 1) {
  g.globalAlpha = alpha;
  const b = s * 0.14;
  const x1 = x + s;
  const y1 = y + s;
  const face = (points, fill) => {
    g.fillStyle = fill;
    g.beginPath();
    points.forEach(([px, py], i) => (i ? g.lineTo(px, py) : g.moveTo(px, py)));
    g.closePath();
    g.fill();
  };
  face([[x, y], [x1, y], [x1 - b, y + b], [x + b, y + b]], shade(color, 0.5));
  face([[x, y], [x + b, y + b], [x + b, y1 - b], [x, y1]], shade(color, 0.28));
  face([[x, y1], [x + b, y1 - b], [x1 - b, y1 - b], [x1, y1]], shade(color, -0.38));
  face([[x1, y], [x1, y1], [x1 - b, y1 - b], [x1 - b, y + b]], shade(color, -0.2));
  g.fillStyle = color;
  g.fillRect(x + b, y + b, s - 2 * b, s - 2 * b);
  g.globalAlpha = 1;
}

const WORDS = ['', '', 'Отлично!', 'Супер!', 'Невероятно!', 'Потрясающе!'];

export function start(root, ctx) {
  root.innerHTML = `<div class="game blockblast">
      <div class="bb-top"><div class="bb-best"><span aria-hidden="true">👑</span><b data-best>—</b></div><div class="bb-score" data-score>0</div></div>
      <div class="game-stage bb-stage"><canvas class="game-canvas bb-canvas" data-board></canvas></div>
      <button class="game-btn ghost small" data-new>Новая игра</button></div>`;
  const stage = root.querySelector('.game-stage');
  const canvas = root.querySelector('[data-board]');
  const width = Math.floor(Math.min(root.clientWidth || 360, 460, (innerHeight - 250) * 0.72));
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
  let theme = 0;
  let over = false;
  // Фигура, летящая обратно в лоток после неудачного броска.
  let returning = null;
  let drag = null;
  // Сгоревшие клетки, которые ещё гаснут на экране.
  let fading = [];
  let flash = 0;

  const showBest = () => { root.querySelector('[data-best]').textContent = String(best('blockblast') || '—'); };
  const persist = () => save('blockblast', over ? null : { board, pieces, score, streak, theme });

  function newGame() {
    stage.querySelector('.game-over')?.remove();
    board = emptyBoard();
    theme = 0;
    pieces = dealPieces(board, PALETTES[0]);
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
    const { colors } = pieces[i];
    cells.forEach(([x, y], k) => { board[gy + y][gx + x] = colors[k]; });
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
      // Чистое поле: бонус и новая палитра, в том числе у фигур в лотке.
      theme = (theme + 1) % PALETTES.length;
      pieces = pieces.map((p) => {
        if (!p) return p;
        const color = PALETTES[theme][rand(PALETTES[theme].length)];
        return { ...p, colors: p.colors.map(() => color) };
      });
      score += 200;
      popText(stage, 'Чистое поле! +200', { x: '50%', y: '45%' });
    }
    score += gain;
    const scoreEl = root.querySelector('[data-score]');
    scoreEl.textContent = String(score);
    bump(scoreEl, score);
    if (pieces.every((p) => !p)) pieces = dealPieces(board, PALETTES[theme]);
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
    if (spot) place(i, spot.gx, spot.gy);
    else returning = { i, x: drag.x, y: drag.y, t: 0 };
    drag = null;
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
    g.fillStyle = 'rgba(0, 0, 0, .22)';
    cells.forEach(([x, y]) => g.fillRect(cx - (w * s) / 2 + x * s + s * 0.08, cy - (h * s) / 2 + y * s + s * 0.1, s, s));
    cells.forEach(([x, y], k) => block(g, cx - (w * s) / 2 + x * s, cy - (h * s) / 2 + y * s, s, pieces[i].colors[k], alpha));
  }

  frames(ctx, (dt) => {
    g.clearRect(0, 0, width, height);
    // Доска.
    g.fillStyle = '#3a3984';
    g.beginPath();
    g.roundRect(bx - 5, -5, bw + 10, bw + 10, 8);
    g.fill();
    for (let r = 0; r < N; r += 1) {
      for (let c = 0; c < N; c += 1) {
        g.fillStyle = '#2b2a68';
        g.fillRect(bx + c * cell + 0.5, r * cell + 0.5, cell - 1, cell - 1);
      }
    }
    // Что сгорит, если отпустить сейчас.
    const spot = target();
    let preview = null;
    if (spot) {
      const cells = piece(drag.i);
      const copy = board.map((row) => [...row]);
      cells.forEach(([x, y]) => { copy[spot.gy + y][spot.gx + x] = '#fff'; });
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
      piece(drag.i).forEach(([x, y], k) => block(g, bx + (spot.gx + x) * cell, (spot.gy + y) * cell, cell, pieces[drag.i].colors[k], 0.45));
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
      if (!pieces[i] || (drag && drag.i === i) || (returning && returning.i === i)) continue;
      const [cx, cy] = slotCenter(i);
      const playable = canPlaceAnywhere(board, piece(i));
      drawPiece(i, cx, cy, trayScale, playable ? 1 : 0.35);
    }
    if (returning) {
      returning.t += dt * 6;
      const k = Math.min(1, returning.t);
      const ease = 1 - (1 - k) ** 3;
      const [h] = [size(piece(returning.i))[1]];
      const [tx, ty] = slotCenter(returning.i);
      const fromY = returning.y - h * cell * 0.5 - cell * 1.4;
      drawPiece(returning.i, returning.x + (tx - returning.x) * ease, fromY + (ty - fromY) * ease, cell + (trayScale - cell) * ease);
      if (k >= 1) returning = null;
    }
    // Фигура в руке: выше пальца, чтобы её было видно.
    if (drag) {
      const [, h] = size(piece(drag.i));
      drawPiece(drag.i, drag.x, drag.y - h * cell * 0.5 - cell * 1.4, cell);
    }
  });

  showBest();
  const saved = load('blockblast');
  if (saved?.board?.length === N && saved.pieces?.length === 3 && saved.pieces.every((p) => !p || p.colors)) {
    ({ board, pieces, score, streak } = saved);
    theme = saved.theme || 0;
    root.querySelector('[data-score]').textContent = String(score);
  } else {
    newGame();
  }
}
