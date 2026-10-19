// Змейка: одна или против двух змей-ботов, которые охотятся за теми же
// яблоками. Врезался в стену, в себя или в чужое тело — конец. Боты после
// гибели возвращаются через пару секунд.
'use strict';

import {
  DIRS, best, bindChips, chipsRow, fitCanvas, frames, load, notify, onDirection, overlay, rand, save, saveBest, tap, themeColor,
} from './kit.js';

const N = 20;
const BOT_COLORS = ['#e5484d', '#f2a33a'];
const OPPOSITE = { up: 'down', down: 'up', left: 'right', right: 'left' };

/** Первый шаг кратчайшего пути до яблока (поиск в ширину) или любой
 *  безопасный ход. Экспортируется для тестов. */
export function botStep(head, dir, blocked, apples, n = N) {
  const key = (x, y) => y * n + x;
  const seen = new Map([[key(...head), null]]);
  const queue = [head];
  const free = (x, y) => x >= 0 && y >= 0 && x < n && y < n && !blocked.has(key(x, y));
  const appleSet = new Set(apples.map(([x, y]) => key(x, y)));
  while (queue.length) {
    const [x, y] = queue.shift();
    if (appleSet.has(key(x, y)) && (x !== head[0] || y !== head[1])) {
      // Назад по цепочке до первого шага.
      let cell = key(x, y);
      let first = null;
      while (seen.get(cell)) {
        first = seen.get(cell).dir;
        cell = seen.get(cell).from;
      }
      return first;
    }
    for (const [name, [dx, dy]] of Object.entries(DIRS)) {
      if (x === head[0] && y === head[1] && name === OPPOSITE[dir]) continue;
      const nx = x + dx;
      const ny = y + dy;
      if (!free(nx, ny) || seen.has(key(nx, ny))) continue;
      seen.set(key(nx, ny), { from: key(x, y), dir: name });
      queue.push([nx, ny]);
    }
  }
  // До яблока не добраться — туда, где больше места.
  const options = Object.entries(DIRS).filter(([name, [dx, dy]]) => name !== OPPOSITE[dir] && free(head[0] + dx, head[1] + dy));
  if (!options.length) return dir;
  const room = ([, [dx, dy]]) => Object.values(DIRS).filter(([ex, ey]) => free(head[0] + dx + ex, head[1] + dy + ey)).length;
  options.sort((a, b) => room(b) - room(a));
  return options[0][0];
}

export function start(root, ctx) {
  let mode = load('snake-mode', 'solo');
  root.innerHTML = `<div class="game">
      <div class="game-stats"><span>Счёт: <b data-score>0</b></span><span>Рекорд: <b data-best>${best('snake')}</b></span></div>
      ${chipsRow('mode', [['solo', 'Одна'], ['bots', 'С ботами']], mode)}
      <div class="game-stage"><canvas class="game-canvas" data-board></canvas></div>
      <div class="dpad">
        <button data-dir="up" aria-label="Вверх">▲</button>
        <button data-dir="left" aria-label="Влево">◀</button>
        <button data-dir="down" aria-label="Вниз">▼</button>
        <button data-dir="right" aria-label="Вправо">▶</button>
      </div>
      <div class="muted small game-hint">Свайп по полю или кнопки. На компьютере — стрелки.</div></div>`;
  const stage = root.querySelector('.game-stage');
  const canvas = root.querySelector('[data-board]');
  const size = Math.min(stage.clientWidth || 340, 420);
  const cell = size / N;
  const g = fitCanvas(canvas, size, size);
  const colors = { bg: themeColor('--card', '#fff'), grid: themeColor('--border', '#ddd'), me: themeColor('--primary', '#3b6fc4') };

  let snakes = [];
  let apples = [];
  let score = 0;
  let running = false;
  let acc = 0;
  let speed = 0.13;

  const occupied = () => {
    const cells = new Set();
    snakes.forEach((snake) => snake.alive && snake.body.forEach(([x, y]) => cells.add(y * N + x)));
    return cells;
  };

  function freeCell() {
    const busy = occupied();
    apples.forEach(([x, y]) => busy.add(y * N + x));
    for (let tries = 0; tries < 400; tries += 1) {
      const x = rand(N);
      const y = rand(N);
      if (!busy.has(y * N + x)) return [x, y];
    }
    return null;
  }

  function spawn(snake, x, y, dir) {
    const [dx, dy] = DIRS[dir];
    snake.body = [[x, y], [x - dx, y - dy], [x - 2 * dx, y - 2 * dy]];
    snake.dir = dir;
    snake.queue = [];
    snake.alive = true;
    snake.grow = 0;
  }

  function reset() {
    snakes = [{ bot: false, color: colors.me }];
    spawn(snakes[0], 5, N - 4, 'right');
    if (mode === 'bots') {
      snakes.push({ bot: true, color: BOT_COLORS[0] });
      snakes.push({ bot: true, color: BOT_COLORS[1] });
      spawn(snakes[1], N - 6, 3, 'left');
      spawn(snakes[2], N - 6, N / 2, 'left');
    }
    apples = [];
    const count = mode === 'bots' ? 3 : 1;
    for (let i = 0; i < count; i += 1) apples.push(freeCell());
    score = 0;
    speed = 0.13;
    root.querySelector('[data-score]').textContent = '0';
  }

  function respawnBot(snake) {
    setTimeout(() => {
      if (!running || ctx.signal.aborted) return;
      const spot = freeCell();
      if (spot && spot[0] > 3 && spot[0] < N - 3) spawn(snake, spot[0], spot[1], rand(2) ? 'left' : 'right');
      else respawnBot(snake);
    }, 2500);
  }

  function step() {
    const me = snakes[0];
    if (me.queue.length) me.dir = me.queue.shift();
    const blocked = occupied();
    snakes.forEach((snake) => {
      if (snake.bot && snake.alive) {
        const others = new Set(blocked);
        // Не врезаться в голову соперника на следующем шаге.
        snakes.forEach((other) => {
          if (other === snake || !other.alive) return;
          const [ox, oy] = other.body[0];
          Object.values(DIRS).forEach(([dx, dy]) => others.add((oy + dy) * N + ox + dx));
        });
        snake.dir = botStep(snake.body[0], snake.dir, others, apples);
      }
    });
    // Все головы двигаются одновременно.
    const heads = snakes.map((snake) => {
      if (!snake.alive) return null;
      const [dx, dy] = DIRS[snake.dir];
      return [snake.body[0][0] + dx, snake.body[0][1] + dy];
    });
    // Хвосты, которые уйдут на этом шаге, не мешают.
    const tails = new Set();
    snakes.forEach((snake) => {
      if (snake.alive && !snake.grow) {
        const [x, y] = snake.body[snake.body.length - 1];
        tails.add(y * N + x);
      }
    });
    const dead = new Set();
    heads.forEach((head, i) => {
      if (!head) return;
      const [x, y] = head;
      const key = y * N + x;
      if (x < 0 || y < 0 || x >= N || y >= N || (blocked.has(key) && !tails.has(key))) dead.add(i);
      heads.forEach((other, j) => { if (j !== i && other && other[0] === x && other[1] === y) dead.add(i); });
    });
    snakes.forEach((snake, i) => {
      if (!snake.alive) return;
      if (dead.has(i)) {
        snake.alive = false;
        if (snake.bot) respawnBot(snake);
        return;
      }
      snake.body.unshift(heads[i]);
      const eaten = apples.findIndex(([x, y]) => x === heads[i][0] && y === heads[i][1]);
      if (eaten >= 0) {
        snake.grow += 1;
        apples.splice(eaten, 1);
        if (!snake.bot) {
          score += 1;
          root.querySelector('[data-score]').textContent = String(score);
          tap('light');
          speed = Math.max(0.065, speed * 0.97);
        }
      }
      if (snake.grow) snake.grow -= 1;
      else snake.body.pop();
    });
    while (apples.length < (mode === 'bots' ? 3 : 1)) {
      const spot = freeCell();
      if (!spot) break;
      apples.push(spot);
    }
    if (!me.alive) gameOver();
  }

  function gameOver() {
    running = false;
    notify('error');
    const record = saveBest('snake', score);
    if (record) root.querySelector('[data-best]').textContent = String(score);
    overlay(stage, `<div class="game-over-title">${record ? 'Новый рекорд!' : 'Конец игры'}</div>
        <div class="muted">Яблок: ${score}</div>`, [{ label: 'Ещё раз', run: begin }]);
  }

  function draw() {
    g.fillStyle = colors.bg;
    g.fillRect(0, 0, size, size);
    g.strokeStyle = colors.grid;
    g.globalAlpha = 0.35;
    g.lineWidth = 0.5;
    for (let i = 1; i < N; i += 1) {
      g.beginPath(); g.moveTo(i * cell, 0); g.lineTo(i * cell, size); g.stroke();
      g.beginPath(); g.moveTo(0, i * cell); g.lineTo(size, i * cell); g.stroke();
    }
    g.globalAlpha = 1;
    g.font = `${cell * 0.85}px system-ui`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    apples.forEach(([x, y]) => g.fillText('🍎', x * cell + cell / 2, y * cell + cell / 2 + 1));
    snakes.forEach((snake) => {
      if (!snake.alive) return;
      g.fillStyle = snake.color;
      snake.body.forEach(([x, y], i) => {
        g.globalAlpha = i === 0 ? 1 : 0.85 - Math.min(0.35, i * 0.015);
        const pad = i === 0 ? 0.5 : 1.5;
        g.beginPath();
        g.roundRect(x * cell + pad, y * cell + pad, cell - pad * 2, cell - pad * 2, cell * 0.3);
        g.fill();
      });
      g.globalAlpha = 1;
      const [hx, hy] = snake.body[0];
      g.fillStyle = '#fff';
      g.beginPath();
      g.arc(hx * cell + cell / 2, hy * cell + cell / 2, cell * 0.14, 0, Math.PI * 2);
      g.fill();
    });
  }

  function turn(dir) {
    const me = snakes[0];
    if (!running || !me) return;
    const last = me.queue[me.queue.length - 1] || me.dir;
    if (dir === last || dir === OPPOSITE[last] || me.queue.length > 2) return;
    me.queue.push(dir);
  }

  function begin() {
    reset();
    acc = 0;
    running = true;
  }

  onDirection(canvas, ctx.signal, turn);
  root.querySelector('.dpad').addEventListener('pointerdown', (event) => {
    const button = event.target.closest('[data-dir]');
    if (!button) return;
    event.preventDefault();
    tap('soft');
    turn(button.dataset.dir);
  }, { signal: ctx.signal });
  bindChips(root, 'mode', (value) => {
    mode = value;
    save('snake-mode', mode);
    root.querySelector('.game-over')?.remove();
    begin();
  });

  frames(ctx, (dt) => {
    if (running) {
      acc += dt;
      while (acc >= speed && running) {
        acc -= speed;
        step();
      }
    }
    draw();
  });
  reset();
  overlay(stage, '<div class="game-over-title">Змейка</div><div class="muted">Собирай яблоки и не врезайся.</div>',
    [{ label: 'Играть', run: begin }]);
}
