// Paper.io: выходи со своей территории, рисуй хвост и возвращайся — всё,
// что обведено, становится твоим. Наехал на чужой хвост — соперник
// выбывает; на свой или в край поля — выбываешь сам. Три бота играют так же
// и охотятся за вашим хвостом.
//
// Двигаются свободно, в любую сторону на 360°, и поворачивают плавно, как в
// оригинале: управление — джойстик под пальцем в любом месте экрана. Поле —
// мелкая сетка: по её клеткам считаются территория, хвосты и захват, а
// хвост рисуется гладкой линией.
'use strict';

import {
  best, bump, fitCanvas, frames, hud, intro, joystick, overlay, particles, popText, quake, rand, saveBest, sfx, shade, tap,
} from './kit.js';

const N = 96;
const VIEW = 26;
const SPEED = 6.5;
const TURN = 7;
// Последние клетки своего хвоста не убивают: на крутом развороте голова
// задевает только что нарисованный хвост.
const GRACE = 4;
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

/** Разница углов в пределах (−π, π]. */
export function angleDiff(to, from) {
  let d = (to - from) % (Math.PI * 2);
  if (d > Math.PI) d -= Math.PI * 2;
  if (d <= -Math.PI) d += Math.PI * 2;
  return d;
}

const countOf = (owner, id) => owner.reduce((sum, v) => sum + (v === id), 0);

export function start(root, ctx) {
  root.innerHTML = `<div class="game">
      ${hud([['score', 'Территория', '0%'], ['kills', 'Срезано', 0], ['best', '🏆', `${best('paperio')}%`]])}
      <div class="game-stage pio-stage"><canvas class="game-canvas" data-canvas></canvas>
        <div class="pio-board" data-board></div><canvas class="pio-map" data-map></canvas></div>
      <div class="joy-hint">Ведите пальцем в любом месте экрана — в любую сторону</div></div>`;
  const stage = root.querySelector('.game-stage');
  const canvas = root.querySelector('[data-canvas]');
  const width = Math.min(root.querySelector('.game').clientWidth || 340, 480);
  const height = Math.round(Math.max(260, Math.min(width * 1.4, innerHeight - 240)));
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
  let peak = 0;
  let kills = 0;
  let camX = 0;
  let camY = 0;
  let slow = 0;

  const idx = (x, y) => y * N + x;
  const inside = (x, y) => x >= 0 && y >= 0 && x < N && y < N;

  function home(p, cx, cy) {
    const now = performance.now();
    // Стартовая территория — круг радиусом в четыре клетки.
    for (let dy = -4; dy <= 4; dy += 1) {
      for (let dx = -4; dx <= 4; dx += 1) {
        if (dx * dx + dy * dy > 15 || !inside(cx + dx, cy + dy)) continue;
        owner[idx(cx + dx, cy + dy)] = p.id;
        flashAt[idx(cx + dx, cy + dy)] = now;
      }
    }
    const angle = Math.atan2(N / 2 - cy, N / 2 - cx);
    Object.assign(p, {
      x: cx + 0.5, y: cy + 0.5, cx, cy, angle, want: angle, alive: true, trail: [], path: [], leg: null, think: 0,
    });
  }

  function spot() {
    for (let tries = 0; tries < 300; tries += 1) {
      const x = 6 + rand(N - 12);
      const y = 6 + rand(N - 12);
      let free = true;
      for (let dy = -4; dy <= 4 && free; dy += 1) {
        for (let dx = -4; dx <= 4; dx += 1) if (owner[idx(x + dx, y + dy)] || trail[idx(x + dx, y + dy)]) free = false;
      }
      if (free) return [x, y];
    }
    return [6 + rand(N - 12), 6 + rand(N - 12)];
  }

  function reset() {
    owner = new Uint8Array(N * N);
    trail = new Uint8Array(N * N);
    flashAt = new Float64Array(N * N);
    players = PLAYERS.map((p, i) => ({ id: i + 1, name: p.name, bot: i > 0, color: p.color }));
    const q = Math.round(N * 0.22);
    const corners = [[q, N - q], [N - q, q], [q, q], [N - q, N - q]];
    players.forEach((p, i) => home(p, ...corners[i]));
    camX = players[0].x;
    camY = players[0].y;
    peak = 0;
    kills = 0;
    bump(root.querySelector('[data-kills]'), 0);
    refreshStats();
  }

  const toScreen = (x, y) => [(x - camX) * cell + width / 2, (y - camY) * cell + height / 2];

  function kill(p, by = null) {
    if (!p.alive) return;
    p.alive = false;
    const [sx, sy] = toScreen(p.x, p.y);
    fx.burst(sx, sy, p.color, 30, 4);
    for (let i = 0; i < owner.length; i += 1) {
      if (owner[i] === p.id) owner[i] = 0;
      if (trail[i] === p.id) trail[i] = 0;
    }
    p.trail = [];
    p.path = [];
    if (by && !by.bot) {
      kills += 1;
      bump(root.querySelector('[data-kills]'), kills);
      popText(stage, `Срезал: ${p.name}!`, { y: '30%', cls: 'gold' });
      sfx('good');
      tap('medium');
    } else if (p.bot) {
      sfx('drop');
    }
    if (!p.bot) {
      gameOver();
      return;
    }
    setTimeout(() => {
      if (!running || ctx.signal.aborted) return;
      home(p, ...spot());
    }, 2500);
  }

  function captureFor(p) {
    const before = owner.slice();
    capture(owner, trail, p.id);
    const now = performance.now();
    for (let c = 0; c < owner.length; c += 1) if (owner[c] !== before[c]) flashAt[c] = now;
    p.trail = [];
    p.path = [];
    if (!p.bot) {
      tap('light');
      const share = Math.round(((countOf(owner, p.id) - countOf(before, p.id)) / owner.length) * 1000) / 10;
      sfx(share >= 3 ? 'line' : 'coin');
      if (share >= 0.5) popText(stage, `+${share}%`, { y: '42%' });
    }
    // У кого отобрали всю территорию — тот выбывает.
    players.forEach((o) => { if (o.alive && o !== p && !owner.includes(o.id)) kill(o, p); });
    refreshStats();
  }

  /** Голова вошла в новую клетку. */
  function enter(p, x, y) {
    p.cx = x;
    p.cy = y;
    if (!inside(x, y)) {
      kill(p);
      return;
    }
    const i = idx(x, y);
    if (trail[i] === p.id) {
      if (p.trail.lastIndexOf(i) < p.trail.length - GRACE) kill(p);
      return;
    }
    if (trail[i]) kill(players[trail[i] - 1], p);
    if (owner[i] === p.id) {
      if (p.trail.length) captureFor(p);
    } else {
      trail[i] = p.id;
      p.trail.push(i);
    }
  }

  function move(p, dt) {
    p.angle += Math.max(-TURN * dt, Math.min(TURN * dt, angleDiff(p.want, p.angle)));
    let left = SPEED * dt;
    const cos = Math.cos(p.angle);
    const sin = Math.sin(p.angle);
    if (p.leg) p.leg.left -= left;
    while (left > 0 && p.alive) {
      const d = Math.min(left, 0.35);
      left -= d;
      p.x += cos * d;
      p.y += sin * d;
      const cx = Math.floor(p.x);
      const cy = Math.floor(p.y);
      if (cx !== p.cx || cy !== p.cy) {
        // По диагонали — через соседнюю клетку: хвост без щелей по углам,
        // иначе заливка «протекала» бы сквозь него.
        if (cx !== p.cx && cy !== p.cy) enter(p, cx, p.cy);
        if (p.alive) enter(p, cx, cy);
      }
      if (p.alive && p.trail.length) {
        const last = p.path[p.path.length - 1];
        if (!last || Math.hypot(last[0] - p.x, last[1] - p.y) > 0.3) p.path.push([p.x, p.y]);
      }
    }
  }

  /** Ближайшая своя клетка — по расширяющимся квадратам вокруг головы. */
  function nearestOwn(p) {
    for (let r = 1; r < 50; r += 1) {
      let found = null;
      let bestD = Infinity;
      for (let dy = -r; dy <= r; dy += 1) {
        for (let dx = -r; dx <= r; dx += 1) {
          if (Math.abs(dx) !== r && Math.abs(dy) !== r) continue;
          const x = p.cx + dx;
          const y = p.cy + dy;
          if (!inside(x, y) || owner[idx(x, y)] !== p.id) continue;
          const dd = dx * dx + dy * dy;
          if (dd < bestD) {
            bestD = dd;
            found = [x + 0.5, y + 0.5];
          }
        }
      }
      if (found) return found;
    }
    return [N / 2, N / 2];
  }

  const aim = (p, [x, y]) => Math.atan2(y - p.y, x - p.x);

  /** Опасно ли ехать под этим углом: стена или свой хвост впереди. */
  function danger(p, angle, reach = 2.5) {
    for (let k = 1; k <= 3; k += 1) {
      const x = Math.floor(p.x + Math.cos(angle) * reach * (k / 3));
      const y = Math.floor(p.y + Math.sin(angle) * reach * (k / 3));
      if (!inside(x, y)) return true;
      const t = trail[idx(x, y)];
      if (t === p.id && p.trail.lastIndexOf(idx(x, y)) < p.trail.length - GRACE) return true;
    }
    return false;
  }

  function think(p) {
    const away = p.trail.length;
    const human = players[0];
    let want = null;
    // Хвост человека рядом — в атаку.
    if (human.alive && human.trail.length) {
      let bestD = 64;
      for (const i of human.trail) {
        const dx = (i % N) + 0.5 - p.x;
        const dy = Math.floor(i / N) + 0.5 - p.y;
        const dd = dx * dx + dy * dy;
        if (dd < bestD) {
          bestD = dd;
          want = Math.atan2(dy, dx);
        }
      }
    }
    if (want === null) {
      const threat = players.some((o) => o !== p && o.alive && Math.hypot(o.x - p.x, o.y - p.y) < away / 3 + 4);
      if (away && (away > 34 || threat || p.leg?.phase === 'home' || !p.leg)) {
        p.leg = { phase: 'home', left: Infinity };
        want = aim(p, nearestOwn(p));
      } else if (!away && owner[idx(p.cx, p.cy)] === p.id && (!p.leg || p.leg.phase === 'home')) {
        // Новая петля: наружу, вбок — и домой.
        const out = Math.atan2(N / 2 - p.y, N / 2 - p.x) + (Math.random() - 0.5) * 2.6;
        p.leg = { phase: 'out', angle: out, left: 5 + rand(8), side: Math.random() < 0.5 ? 1 : -1 };
      }
      if (p.leg?.phase === 'out' && p.leg.left <= 0) {
        p.leg = { ...p.leg, phase: 'side', angle: p.leg.angle + (Math.PI / 2) * p.leg.side, left: 4 + rand(8) };
      } else if (p.leg?.phase === 'side' && p.leg.left <= 0) {
        p.leg = { phase: 'home', left: Infinity };
      }
      if (want === null) want = p.leg && p.leg.phase !== 'home' ? p.leg.angle : aim(p, nearestOwn(p));
    }
    // Впереди стена или свой хвост — отворачиваем.
    if (danger(p, want)) {
      for (const turn of [0.6, -0.6, 1.2, -1.2, 1.8, -1.8, 2.6, -2.6]) {
        if (!danger(p, want + turn)) {
          want += turn;
          break;
        }
      }
    }
    p.want = want;
  }

  function paintMap() {
    const image = miniCtx.createImageData(N, N);
    for (let i = 0; i < owner.length; i += 1) {
      const o = owner[i] || trail[i];
      const [r, gg, b] = o ? rgb[o - 1] : [236, 232, 222];
      image.data.set([r, gg, b, o && !owner[i] ? 150 : 255], i * 4);
    }
    miniCtx.putImageData(image, 0, 0);
  }

  function refreshStats() {
    const counts = players.map((p) => countOf(owner, p.id));
    const percent = Math.round((counts[0] / (N * N)) * 1000) / 10;
    peak = Math.max(peak, percent);
    root.querySelector('[data-score]').textContent = `${percent}%`;
    const ranking = players.map((p, i) => ({ p, share: counts[i] / (N * N) })).sort((a, b) => b.share - a.share);
    root.querySelector('[data-board]').innerHTML = ranking.map(({ p, share }, place) => `<div class="pio-row${p.bot ? '' : ' me'}" style="--c:${p.color};--w:${Math.min(100, Math.max(6, share * 200))}%">
        <span>${place + 1}. ${p.name}</span><b>${p.alive ? `${(share * 100).toFixed(1)}%` : '💥'}</b></div>`).join('');
    paintMap();
    if (running && percent >= 50) gameOver(true);
  }

  function update(dt) {
    players.forEach((p) => {
      if (!p.alive || !p.bot) return;
      p.think -= dt;
      if (p.think <= 0) {
        p.think = 0.12;
        think(p);
      }
    });
    players.forEach((p) => { if (p.alive && running) move(p, dt); });
    // Лоб в лоб: выбывает тот, кто не у себя дома (оба — если оба в поле).
    for (let a = 0; a < players.length; a += 1) {
      for (let b = a + 1; b < players.length; b += 1) {
        const p = players[a];
        const o = players[b];
        if (!p.alive || !o.alive || Math.hypot(p.x - o.x, p.y - o.y) > 0.9) continue;
        const pHome = inside(p.cx, p.cy) && owner[idx(p.cx, p.cy)] === p.id;
        const oHome = inside(o.cx, o.cy) && owner[idx(o.cx, o.cy)] === o.id;
        if (!pHome) kill(p, oHome ? o : null);
        if (!oHome) kill(o, pHome ? p : null);
      }
    }
    slow -= dt;
    if (slow <= 0) {
      slow = 0.3;
      refreshStats();
    }
  }

  function gameOver(won = false) {
    if (!running) return;
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

  function draw(dt) {
    const me = players[0];
    // Камера догоняет игрока плавно.
    camX += (me.x - camX) * Math.min(1, dt * 8);
    camY += (me.y - camY) * Math.min(1, dt * 8);
    const rows = height / cell;
    const ox = camX - VIEW / 2;
    const oy = camY - rows / 2;
    g.fillStyle = '#cfc8b6';
    g.fillRect(0, 0, width, height);
    // Бумага: светлое поле с редкой сеткой.
    const fx0 = Math.max(0, -ox * cell);
    const fy0 = Math.max(0, -oy * cell);
    const fx1 = Math.min(width, (N - ox) * cell);
    const fy1 = Math.min(height, (N - oy) * cell);
    g.fillStyle = '#f4f1e9';
    g.fillRect(fx0, fy0, fx1 - fx0, fy1 - fy0);
    g.strokeStyle = 'rgba(120, 110, 90, .08)';
    g.lineWidth = 1;
    for (let x = Math.ceil(ox / 4) * 4; x <= ox + VIEW + 4; x += 4) {
      if (x < 0 || x > N) continue;
      g.beginPath(); g.moveTo((x - ox) * cell, fy0); g.lineTo((x - ox) * cell, fy1); g.stroke();
    }
    for (let y = Math.ceil(oy / 4) * 4; y <= oy + rows + 4; y += 4) {
      if (y < 0 || y > N) continue;
      g.beginPath(); g.moveTo(fx0, (y - oy) * cell); g.lineTo(fx1, (y - oy) * cell); g.stroke();
    }
    const x0 = Math.max(0, Math.floor(ox));
    const y0 = Math.max(0, Math.floor(oy));
    const x1 = Math.min(N, Math.ceil(ox + VIEW) + 1);
    const y1 = Math.min(N, Math.ceil(oy + rows) + 1);
    const now = performance.now();
    const lift = cell * 0.35;
    // Территория — приподнятые плитки: снизу кромка темнее.
    for (let y = y0; y < y1; y += 1) {
      for (let x = x0; x < x1; x += 1) {
        const i = idx(x, y);
        const o = owner[i];
        if (!o) continue;
        const color = PLAYERS[o - 1].color;
        const sx = (x - ox) * cell;
        const sy = (y - oy) * cell;
        if ((y + 1 < N ? owner[i + N] : 0) !== o) {
          g.fillStyle = shade(color, -0.3);
          g.fillRect(sx, sy + cell - lift * 0.2, cell + 0.7, lift);
        }
        const age = (now - flashAt[i]) / 450;
        g.fillStyle = age < 1 ? shade(color, 0.55 * (1 - age)) : color;
        g.fillRect(sx, sy - lift * 0.2, cell + 0.7, cell + 0.7);
      }
    }
    // Хвосты — гладкие полупрозрачные линии.
    g.lineCap = 'round';
    g.lineJoin = 'round';
    players.forEach((p) => {
      if (!p.alive || !p.path.length) return;
      g.strokeStyle = p.color;
      g.globalAlpha = 0.55;
      g.lineWidth = cell * 0.85;
      g.beginPath();
      p.path.forEach(([x, y], k) => {
        const sx = (x - ox) * cell;
        const sy = (y - oy) * cell;
        if (k) g.lineTo(sx, sy);
        else g.moveTo(sx, sy);
      });
      g.lineTo((p.x - ox) * cell, (p.y - oy) * cell);
      g.stroke();
      g.globalAlpha = 1;
    });
    players.forEach((p) => {
      if (!p.alive) return;
      const hx = (p.x - ox) * cell;
      const hy = (p.y - oy) * cell;
      const s = cell * 1.5;
      g.fillStyle = 'rgba(0,0,0,.18)';
      g.beginPath();
      g.roundRect(hx - s / 2 + 2, hy - s / 2 + lift + 2, s, s, s * 0.24);
      g.fill();
      g.fillStyle = shade(p.color, -0.35);
      g.beginPath();
      g.roundRect(hx - s / 2, hy - s / 2, s, s + lift, s * 0.24);
      g.fill();
      g.fillStyle = shade(p.color, 0.18);
      g.beginPath();
      g.roundRect(hx - s / 2, hy - s / 2 - lift * 0.3, s, s, s * 0.24);
      g.fill();
      // Стрелка — куда едет.
      g.fillStyle = 'rgba(255,255,255,.9)';
      g.beginPath();
      const a = p.angle;
      g.moveTo(hx + Math.cos(a) * s * 0.32, hy - lift * 0.3 + Math.sin(a) * s * 0.32);
      g.lineTo(hx + Math.cos(a + 2.4) * s * 0.22, hy - lift * 0.3 + Math.sin(a + 2.4) * s * 0.22);
      g.lineTo(hx + Math.cos(a - 2.4) * s * 0.22, hy - lift * 0.3 + Math.sin(a - 2.4) * s * 0.22);
      g.fill();
      g.font = `700 ${Math.max(10, cell * 0.75)}px -apple-system, system-ui, sans-serif`;
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      const tw = g.measureText(p.name).width + 12;
      const ly = hy - s * 0.5 - cell * 1.1;
      g.fillStyle = 'rgba(0,0,0,.45)';
      g.beginPath();
      g.roundRect(hx - tw / 2, ly - cell * 0.5, tw, cell, cell * 0.5);
      g.fill();
      g.fillStyle = '#fff';
      g.fillText(p.name, hx, ly + 1);
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

  function begin() {
    reset();
    running = true;
  }

  // Палец задаёт направление: джойстик под пальцем в любом месте экрана.
  joystick(root, ctx.signal, (angle) => {
    const me = players?.[0];
    if (running && me?.alive) me.want = angle;
  });

  frames(ctx, (dt) => {
    if (running) update(dt);
    draw(dt);
  });
  reset();
  intro(stage, {
    icon: '🟦', title: 'Paper.io',
    text: 'Ведите пальцем в любую сторону. Выезжайте со своей территории и возвращайтесь — обведённое станет вашим. Не дайте ботам наехать на ваш хвост!',
    record: best('paperio') ? `Рекорд: ${best('paperio')}%` : '', run: begin,
  });
}
