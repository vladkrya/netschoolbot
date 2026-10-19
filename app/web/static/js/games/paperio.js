// Paper.io: выходи со своей территории, рисуй хвост и возвращайся — всё,
// что обведено, становится твоим. Наехал на чужой хвост — соперник
// выбывает; на свой или в край поля — выбываешь сам. Три бота играют так же
// и охотятся за вашим хвостом.
'use strict';

import { DIRS, best, fitCanvas, frames, notify, onDirection, overlay, rand, saveBest, tap, themeColor } from './kit.js';

const N = 48;
const VIEW = 22;
const STEP = 0.12;
const OPPOSITE = { up: 'down', down: 'up', left: 'right', right: 'left' };
const PLAYERS = [
  { name: 'Вы', color: null },
  { name: 'Рыжий', color: '#f08a24' },
  { name: 'Зелёный', color: '#3fb950' },
  { name: 'Фиолетовый', color: '#a46ee0' },
];

/** Вернулся домой: хвост и всё, что он обвёл, — твоё. Обведено то, до чего
 *  нельзя дойти от края поля, не наступив на свою территорию. */
export function capture(owner, trail, id, n = N) {
  for (let i = 0; i < owner.length; i += 1) {
    if (trail[i] === id) {
      owner[i] = id;
      trail[i] = 0;
    }
  }
  const outside = new Uint8Array(owner.length);
  const stack = [];
  for (let x = 0; x < n; x += 1) {
    for (const y of [0, n - 1]) stack.push(y * n + x);
  }
  for (let y = 0; y < n; y += 1) {
    for (const x of [0, n - 1]) stack.push(y * n + x);
  }
  while (stack.length) {
    const i = stack.pop();
    if (outside[i] || owner[i] === id) continue;
    outside[i] = 1;
    const x = i % n;
    const y = Math.floor(i / n);
    if (x > 0) stack.push(i - 1);
    if (x < n - 1) stack.push(i + 1);
    if (y > 0) stack.push(i - n);
    if (y < n - 1) stack.push(i + n);
  }
  let gained = 0;
  for (let i = 0; i < owner.length; i += 1) {
    if (!outside[i] && owner[i] !== id) {
      owner[i] = id;
      gained += 1;
    }
  }
  return gained;
}

export function start(root, ctx) {
  root.innerHTML = `<div class="game">
      <div class="game-stats"><span>Ваша территория: <b data-score>0%</b></span><span>Рекорд: <b data-best>${best('paperio')}%</b></span></div>
      <div class="pio-board" data-board></div>
      <div class="game-stage"><canvas class="game-canvas" data-canvas></canvas></div>
      <div class="dpad">
        <button data-dir="up" aria-label="Вверх">▲</button>
        <button data-dir="left" aria-label="Влево">◀</button>
        <button data-dir="down" aria-label="Вниз">▼</button>
        <button data-dir="right" aria-label="Вправо">▶</button>
      </div></div>`;
  const stage = root.querySelector('.game-stage');
  const canvas = root.querySelector('[data-canvas]');
  const width = Math.min(stage.clientWidth || 340, 440);
  const height = Math.min(width, innerHeight - 300);
  const cell = width / VIEW;
  const g = fitCanvas(canvas, width, height);
  const me = themeColor('--primary', '#3b6fc4');
  const colors = { bg: themeColor('--card', '#fff'), grid: themeColor('--border', '#ddd') };
  const palette = PLAYERS.map((p) => p.color || me);

  let owner;
  let trail;
  let players;
  let running = false;
  let acc = 0;
  let peak = 0;

  const idx = (x, y) => y * N + x;
  const inside = (x, y) => x >= 0 && y >= 0 && x < N && y < N;

  function home(p, x, y) {
    for (let dy = -1; dy <= 1; dy += 1) {
      for (let dx = -1; dx <= 1; dx += 1) owner[idx(x + dx, y + dy)] = p.id;
    }
    Object.assign(p, { x, y, px: x, py: y, alive: true, trail: [], plan: [], dir: ['up', 'down', 'left', 'right'][rand(4)] });
  }

  function spot() {
    for (let tries = 0; tries < 300; tries += 1) {
      const x = 3 + rand(N - 6);
      const y = 3 + rand(N - 6);
      let free = true;
      for (let dy = -2; dy <= 2 && free; dy += 1) {
        for (let dx = -2; dx <= 2; dx += 1) if (owner[idx(x + dx, y + dy)] || trail[idx(x + dx, y + dy)]) free = false;
      }
      if (free) return [x, y];
    }
    return [3 + rand(N - 6), 3 + rand(N - 6)];
  }

  function reset() {
    owner = new Uint8Array(N * N);
    trail = new Uint8Array(N * N);
    players = PLAYERS.map((p, i) => ({ id: i + 1, name: p.name, bot: i > 0, color: palette[i] }));
    const corners = [[8, N - 9], [N - 9, 8], [8, 8], [N - 9, N - 9]];
    players.forEach((p, i) => home(p, ...corners[i]));
    // Человек начинает лицом к центру: иначе мог сразу уехать в край поля.
    players[0].dir = 'up';
    peak = 0;
  }

  function kill(p) {
    p.alive = false;
    for (let i = 0; i < owner.length; i += 1) {
      if (owner[i] === p.id) owner[i] = 0;
      if (trail[i] === p.id) trail[i] = 0;
    }
    if (!p.bot) return;
    setTimeout(() => {
      if (!running || ctx.signal.aborted) return;
      home(p, ...spot());
    }, 2500);
  }

  /** Направление первого шага к ближайшей клетке, где goal(i) — истина. */
  function route(p, goal, limit = 400) {
    const seen = new Map([[idx(p.x, p.y), null]]);
    const queue = [[p.x, p.y]];
    while (queue.length && seen.size < limit) {
      const [x, y] = queue.shift();
      const i = idx(x, y);
      if (i !== idx(p.x, p.y) && goal(i)) {
        let cur = i;
        let first = null;
        while (seen.get(cur)) {
          first = seen.get(cur).dir;
          cur = seen.get(cur).from;
        }
        return first;
      }
      for (const [name, [dx, dy]] of Object.entries(DIRS)) {
        if (i === idx(p.x, p.y) && name === OPPOSITE[p.dir]) continue;
        const nx = x + dx;
        const ny = y + dy;
        if (!inside(nx, ny) || trail[idx(nx, ny)] === p.id || seen.has(idx(nx, ny))) continue;
        seen.set(idx(nx, ny), { from: i, dir: name });
        queue.push([nx, ny]);
      }
    }
    return null;
  }

  function think(p) {
    const away = p.trail.length;
    const human = players[0];
    // Чужой хвост рядом — в атаку.
    if (human.alive && human.trail.length) {
      const near = human.trail.some((i) => Math.abs((i % N) - p.x) + Math.abs(Math.floor(i / N) - p.y) <= 5);
      if (near) {
        const dir = route(p, (i) => trail[i] && trail[i] !== p.id, 120);
        if (dir) return dir;
      }
    }
    // Опасно далеко или рядом соперник — домой.
    const threat = players.some((o) => o !== p && o.alive && Math.abs(o.x - p.x) + Math.abs(o.y - p.y) <= away / 2 + 2);
    if (away && (away > 14 || threat || !p.plan.length)) {
      p.plan = [];
      return route(p, (i) => owner[i] === p.id) || p.dir;
    }
    if (!p.plan.length && owner[idx(p.x, p.y)] === p.id) {
      // Новая петля: прямо, поворот, прямо, поворот — и домой.
      const out = Object.keys(DIRS).filter((d) => d !== OPPOSITE[p.dir])[rand(3)];
      const side = (out === 'up' || out === 'down') ? ['left', 'right'][rand(2)] : ['up', 'down'][rand(2)];
      const a = 3 + rand(5);
      const b = 2 + rand(5);
      p.plan = [...Array(a).fill(out), ...Array(b).fill(side), ...Array(a).fill(OPPOSITE[out])];
    }
    const want = p.plan.shift() || p.dir;
    const [dx, dy] = DIRS[want];
    if (want === OPPOSITE[p.dir] || !inside(p.x + dx, p.y + dy) || trail[idx(p.x + dx, p.y + dy)] === p.id) {
      p.plan = [];
      return route(p, (i) => owner[i] === p.id) || p.dir;
    }
    return want;
  }

  function step() {
    players.forEach((p) => {
      if (!p.alive) return;
      if (p.bot) p.dir = think(p);
      else if (p.next) {
        p.dir = p.next;
        p.next = null;
      }
    });
    const moves = players.map((p) => {
      if (!p.alive) return null;
      const [dx, dy] = DIRS[p.dir];
      return [p.x + dx, p.y + dy];
    });
    players.forEach((p, k) => {
      if (!p.alive) return;
      const [x, y] = moves[k];
      p.px = p.x;
      p.py = p.y;
      if (!inside(x, y)) {
        kill(p);
        return;
      }
      const i = idx(x, y);
      if (trail[i] === p.id) {
        kill(p);
        return;
      }
      if (trail[i]) {
        const victim = players[trail[i] - 1];
        if (victim.alive) {
          kill(victim);
          if (!p.bot) tap('medium');
        }
      }
      // Лоб в лоб: проигрывает тот, кто не у себя дома.
      players.forEach((o, j) => {
        if (j === k || !o.alive || !moves[j] || moves[j][0] !== x || moves[j][1] !== y) return;
        if (owner[i] !== p.id) kill(p);
      });
      if (!p.alive) return;
      p.x = x;
      p.y = y;
      if (owner[i] === p.id) {
        if (p.trail.length) {
          capture(owner, trail, p.id);
          p.trail = [];
          if (!p.bot) tap('light');
          // У кого отобрали всю территорию — тот выбывает.
          players.forEach((o) => { if (o.alive && o !== p && !owner.includes(o.id)) kill(o); });
        }
      } else {
        trail[i] = p.id;
        p.trail.push(i);
      }
    });
    // Хвост, через который проехали и стёрли, больше не хвост.
    players.forEach((p) => { p.trail = p.trail.filter((i) => trail[i] === p.id); });
    const mine = owner.reduce((sum, v) => sum + (v === 1), 0);
    const percent = Math.round((mine / (N * N)) * 1000) / 10;
    peak = Math.max(peak, percent);
    root.querySelector('[data-score]').textContent = `${percent}%`;
    const counts = players.map((p) => owner.reduce((sum, v) => sum + (v === p.id), 0));
    root.querySelector('[data-board]').innerHTML = players.map((p, i) => `<span style="--c:${p.color}">${p.name} ${
      p.alive ? `${Math.round((counts[i] / (N * N)) * 100)}%` : '💥'}</span>`).join('');
    if (!players[0].alive) gameOver();
    else if (percent >= 50) gameOver(true);
  }

  function gameOver(won = false) {
    running = false;
    notify(won ? 'success' : 'error');
    const score = Math.round(peak);
    const record = saveBest('paperio', score);
    root.querySelector('[data-best]').textContent = `${best('paperio')}%`;
    overlay(stage, `<div class="game-over-title">${won ? 'Половина поля ваша!' : record ? 'Новый рекорд!' : 'Вас подрезали'}</div>
        <div class="muted">Лучший захват: ${score}%</div>`, [{ label: 'Ещё раз', run: begin }]);
  }

  function draw(t) {
    const p = players[0];
    const cx = p.px + (p.x - p.px) * t;
    const cy = p.py + (p.y - p.py) * t;
    const ox = cx - VIEW / 2 + 0.5;
    const oy = cy - (height / cell) / 2 + 0.5;
    g.fillStyle = colors.bg;
    g.fillRect(0, 0, width, height);
    const x0 = Math.max(0, Math.floor(ox));
    const y0 = Math.max(0, Math.floor(oy));
    const x1 = Math.min(N, Math.ceil(ox + VIEW) + 1);
    const y1 = Math.min(N, Math.ceil(oy + height / cell) + 1);
    // Край поля.
    g.fillStyle = colors.grid;
    g.globalAlpha = 0.5;
    if (ox < 0) g.fillRect(0, 0, -ox * cell, height);
    if (oy < 0) g.fillRect(0, 0, width, -oy * cell);
    if (ox + VIEW > N) g.fillRect((N - ox) * cell, 0, width, height);
    if (oy + height / cell > N) g.fillRect(0, (N - oy) * cell, width, height);
    g.globalAlpha = 1;
    for (let y = y0; y < y1; y += 1) {
      for (let x = x0; x < x1; x += 1) {
        const i = idx(x, y);
        const sx = (x - ox) * cell;
        const sy = (y - oy) * cell;
        if (owner[i]) {
          g.fillStyle = palette[owner[i] - 1];
          g.globalAlpha = 0.55;
          g.fillRect(sx, sy, cell + 0.5, cell + 0.5);
        }
        if (trail[i]) {
          g.fillStyle = palette[trail[i] - 1];
          g.globalAlpha = 0.3;
          g.fillRect(sx + cell * 0.15, sy + cell * 0.15, cell * 0.7, cell * 0.7);
        }
      }
    }
    g.globalAlpha = 1;
    players.forEach((o) => {
      if (!o.alive) return;
      const hx = (o.px + (o.x - o.px) * t - ox) * cell;
      const hy = (o.py + (o.y - o.py) * t - oy) * cell;
      g.fillStyle = o.color;
      g.strokeStyle = 'rgba(0,0,0,.35)';
      g.lineWidth = 1.5;
      g.beginPath();
      g.roundRect(hx + 1, hy + 1, cell - 2, cell - 2, cell * 0.25);
      g.fill();
      g.stroke();
    });
  }

  function turn(dir) {
    const p = players?.[0];
    if (!running || !p?.alive) return;
    const current = p.next || p.dir;
    if (dir === OPPOSITE[current] && p.trail.length) return;
    p.next = dir;
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

  frames(ctx, (dt) => {
    if (running) {
      acc += dt;
      while (acc >= STEP && running) {
        acc -= STEP;
        step();
      }
    }
    draw(running ? Math.min(1, acc / STEP) : 1);
  });
  reset();
  overlay(stage, `<div class="game-over-title">Paper.io</div>
      <div class="muted">Обводи клетки и возвращайся домой. Не дай ботам наехать на твой хвост!</div>`,
  [{ label: 'Играть', run: begin }]);
}
