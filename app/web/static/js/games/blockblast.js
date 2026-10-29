// Block Blast: поле 8×8 и три фигуры. Фигуру перетаскивают на поле (поворачивать
// нельзя); заполненные строки и столбцы сгорают. Подряд сгоревшие линии дают
// серию с повышенными очками. Партия кончается, когда ни одна из трёх фигур
// не помещается. Начатая партия сохраняется. Рекламы нет.
'use strict';

import {
  ask, best, fitCanvas, frames, isMuted, load, overlay, rand, save, saveBest, setMuted, sfx, shade, shuffle, tap,
} from './kit.js';

const N = 8;
// Темы: фон экрана, поле и цвета фигур. После каждого чистого поля игра
// переходит к следующей теме (по кругу), как в оригинале. У фигуры один цвет.
const THEMES = [
  { bg: ['#4748a5', '#403c9c'], frame: '#332d7c', cell: '#2c2756', line: '#1c1545', colors: ['#eab8c2', '#5367c2', '#8f5fd6', '#b8c2f4', '#3a9af4'] },
  { bg: ['#35448c', '#314686'], frame: '#35478a', cell: '#1e254a', line: '#1b2245', colors: ['#2f50e0', '#e0382f', '#2fae38', '#e1a52d', '#ecc12a'] },
  { bg: ['#706968', '#5e5454'], frame: '#3f3a39', cell: '#221f23', line: '#171418', colors: ['#2f4fd8', '#b8392c', '#e08a3c', '#73412d', '#e0b84a'] },
  { bg: ['#ae301b', '#8c2718'], frame: '#802113', cell: '#442019', line: '#2d130f', colors: ['#f5c11a', '#f08a24', '#ffd84a', '#e8651f'] },
  { bg: ['#9277cd', '#947cd1'], frame: '#7e66b7', cell: '#bea6f7', line: '#876fc0', colors: ['#c33ac3', '#e15fe1', '#a523a5'] },
];
// Фон между чистым полем и следующим ходом.
const BONUS_BG = ['#5b42bc', '#2c399f'];
const PALETTES = THEMES.map((theme) => theme.colors);

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

const WORDS = ['', '', 'Great!', 'Excellent!', 'Unbelievable!', 'Unbelievable!'];

export function start(root, ctx) {
  let vibrate = load('bb-vibrate') !== false;
  const hap = (kind) => { if (vibrate) tap(kind); };
  root.innerHTML = `<div class="game blockblast">
      <div class="bb-top">
        <div class="bb-best"><svg viewBox="0 0 32 26" aria-hidden="true"><path d="M2 8l7 6 7-11 7 11 7-6-3 16H5z" fill="currentColor"/><rect x="5" y="22" width="22" height="3" rx="1.5" fill="currentColor" opacity=".7"/></svg><b data-best>—</b></div>
        <button class="bb-gear" data-gear aria-label="Настройки"><svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M19.4 13a7.6 7.6 0 0 0 0-2l2.1-1.6a.5.5 0 0 0 .1-.6l-2-3.5a.5.5 0 0 0-.6-.2l-2.5 1a7.5 7.5 0 0 0-1.7-1l-.4-2.6a.5.5 0 0 0-.5-.4h-4a.5.5 0 0 0-.5.4l-.4 2.6c-.6.3-1.2.6-1.7 1l-2.5-1a.5.5 0 0 0-.6.2l-2 3.5a.5.5 0 0 0 .1.6L4.6 11a7.6 7.6 0 0 0 0 2l-2.1 1.6a.5.5 0 0 0-.1.6l2 3.5c.1.2.4.3.6.2l2.5-1c.5.4 1.1.7 1.7 1l.4 2.6c0 .2.2.4.5.4h4c.3 0 .5-.2.5-.4l.4-2.6c.6-.3 1.2-.6 1.7-1l2.5 1c.2.1.5 0 .6-.2l2-3.5a.5.5 0 0 0-.1-.6zM12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z"/></svg></button>
        <div class="bb-score"><i class="bb-diamond" data-diamond></i><span data-score>0</span></div>
      </div>
      <div class="game-stage bb-stage"><canvas class="game-canvas bb-canvas" data-board></canvas></div></div>`;
  const flashEl = document.createElement('div');
  flashEl.className = 'bb-flash';
  root.closest('.game-screen')?.append(flashEl);
  const bumpFlash = () => {
    flashEl.classList.remove('on');
    void flashEl.offsetWidth;
    flashEl.classList.add('on');
  };
  const pulseDiamond = () => {
    const el = root.querySelector('[data-diamond]');
    el.classList.remove('pulse');
    void el.offsetWidth;
    el.classList.add('pulse');
  };
  /** Надпись поверх поля с разметкой (в kit.popText только текст). */
  const pop = (html, cls, x = '50%', y = '40%') => {
    const el = document.createElement('div');
    el.className = `pop-text ${cls}`;
    el.innerHTML = html;
    el.style.left = x;
    el.style.top = y;
    stage.append(el);
    el.addEventListener('animationend', () => el.remove());
  };
  const stage = root.querySelector('.game-stage');
  const canvas = root.querySelector('[data-board]');
  // Доска — 88% ширины экрана, вокруг неё рамка и поля, слоты лотка — по третям доски.
  const cell = Math.floor(Math.min((root.clientWidth || 360) * 0.884, 460, (innerHeight - 250) * 0.72) / N);
  const bw = cell * N;
  const pad = 8;
  const width = bw + pad * 2;
  const bx = pad;
  const oy = pad;
  const slot = bw / 3;
  const trayScale = Math.min(cell * 0.45, (slot - 14) / 5);
  const trayTop = bw + cell * 0.8;
  const height = Math.ceil(bw + cell * 4.4 + oy);
  const g = fitCanvas(canvas, width, height);

  let board;
  let pieces;
  let score = 0;
  let streak = 0;
  let theme = 0;
  let over = false;
  // Фигура, летящая обратно в лоток после неудачного броска.
  let returning = null;
  // Фигура в руке плавно догоняет палец (hold), клетки только что поставленной фигуры «подпрыгивают» (pops),
  // сгорающие линии (burning) и осколки (bits).
  let hold = null;
  let pops = [];
  let burning = [];
  let bits = [];
  const LIFT = 2.2;
  let drag = null;
  // Сгоревшие клетки, которые ещё гаснут на экране.
  let fading = [];
  // Счёт на экране догоняет настоящий плавно.
  let shown = 0;
  // После чистого поля вокруг доски светится рамка — до следующего хода.
  let cleanGlow = false;
  let bonusBg = false;

  const showBest = () => {
    root.querySelector('[data-best]').textContent = String(Math.max(best('blockblast'), score) || '—');
    root.querySelector('.bb-best').classList.toggle('gold', theme > 0 || score > best('blockblast'));
  };
  const persist = () => save('blockblast', over ? null : { board, pieces, score, streak, theme });

  function newGame() {
    stage.querySelector('.game-over')?.remove();
    board = emptyBoard();
    theme = 0;
    cleanGlow = false;
    bonusBg = false;
    shown = 0;
    paint();
    pieces = dealPieces(board, PALETTES[0]);
    score = 0;
    streak = 0;
    over = false;
    drag = null;
    fading = [];
    root.querySelector('[data-score]').textContent = '0';
    persist();
  }

  const screen = root.closest('.game-screen');
  const look = () => THEMES[theme % THEMES.length];
  const paint = () => {
    showBest();
    const [top, bottom] = bonusBg ? BONUS_BG : look().bg;
    screen?.style.setProperty('--g-bg', `linear-gradient(180deg, ${top}, ${bottom})`);
  };

  const piece = (i) => (pieces[i] ? SHAPES[pieces[i].shape] : null);
  // Центр слота в лотке.
  const slotCenter = (i) => [bx + slot * (i + 0.5), bw + cell * 2.4];

  /** Клетка поля под перетаскиваемой фигурой или null, если фигура не встаёт. */
  function target() {
    if (!drag) return null;
    const cells = piece(drag.i);
    const [w, h] = size(cells);
    const gx = Math.round((drag.x - (w * cell) / 2 - bx) / cell);
    const gy = Math.round((drag.y - h * cell - cell * LIFT) / cell);
    return fits(board, cells, gx, gy) ? { gx, gy } : null;
  }

  function place(i, gx, gy) {
    const cells = piece(i);
    const { colors } = pieces[i];
    cells.forEach(([x, y], k) => { board[gy + y][gx + x] = colors[k]; });
    pieces[i] = null;
    cleanGlow = false;
    if (bonusBg) {
      bonusBg = false;
      paint();
    }
    pops = cells.map(([x, y]) => ({ r: gy + y, c: gx + x, t: 0 }));
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
        fading.push({ r, c, color: board[r][c], t: 0, spawned: false });
        board[r][c] = null;
      });
      rows.forEach((idx) => burning.push({ row: true, idx, t: 0 }));
      cols.forEach((idx) => burning.push({ row: false, idx, t: 0 }));
      streak += 1;
      sfx('bbClear', { lines, combo: streak });
      hap('medium');
      flashEl.style.setProperty('--f', String(Math.min(1, 0.35 + lines * 0.2)));
      bumpFlash();
      pulseDiamond();
      pop(String(gain), 'bb-total', '50%', '48%');
      if (WORDS[Math.min(lines, 5)]) pop(WORDS[Math.min(lines, 5)], 'bb-word', '50%', '30%');
      if (streak >= 2) pop(`<span>Combo</span> <b>${streak}</b>`, 'bb-combo', '50%', '58%');
    } else {
      streak = 0;
      sfx('bbPlace');
      hap('light');
    }
    if (board.every((row) => row.every((v) => !v))) {
      // Чистое поле: бонус и новая палитра, в том числе у фигур в лотке.
      theme = (theme + 1) % THEMES.length;
      cleanGlow = true;
      bonusBg = true;
      sfx('bbClean');
      paint();
      pieces = pieces.map((p) => {
        if (!p) return p;
        const color = PALETTES[theme][rand(PALETTES[theme].length)];
        return { ...p, colors: p.colors.map(() => color) };
      });
      score += 200;
      pop(String(gain + 200), 'bb-bonus', '26%', '9%');
    }
    score += gain;
    if (pieces.every((p) => !p)) pieces = dealPieces(board, PALETTES[theme]);
    if (!pieces.some((p, k) => p && canPlaceAnywhere(board, piece(k)))) finish();
    else persist();
  }

  function finish() {
    over = true;
    sfx('bbOver');
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
    return [event.clientX - rect.left, event.clientY - rect.top - oy];
  };
  canvas.addEventListener('pointerdown', (event) => {
    if (over || drag) return;
    const [x, y] = point(event);
    if (y < bw + cell * 0.5) return;
    const i = Math.min(2, Math.max(0, Math.floor((x - bx) / slot)));
    if (!pieces[i]) return;
    canvas.setPointerCapture(event.pointerId);
    const [sx, sy] = slotCenter(i);
    drag = { i, x, y, id: event.pointerId };
    hold = { x: sx, y: sy, s: trayScale };
    hap('light');
    sfx('bbPick');
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
    else {
      returning = { i, x: hold.x, y: hold.y, s: hold.s, t: 0 };
      sfx('bbBack');
    }
    drag = null;
  };
  canvas.addEventListener('pointerup', release);
  canvas.addEventListener('pointercancel', release);

  async function restart() {
    const started = !over && (score > 0 || board.some((row) => row.some(Boolean)));
    if (started && !(await ask(stage, 'Начать заново?', 'Текущая партия пропадёт.', 'Начать заново'))) return;
    newGame();
  }

  // Настройки: звук, вибрация, выход из игры и новая партия.
  root.querySelector('[data-gear]').addEventListener('click', () => {
    const host = root.closest('.game-screen') || root;
    if (host.querySelector('.bb-modal')) return;
    hap('light');
    sfx('bbPick');
    const modal = document.createElement('div');
    modal.className = 'bb-modal';
    modal.innerHTML = `<div class="bb-card" role="dialog" aria-label="Настройки">
        <div class="bb-card-head"><b>Настройки</b><button data-close aria-label="Закрыть">✕</button></div>
        <label class="bb-row"><span>Звук</span><input type="checkbox" class="bb-sw" data-sound ${isMuted() ? '' : 'checked'}></label>
        <label class="bb-row"><span>Вибрация</span><input type="checkbox" class="bb-sw" data-vibe ${vibrate ? 'checked' : ''}></label>
        <div class="bb-row"><span>Начать заново</span><button class="bb-go" data-again>Играть</button></div>
        <div class="bb-row"><span>Все игры</span><button class="bb-go home" data-home>Выйти</button></div></div>`;
    const close = () => modal.remove();
    modal.addEventListener('click', (event) => {
      if (event.target === modal || event.target.closest('[data-close]')) close();
      else if (event.target.closest('[data-again]')) { close(); restart(); }
      else if (event.target.closest('[data-home]')) {
        close();
        host.querySelector('.screen-back')?.click();
      }
    });
    modal.querySelector('[data-sound]').addEventListener('change', (event) => {
      setMuted(!event.target.checked);
      sfx('bbPick');
    });
    modal.querySelector('[data-vibe]').addEventListener('change', (event) => {
      vibrate = event.target.checked;
      save('bb-vibrate', vibrate);
      hap('light');
    });
    host.append(modal);
  });
  ctx.signal.addEventListener('abort', () => document.querySelectorAll('.bb-modal').forEach((el) => el.remove()));

  // ───────────── Рисование ─────────────

  function drawPiece(i, cx, cy, s, alpha = 1) {
    const cells = piece(i);
    const [w, h] = size(cells);
    g.fillStyle = 'rgba(0, 0, 0, .22)';
    cells.forEach(([x, y]) => g.fillRect(cx - (w * s) / 2 + x * s + s * 0.08, cy - (h * s) / 2 + y * s + s * 0.1, s, s));
    cells.forEach(([x, y], k) => block(g, cx - (w * s) / 2 + x * s, cy - (h * s) / 2 + y * s, s, pieces[i].colors[k], alpha));
  }

  const soft = (t) => 1 - (1 - Math.min(1, t)) ** 3;
  // Свечение рисуется слоями линий: shadowBlur на холсте каждый кадр тормозит iPhone.
  const outline = (x, y, w, h, alpha) => {
    g.save();
    g.strokeStyle = '#ffb83a';
    [[8, 0.12], [4.5, 0.25]].forEach(([width, a]) => {
      g.globalAlpha = alpha * a;
      g.lineWidth = width;
      g.strokeRect(x + 1, y + 1, w - 2, h - 2);
    });
    g.globalAlpha = alpha;
    g.strokeStyle = '#ffe08a';
    g.lineWidth = 1.8;
    g.strokeRect(x + 1, y + 1, w - 2, h - 2);
    g.restore();
  };

  frames(ctx, (dt) => {
    g.clearRect(0, 0, width, height);
    g.save();
    g.translate(0, oy);
    if (shown !== score) {
      shown = shown < score ? Math.min(score, shown + Math.max(1, Math.ceil((score - shown) * dt * 9))) : score;
      root.querySelector('[data-score]').textContent = String(shown);
      root.querySelector('[data-diamond]').style.rotate = `${45 + shown * 2}deg`;
      showBest();
    }
    // Доска.
    g.fillStyle = look().frame;
    g.beginPath();
    g.roundRect(bx - 6, -6, bw + 12, bw + 12, 9);
    g.fill();
    g.fillStyle = look().line;
    g.fillRect(bx, 0, bw, bw);
    g.fillStyle = look().cell;
    for (let r = 0; r < N; r += 1) {
      for (let c = 0; c < N; c += 1) g.fillRect(bx + c * cell + 0.5, r * cell + 0.5, cell - 1, cell - 1);
    }
    // Что сгорит, если отпустить сейчас: клетки светлеют, по краям линии — золотая кайма.
    const spot = target();
    let preview = null;
    if (spot) {
      const copy = board.map((row) => [...row]);
      piece(drag.i).forEach(([x, y]) => { copy[spot.gy + y][spot.gx + x] = '#fff'; });
      preview = fullLines(copy);
    }
    const glowing = (r, c) => preview && (preview.rows.includes(r) || preview.cols.includes(c));
    pops = pops.filter((p) => { p.t += dt / 0.14; return p.t < 1; });
    for (let r = 0; r < N; r += 1) {
      for (let c = 0; c < N; c += 1) {
        if (!board[r][c]) continue;
        const pop = pops.find((p) => p.r === r && p.c === c);
        const s = pop ? cell * (1 + 0.16 * Math.sin(Math.PI * pop.t)) : cell;
        const o = (cell - s) / 2;
        block(g, bx + c * cell + o, r * cell + o, s, glowing(r, c) ? shade(board[r][c], 0.4) : board[r][c]);
      }
    }
    if (spot) {
      piece(drag.i).forEach(([x, y], k) => block(g, bx + (spot.gx + x) * cell, (spot.gy + y) * cell, cell, pieces[drag.i].colors[k], 0.4));
    }
    if (preview) {
      preview.rows.forEach((r) => outline(bx, r * cell, bw, cell, 1));
      preview.cols.forEach((c) => outline(bx + c * cell, 0, cell, bw, 1));
    }
    // Сгорание линии: золотая кайма → синий неоновый луч.
    burning = burning.filter((b) => {
      b.t += dt / 0.42;
      if (b.t >= 1) return false;
      const x = b.row ? bx : bx + b.idx * cell;
      const y = b.row ? b.idx * cell : 0;
      const w = b.row ? bw : cell;
      const h = b.row ? cell : bw;
      if (b.t < 0.3) outline(x, y, w, h, 1 - b.t / 0.3 * 0.5);
      if (b.t > 0.12 && b.t < 0.75) {
        const k = Math.sin(Math.PI * (b.t - 0.12) / 0.63);
        const th = cell * 0.16 * (1 + k);
        g.save();
        const beam = (extra, alpha, color) => {
          g.globalAlpha = k * alpha;
          g.fillStyle = color;
          if (b.row) g.fillRect(x + cell * 0.2, y + cell / 2 - (th + extra) / 2, w - cell * 0.4, th + extra);
          else g.fillRect(x + cell / 2 - (th + extra) / 2, y + cell * 0.2, th + extra, h - cell * 0.4);
        };
        beam(cell * 0.5, 0.18, '#7da8ff');
        beam(cell * 0.2, 0.35, '#9dbdff');
        beam(0, 1, '#e4eeff');
        g.restore();
      }
      return true;
    });
    // Сгоревшие клетки: тают, потом «5» и рассыпаются квадратиками.
    fading = fading.filter((f) => {
      f.t += dt / 0.5;
      if (f.t >= 1) return false;
      const cx = bx + f.c * cell + cell / 2;
      const cy = f.r * cell + cell / 2;
      if (f.t < 0.22) {
        g.globalAlpha = 1 - f.t / 0.22;
        block(g, cx - cell / 2, cy - cell / 2, cell, shade(f.color, 0.35));
        g.globalAlpha = 1;
      }
      if (f.t > 0.18) {
        if (!f.spawned) {
          f.spawned = true;
          for (let k = 0; k < 4; k += 1) {
            bits.push({ x: cx + (Math.random() - 0.5) * cell * 0.8, y: cy + (Math.random() - 0.5) * cell * 0.8, vy: 10 + Math.random() * 30, size: cell * (0.1 + Math.random() * 0.1), life: 1, color: shade(f.color, 0.3) });
          }
        }
        const k = Math.min(1, (f.t - 0.18) / 0.15) * Math.min(1, (1 - f.t) / 0.25);
        g.save();
        g.globalAlpha = k;
        g.fillStyle = 'rgba(255, 255, 255, .22)';
        g.fillRect(cx - cell / 2 + 1, cy - cell / 2 + 1, cell - 2, cell - 2);
        g.font = `${Math.round(cell * 0.64)}px "BB Lilita", ui-rounded, system-ui, sans-serif`;
        g.textAlign = 'center';
        g.textBaseline = 'middle';
        g.strokeStyle = 'rgba(255, 110, 20, .7)';
        g.lineWidth = 4;
        g.lineJoin = 'round';
        g.strokeText('5', cx, cy + 1);
        g.fillStyle = '#ffe08a';
        g.fillText('5', cx, cy + 1);
        g.restore();
      }
      return true;
    });
    bits = bits.filter((b) => {
      b.life -= dt / 0.45;
      if (b.life <= 0) return false;
      b.y += b.vy * dt;
      g.globalAlpha = b.life * 0.8;
      g.fillStyle = b.color;
      g.fillRect(b.x - b.size / 2, b.y - b.size / 2, b.size, b.size);
      g.globalAlpha = 1;
      return true;
    });
    if (cleanGlow) {
      g.fillStyle = 'rgba(140, 90, 255, .22)';
      g.fillRect(bx, 0, bw, bw);
      g.save();
      [['#3fe0ff', 12, 0.14], ['#3fe0ff', 7, 0.28], ['#7fefff', 3.5, 1], ['#e8fdff', 1.2, 1]].forEach(([color, width, alpha]) => {
        g.globalAlpha = alpha;
        g.strokeStyle = color;
        g.lineWidth = width;
        g.strokeRect(bx - 3, -3, bw + 6, bw + 6);
      });
      g.restore();
    }
    // Лоток. На месте взятой фигуры остаётся бирюзовая рамка.
    if (drag) {
      const [hx, hy] = slotCenter(drag.i);
      g.strokeStyle = '#5ee6ff';
      g.lineWidth = 2;
      g.strokeRect(hx - trayScale * 0.9, hy - trayScale * 0.9, trayScale * 1.8, trayScale * 1.8);
    }
    for (let i = 0; i < 3; i += 1) {
      if (!pieces[i] || (drag && drag.i === i) || (returning && returning.i === i)) continue;
      const [cx, cy] = slotCenter(i);
      drawPiece(i, cx, cy, trayScale, canPlaceAnywhere(board, piece(i)) ? 1 : 0.35);
    }
    if (returning) {
      returning.t += dt / 0.12;
      const k = soft(returning.t);
      const [tx, ty] = slotCenter(returning.i);
      drawPiece(returning.i, returning.x + (tx - returning.x) * k, returning.y + (ty - returning.y) * k, returning.s + (trayScale - returning.s) * k);
      if (returning.t >= 1) returning = null;
    }
    // Фигура в руке: растёт из лотка и идёт ровно за пальцем, выше него.
    if (drag && hold) {
      const [, h] = size(piece(drag.i));
      const follow = 1 - Math.exp(-dt * 60);
      hold.x += (drag.x - hold.x) * follow;
      hold.y += (drag.y - cell * LIFT - (h * cell) / 2 - hold.y) * follow;
      hold.s += (cell - hold.s) * (1 - Math.exp(-dt * 30));
      drawPiece(drag.i, hold.x, hold.y, hold.s);
    }
    g.restore();
  });

  showBest();
  const saved = load('blockblast');
  if (saved?.board?.length === N && saved.pieces?.length === 3 && saved.pieces.every((p) => !p || p.colors)) {
    ({ board, pieces, score, streak } = saved);
    theme = saved.theme || 0;
    shown = score;
    root.querySelector('[data-score]').textContent = String(score);
    paint();
  } else {
    newGame();
  }
}
