// Змейка: одна или против двух змей-ботов, которые охотятся за теми же
// яблоками. Врезался в стену, в себя или в чужое тело — конец. Боты после
// гибели возвращаются через пару секунд. Змейки ползут плавно, между
// клетками, а не прыжками.
'use strict';

import {
  DIRS, angleToDir, best, bindChips, bump, chipsRow, fitCanvas, frames, hud, intro, joystick, load, overlay, particles, popText,
  quake, rand, save, saveBest, sfx, shade, tap,
} from './kit.js';

const N = 17;
const SNAKES = [
  { color: '#4a7cf3', name: 'Вы' },
  { color: '#f2554a', name: 'Бот' },
  { color: '#b05cf0', name: 'Бот' },
];
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
      ${hud([['score', '🍎', 0], ['best', '🏆', best('snake')]])}
      ${chipsRow('mode', [['solo', 'Одна'], ['bots', 'Против ботов']], mode)}
      <div class="game-stage snake-stage"><canvas class="game-canvas" data-board></canvas></div>
      <div class="joy-hint">Ведите пальцем в любом месте экрана — змейка поползёт туда</div></div>`;
  const stage = root.querySelector('.game-stage');
  const canvas = root.querySelector('[data-board]');
  const size = Math.min(root.querySelector('.game').clientWidth || 340, 460, innerHeight - 270);
  const cell = size / N;
  const g = fitCanvas(canvas, size, size);
  const fx = particles();

  let snakes = [];
  let apples = [];
  let score = 0;
  let running = false;
  let acc = 0;
  let speed = 0.14;
  let flash = 0;
  let time = 0;

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
      if (!busy.has(y * N + x)) return [x, y, performance.now()];
    }
    return null;
  }

  function spawn(snake, x, y, dir) {
    const [dx, dy] = DIRS[dir];
    snake.body = [[x, y], [x - dx, y - dy], [x - 2 * dx, y - 2 * dy]];
    snake.prev = snake.body.map((c) => [...c]);
    snake.dir = dir;
    snake.queue = [];
    snake.alive = true;
    snake.grow = 0;
    snake.born = performance.now();
  }

  function reset() {
    snakes = [{ bot: false, ...SNAKES[0] }];
    spawn(snakes[0], 4, N - 4, 'right');
    if (mode === 'bots') {
      snakes.push({ bot: true, ...SNAKES[1] });
      snakes.push({ bot: true, ...SNAKES[2] });
      spawn(snakes[1], N - 5, 3, 'left');
      spawn(snakes[2], N - 5, Math.floor(N / 2), 'left');
    }
    apples = [];
    const count = mode === 'bots' ? 3 : 1;
    for (let i = 0; i < count; i += 1) apples.push(freeCell());
    score = 0;
    speed = 0.14;
    bump(root.querySelector('[data-score]'), 0);
  }

  function respawnBot(snake) {
    setTimeout(() => {
      if (!running || ctx.signal.aborted) return;
      const spot = freeCell();
      if (spot && spot[0] > 3 && spot[0] < N - 3) spawn(snake, spot[0], spot[1], spot[0] > N / 2 ? 'left' : 'right');
      else respawnBot(snake);
    }, 2500);
  }

  const center = ([x, y]) => [x * cell + cell / 2, y * cell + cell / 2];

  function step() {
    const me = snakes[0];
    if (me.queue.length) me.dir = me.queue.shift();
    const blocked = occupied();
    snakes.forEach((snake) => {
      snake.prev = snake.body.map((c) => [...c]);
      if (!snake.bot || !snake.alive) return;
      const others = new Set(blocked);
      // Не врезаться в голову соперника на следующем шаге.
      snakes.forEach((other) => {
        if (other === snake || !other.alive) return;
        const [ox, oy] = other.body[0];
        Object.values(DIRS).forEach(([dx, dy]) => others.add((oy + dy) * N + ox + dx));
      });
      snake.dir = botStep(snake.body[0], snake.dir, others, apples);
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
        const [px, py] = center(snake.body[0]);
        fx.burst(px, py, snake.color, 26, 4);
        if (snake.bot) {
          sfx('drop');
          popText(stage, '💥', { x: px, y: py });
          respawnBot(snake);
        }
        return;
      }
      snake.body.unshift(heads[i]);
      const eaten = apples.findIndex(([x, y]) => x === heads[i][0] && y === heads[i][1]);
      if (eaten >= 0) {
        snake.grow += 1;
        apples.splice(eaten, 1);
        const [px, py] = center(heads[i]);
        fx.burst(px, py, '#ff3b30', 12, 2.5);
        if (!snake.bot) {
          score += 1;
          bump(root.querySelector('[data-score]'), score);
          popText(stage, '+1', { x: px, y: py });
          sfx('eat');
          tap('light');
          speed = Math.max(0.07, speed * 0.975);
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
    flash = 1;
    quake(stage, 10);
    const record = saveBest('snake', score);
    bump(root.querySelector('[data-best]'), best('snake'));
    setTimeout(() => {
      if (ctx.signal.aborted) return;
      overlay(stage, `<div class="game-over-title">${record ? 'Новый рекорд!' : 'Конец игры'}</div>
          <div class="game-over-text">Съедено яблок: <b>${score}</b></div>`,
      [{ label: 'Ещё раз', run: begin }], { icon: record ? '🏆' : '🐍', win: record, lose: !record });
    }, 650);
  }

  // Шахматное поле в двух оттенках зелёного, как в классической змейке.
  const board = document.createElement('canvas');
  const bg = fitCanvas(board, size, size);
  for (let y = 0; y < N; y += 1) {
    for (let x = 0; x < N; x += 1) {
      bg.fillStyle = (x + y) % 2 ? '#a2d149' : '#aad751';
      bg.fillRect(x * cell, y * cell, cell + 0.5, cell + 0.5);
    }
  }

  function drawSnake(snake, t) {
    if (!snake.alive) return;
    const points = snake.body.map((c, i) => {
      const from = snake.prev[i] || snake.prev[snake.prev.length - 1];
      return center([from[0] + (c[0] - from[0]) * t, from[1] + (c[1] - from[1]) * t]);
    });
    // Хвост подтягивается плавно: последняя точка — между старым и новым хвостом.
    const width = cell * 0.74;
    g.lineCap = 'round';
    g.lineJoin = 'round';
    g.strokeStyle = shade(snake.color, -0.25);
    g.lineWidth = width + 3;
    g.beginPath();
    points.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
    g.stroke();
    g.strokeStyle = snake.color;
    g.lineWidth = width;
    g.stroke();
    // Блик вдоль спины.
    g.strokeStyle = 'rgba(255,255,255,.22)';
    g.lineWidth = width * 0.28;
    g.beginPath();
    points.forEach(([x, y], i) => (i ? g.lineTo(x - width * 0.12, y - width * 0.12) : g.moveTo(x - width * 0.12, y - width * 0.12)));
    g.stroke();
    // Голова с глазами, которые смотрят по ходу.
    const [hx, hy] = points[0];
    const [dx, dy] = DIRS[snake.dir];
    g.fillStyle = snake.color;
    g.beginPath();
    g.arc(hx, hy, width * 0.62, 0, Math.PI * 2);
    g.fill();
    const side = [-dy, dx];
    [-1, 1].forEach((s) => {
      const ex = hx + dx * width * 0.18 + side[0] * s * width * 0.3;
      const ey = hy + dy * width * 0.18 + side[1] * s * width * 0.3;
      g.fillStyle = '#fff';
      g.beginPath();
      g.arc(ex, ey, width * 0.2, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = '#1b1b1b';
      g.beginPath();
      g.arc(ex + dx * width * 0.07, ey + dy * width * 0.07, width * 0.1, 0, Math.PI * 2);
      g.fill();
    });
  }

  function draw(t, dt) {
    g.drawImage(board, 0, 0, size, size);
    apples.forEach(([x, y, born]) => {
      const age = Math.min(1, (performance.now() - born) / 250);
      const pulse = 1 + Math.sin(time * 5 + x) * 0.05;
      const r = cell * 0.36 * age * pulse;
      const [cx, cy] = center([x, y]);
      g.fillStyle = 'rgba(0,0,0,.15)';
      g.beginPath();
      g.ellipse(cx, cy + r * 0.9, r * 0.8, r * 0.3, 0, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = '#e7471d';
      g.beginPath();
      g.arc(cx, cy, r, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = 'rgba(255,255,255,.45)';
      g.beginPath();
      g.arc(cx - r * 0.35, cy - r * 0.35, r * 0.28, 0, Math.PI * 2);
      g.fill();
      g.strokeStyle = '#5b3a1a';
      g.lineWidth = Math.max(1.5, cell * 0.07);
      g.beginPath();
      g.moveTo(cx, cy - r * 0.8);
      g.lineTo(cx + r * 0.15, cy - r * 1.3);
      g.stroke();
      g.fillStyle = '#4caf50';
      g.beginPath();
      g.ellipse(cx + r * 0.45, cy - r * 1.1, r * 0.35, r * 0.17, -0.5, 0, Math.PI * 2);
      g.fill();
    });
    snakes.slice().reverse().forEach((snake) => drawSnake(snake, t));
    fx.draw(g, dt);
    if (flash > 0) {
      g.fillStyle = `rgba(255, 60, 60, ${flash * 0.35})`;
      g.fillRect(0, 0, size, size);
      flash = Math.max(0, flash - dt * 2);
    }
  }

  function turn(dir) {
    const me = snakes[0];
    if (!running || !me) return;
    const last = me.queue[me.queue.length - 1] || me.dir;
    if (dir === last || dir === OPPOSITE[last] || me.queue.length > 2) return;
    me.queue.push(dir);
    sfx('move');
  }

  function begin() {
    reset();
    acc = 0;
    running = true;
  }

  // Палец ведёт змейку: джойстик под пальцем в любом месте экрана игры.
  joystick(root, ctx.signal, (angle) => turn(angleToDir(angle)));
  bindChips(root, 'mode', (value) => {
    mode = value;
    save('snake-mode', mode);
    stage.querySelector('.game-over')?.remove();
    begin();
  });

  frames(ctx, (dt) => {
    time += dt;
    if (running) {
      acc += dt;
      while (acc >= speed && running) {
        acc -= speed;
        step();
      }
    }
    draw(running ? Math.min(1, acc / speed) : 1, dt);
  });
  reset();
  intro(stage, {
    icon: '🐍', title: 'Змейка', text: 'Собирай яблоки и расти. Не врезайся в стены, в себя и в других змей.',
    record: best('snake') ? `Рекорд: ${best('snake')}` : '', run: begin,
  });
}
