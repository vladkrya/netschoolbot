// Paper.io: выходи со своей территории, рисуй хвост и возвращайся — всё,
// что обведено, становится твоим. Наехал на чужой хвост — соперник
// выбывает; на свой или в край поля — выбываешь сам. Три бота играют так же
// и охотятся за вашим хвостом. Территория рисуется объёмными плитками, как
// в оригинале, камера плавно едет за игроком, в углу — мини-карта.
'use strict';

import {
  DIRS, best, bump, fitCanvas, frames, hud, intro, onDirection, overlay, particles, popText, quake, rand, saveBest, sfx,
  shade, tap,
} from './kit.js';

const N = 48;
const VIEW = 18;
const STEP = 0.125;
const OPPOSITE = { up: 'down', down: 'up', left: 'right', right: 'left' };
const PLAYERS = [
  { name: 'Вы', color: '#4a7cf3' },
  { name: 'Рыжий', color: '#ff8a3d' },
  { name: 'Мятный', color: '#2ec4a0' },
  { name: 'Сиреневый', color: '#b26cf0' },
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

const countOf = (owner, id) => owner.reduce((sum, v) => sum + (v === id), 0);

export function start(root, ctx) {
  root.innerHTML = `<div class="game">
      ${hud([['score', 'Территория', '0%'], ['kills', 'Срезано', 0], ['best', '🏆', `${best('paperio')}%`]])}
      <div class="game-stage pio-stage"><canvas class="game-canvas" data-canvas></canvas>
        <div class="pio-board" data-board></div><canvas class="pio-map" data-map></canvas></div>
      <div class="dpad">
        <button data-dir="up" aria-label="Вверх"><i></i></button>
        <button data-dir="left" aria-label="Влево"><i></i></button>
        <button data-dir="down" aria-label="Вниз"><i></i></button>
        <button data-dir="right" aria-label="Вправо"><i></i></button>
      </div></div>`;
  const stage = root.querySelector('.game-stage');
  const canvas = root.querySelector('[data-canvas]');
  const width = Math.min(root.querySelector('.game').clientWidth || 340, 460);
  const height = Math.round(Math.min(width * 1.15, innerHeight - 330));
  const cell = width / VIEW;
  const g = fitCanvas(canvas, width, height);
  const map = fitCanvas(root.querySelector('[data-map]'), 64, 64);
  const mini = document.createElement('canvas');
  mini.width = N;
  mini.height = N;
  const miniCtx = mini.getContext('2d');
  const fx = particles();
  const rgb = PLAYERS.map(({ color }) => [1, 3, 5].map((o) => parseInt(color.slice(o, o + 2), 16)));

  let owner;
  let trail;
  let flashAt;
  let players;
  let running = false;
  let acc = 0;
  let peak = 0;
  let kills = 0;
  let camX = 0;
  let camY = 0;

  const idx = (x, y) => y * N + x;
  const inside = (x, y) => x >= 0 && y >= 0 && x < N && y < N;

  function home(p, x, y) {
    for (let dy = -1; dy <= 1; dy += 1) {
      for (let dx = -1; dx <= 1; dx += 1) {
        owner[idx(x + dx, y + dy)] = p.id;
        flashAt[idx(x + dx, y + dy)] = performance.now();
      }
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
    flashAt = new Float64Array(N * N);
    players = PLAYERS.map((p, i) => ({ id: i + 1, name: p.name, bot: i > 0, color: p.color }));
    const corners = [[9, N - 10], [N - 10, 9], [9, 9], [N - 10, N - 10]];
    players.forEach((p, i) => home(p, ...corners[i]));
    // Человек начинает лицом к центру: иначе мог сразу уехать в край поля.
    players[0].dir = 'up';
    camX = players[0].x;
    camY = players[0].y;
    peak = 0;
    kills = 0;
    bump(root.querySelector('[data-kills]'), 0);
    paintMap();
  }

  const screenOf = (x, y) => [(x - camX + VIEW / 2 - 0.5) * cell, (y - camY + height / cell / 2 - 0.5) * cell];

  function kill(p, by = null) {
    p.alive = false;
    const [sx, sy] = screenOf(p.x, p.y);
    fx.burst(sx + cell / 2, sy + cell / 2, p.color, 30, 4);
    for (let i = 0; i < owner.length; i += 1) {
      if (owner[i] === p.id) owner[i] = 0;
      if (trail[i] === p.id) trail[i] = 0;
    }
    if (by && !by.bot) {
      kills += 1;
      bump(root.querySelector('[data-kills]'), kills);
      popText(stage, `Срезал: ${p.name}!`, { y: '30%', cls: 'gold' });
      sfx('good');
      tap('medium');
    } else if (p.bot) {
      sfx('drop');
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

  function paintMap() {
    const image = miniCtx.createImageData(N, N);
    for (let i = 0; i < owner.length; i += 1) {
      const o = owner[i] || trail[i];
      const [r, gg, b] = o ? rgb[o - 1] : [236, 232, 222];
      image.data.set([r, gg, b, o && !owner[i] ? 140 : 255], i * 4);
    }
    miniCtx.putImageData(image, 0, 0);
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
        if (victim.alive) kill(victim, p);
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
          const before = owner.slice();
          capture(owner, trail, p.id);
          const now = performance.now();
          for (let c = 0; c < owner.length; c += 1) if (owner[c] !== before[c]) flashAt[c] = now;
          p.trail = [];
          if (!p.bot) {
            tap('light');
            const share = Math.round(((countOf(owner, p.id) - countOf(before, p.id)) / owner.length) * 1000) / 10;
            sfx(share >= 3 ? 'line' : 'coin');
            if (share >= 0.5) popText(stage, `+${share}%`, { y: '42%' });
          }
          // У кого отобрали всю территорию — тот выбывает.
          players.forEach((o) => { if (o.alive && o !== p && !owner.includes(o.id)) kill(o, p); });
        }
      } else {
        trail[i] = p.id;
        p.trail.push(i);
      }
    });
    // Хвост, через который проехали и стёрли, больше не хвост.
    players.forEach((p) => { p.trail = p.trail.filter((i) => trail[i] === p.id); });
    const counts = players.map((p) => countOf(owner, p.id));
    const percent = Math.round((counts[0] / (N * N)) * 1000) / 10;
    peak = Math.max(peak, percent);
    root.querySelector('[data-score]').textContent = `${percent}%`;
    const ranking = players.map((p, i) => ({ p, share: counts[i] / (N * N) })).sort((a, b) => b.share - a.share);
    root.querySelector('[data-board]').innerHTML = ranking.map(({ p, share }, place) => `<div class="pio-row${p.bot ? '' : ' me'}" style="--c:${p.color};--w:${Math.min(100, Math.max(6, share * 200))}%">
        <span>${place + 1}. ${p.name}</span><b>${p.alive ? `${(share * 100).toFixed(1)}%` : '💥'}</b></div>`).join('');
    paintMap();
    if (!players[0].alive) gameOver();
    else if (percent >= 50) gameOver(true);
  }

  function gameOver(won = false) {
    running = false;
    if (!won) quake(stage, 10);
    const score = Math.round(peak);
    const record = saveBest('paperio', score);
    root.querySelector('[data-best]').textContent = `${best('paperio')}%`;
    setTimeout(() => {
      if (ctx.signal.aborted) return;
      overlay(stage, `<div class="game-over-title">${won ? 'Половина поля ваша!' : record ? 'Новый рекорд!' : 'Вас подрезали'}</div>
          <div class="game-over-text">Лучший захват: <b>${score}%</b> · срезано соперников: <b>${kills}</b></div>`,
      [{ label: 'Ещё раз', run: begin }], { icon: won || record ? '🏆' : '✂️', win: won || record, lose: !won && !record });
    }, won ? 200 : 700);
  }

  function draw(t, dt) {
    const me = players[0];
    const tx = me.px + (me.x - me.px) * t;
    const ty = me.py + (me.y - me.py) * t;
    // Камера догоняет игрока плавно, а не дёргается по клеткам.
    camX += (tx - camX) * Math.min(1, dt * 10);
    camY += (ty - camY) * Math.min(1, dt * 10);
    const rows = height / cell;
    const ox = camX - VIEW / 2 + 0.5;
    const oy = camY - rows / 2 + 0.5;
    g.fillStyle = '#cfc8b6';
    g.fillRect(0, 0, width, height);
    // Бумага: светлое поле с тонкой сеткой.
    const fx0 = Math.max(0, -ox) * cell;
    const fy0 = Math.max(0, -oy) * cell;
    const fx1 = Math.min(width, (N - ox) * cell);
    const fy1 = Math.min(height, (N - oy) * cell);
    g.fillStyle = '#f4f1e9';
    g.fillRect(fx0, fy0, fx1 - fx0, fy1 - fy0);
    g.strokeStyle = 'rgba(120, 110, 90, .09)';
    g.lineWidth = 1;
    for (let x = Math.ceil(ox); x <= Math.floor(ox + VIEW) + 1; x += 1) {
      if (x < 0 || x > N) continue;
      g.beginPath(); g.moveTo((x - ox) * cell, fy0); g.lineTo((x - ox) * cell, fy1); g.stroke();
    }
    for (let y = Math.ceil(oy); y <= Math.floor(oy + rows) + 1; y += 1) {
      if (y < 0 || y > N) continue;
      g.beginPath(); g.moveTo(fx0, (y - oy) * cell); g.lineTo(fx1, (y - oy) * cell); g.stroke();
    }
    const x0 = Math.max(0, Math.floor(ox));
    const y0 = Math.max(0, Math.floor(oy));
    const x1 = Math.min(N, Math.ceil(ox + VIEW) + 1);
    const y1 = Math.min(N, Math.ceil(oy + rows) + 1);
    const now = performance.now();
    const lift = cell * 0.18;
    // Территория — приподнятые плитки: верх цветом, снизу кромка темнее.
    for (let y = y0; y < y1; y += 1) {
      for (let x = x0; x < x1; x += 1) {
        const i = idx(x, y);
        const o = owner[i];
        if (!o) continue;
        const color = PLAYERS[o - 1].color;
        const sx = (x - ox) * cell;
        const sy = (y - oy) * cell;
        const edge = (y + 1 < N ? owner[i + N] : 0) !== o;
        if (edge) {
          g.fillStyle = shade(color, -0.3);
          g.fillRect(sx, sy + cell - lift, cell + 0.6, lift + 0.6);
        }
        const age = (now - flashAt[i]) / 400;
        g.fillStyle = age < 1 ? shade(color, 0.55 * (1 - age)) : color;
        g.fillRect(sx, sy - (edge ? lift : 0), cell + 0.6, cell + 0.6);
      }
    }
    // Хвосты — полупрозрачные дорожки.
    g.globalAlpha = 0.5;
    for (let y = y0; y < y1; y += 1) {
      for (let x = x0; x < x1; x += 1) {
        const o = trail[idx(x, y)];
        if (!o) continue;
        g.fillStyle = PLAYERS[o - 1].color;
        g.fillRect((x - ox) * cell, (y - oy) * cell, cell + 0.6, cell + 0.6);
      }
    }
    g.globalAlpha = 1;
    players.forEach((p) => {
      if (!p.alive) return;
      const hx = (p.px + (p.x - p.px) * t - ox) * cell;
      const hy = (p.py + (p.y - p.py) * t - oy) * cell;
      const s = cell * 1.05;
      const off = (s - cell) / 2;
      g.fillStyle = 'rgba(0,0,0,.18)';
      g.beginPath();
      g.roundRect(hx - off + 2, hy - off + lift + 2, s, s, s * 0.22);
      g.fill();
      g.fillStyle = shade(p.color, -0.35);
      g.beginPath();
      g.roundRect(hx - off, hy - off, s, s + lift, s * 0.22);
      g.fill();
      g.fillStyle = shade(p.color, 0.15);
      g.beginPath();
      g.roundRect(hx - off, hy - off - lift * 0.3, s, s, s * 0.22);
      g.fill();
      g.font = `600 ${Math.max(10, cell * 0.42)}px -apple-system, system-ui, sans-serif`;
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      const tw = g.measureText(p.name).width + 12;
      const ly = hy - off - cell * 0.65;
      g.fillStyle = 'rgba(0,0,0,.45)';
      g.beginPath();
      g.roundRect(hx + cell / 2 - tw / 2, ly - cell * 0.3, tw, cell * 0.6, cell * 0.3);
      g.fill();
      g.fillStyle = '#fff';
      g.fillText(p.name, hx + cell / 2, ly + 1);
    });
    fx.draw(g, dt);
    // Мини-карта: всё поле и рамка того, что видно.
    map.clearRect(0, 0, 64, 64);
    map.imageSmoothingEnabled = false;
    map.drawImage(mini, 0, 0, 64, 64);
    map.strokeStyle = 'rgba(0,0,0,.6)';
    map.lineWidth = 1;
    map.strokeRect((ox / N) * 64, (oy / N) * 64, (VIEW / N) * 64, (rows / N) * 64);
    players.forEach((p) => {
      if (!p.alive) return;
      map.fillStyle = p.bot ? shade(p.color, -0.4) : '#fff';
      map.fillRect((p.x / N) * 64 - 1.5, (p.y / N) * 64 - 1.5, 3, 3);
    });
  }

  function turn(dir) {
    const p = players?.[0];
    if (!running || !p?.alive) return;
    const current = p.next || p.dir;
    if (dir === current || (dir === OPPOSITE[current] && p.trail.length)) return;
    p.next = dir;
    sfx('move');
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
    draw(running ? Math.min(1, acc / STEP) : 1, dt);
  });
  reset();
  intro(stage, {
    icon: '🟦', title: 'Paper.io',
    text: 'Выезжай со своей территории и возвращайся — обведённое станет твоим. Не дай ботам наехать на твой хвост!',
    record: best('paperio') ? `Рекорд: ${best('paperio')}%` : '', run: begin,
  });
}
