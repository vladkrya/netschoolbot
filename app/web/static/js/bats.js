// Хеллоуин: полёт летучих мышей. У каждого пролёта свой маршрут —
// слева или справа, волной, нырком, с петлёй, разворотом на полпути;
// своя высота и скорость. Двигаем сам <span> через Web Animations
// (transform), крылья машут у его ::before (halloween.css): если махать
// масштабом того же элемента, вместе с крыльями сжимается и сдвиг.
'use strict';

const KINDS = ['wave', 'wave', 'dip', 'loop', 'zigzag', 'turn'];

/** Наклон по ходу полёта: подъём — нос вверх, спуск — вниз; не круче 28°. */
function tilt(dx, dy) {
  if (!dx) return 0;
  const deg = Math.atan(dy / dx) * 180 / Math.PI;
  return Math.max(-28, Math.min(28, Math.round(deg)));
}

/** Точки [x, y, доля пути] → ключевые кадры transform с наклоном. */
function toFrames(points) {
  return points.map(([x, y, offset], i) => {
    const [nx, ny] = points[Math.min(i + 1, points.length - 1)];
    const [px, py] = points[Math.max(i - 1, 0)];
    const angle = tilt(nx - px, ny - py);
    return { offset, transform: `translate(${Math.round(x)}px, ${Math.round(y)}px) rotate(${angle}deg)` };
  });
}

/** Маршрут мыши над шапкой шириной width. rand — Math.random или подмена
 *  в тестах. Возвращает ключевые кадры и длительность в мс. */
export function batFlight(width, rand = Math.random) {
  const kind = KINDS[Math.floor(rand() * KINDS.length)];
  const rtl = rand() < 0.5;
  const out = 60;
  const from = rtl ? width + out : -out;
  const to = rtl ? -out : width + out;
  const at = (share) => from + (to - from) * share;
  const base = Math.round(rand() * 14) - 4;
  const amp = 4 + rand() * 8;
  let points;
  if (kind === 'dip') {
    const deep = 12 + rand() * 10;
    points = [[at(0), base - 6, 0], [at(0.35), base + deep * 0.7, 0.3], [at(0.5), base + deep, 0.45],
      [at(0.7), base - 4, 0.7], [at(1), base - 10, 1]];
  } else if (kind === 'loop') {
    // Петля посередине: мышь уходит вверх и описывает круг по ходу полёта.
    const r = 10 + rand() * 4;
    const cx = at(0.5);
    const dir = rtl ? -1 : 1;
    points = [[at(0), base, 0], [at(0.48), base, 0.4]];
    for (let step = 1; step <= 8; step += 1) {
      const a = (Math.PI / 2) + (step / 8) * Math.PI * 2;
      points.push([cx - dir * Math.cos(a) * r, base - r + Math.sin(a) * r, 0.4 + step * 0.03]);
    }
    points.push([at(1), base + amp * 0.5, 1]);
  } else if (kind === 'zigzag') {
    points = [];
    for (let i = 0; i <= 8; i += 1) points.push([at(i / 8), base + (i % 2 ? amp : -amp) * 0.8, i / 8]);
  } else if (kind === 'turn') {
    // Залетает, кружит на месте и улетает обратно, откуда прилетела.
    const far = 0.35 + rand() * 0.3;
    points = [[at(0), base, 0], [at(far * 0.7), base + amp, 0.3], [at(far), base - amp, 0.48],
      [at(far * 0.85), base - amp * 1.4, 0.56], [at(far * 0.6), base + amp * 0.3, 0.7], [at(0), base - amp, 1]];
  } else {
    points = [0, 0.25, 0.5, 0.75, 1].map((share, i) => [at(share), base + (i % 2 ? amp : -amp * 0.5), share]);
  }
  // Скорость — 45–110 px/с по прямой; зигзаг суетливее, разворот — путь вдвое.
  const speed = (45 + rand() * 65) * (kind === 'zigzag' ? 1.4 : 1);
  const span = (width + out * 2) * (kind === 'turn' ? 0.9 : 1);
  return { kind, rtl, frames: toFrames(points), duration: Math.round(span / speed * 1000) };
}

/** Мыши летают, пока слой в документе и анимации разрешены; между
 *  пролётами — пауза. Первый пролёт начинается с середины, чтобы мыши
 *  были видны сразу. */
export function flyBats(decor, allowed) {
  decor.querySelectorAll('.hw-bat').forEach((bat, index) => {
    const fly = (first) => {
      if (!bat.isConnected || !allowed() || !bat.animate) return;
      // Уже летит (анимации включили повторно) — второй круг не заводим.
      if (first && bat.getAnimations().length) return;
      const flight = batFlight(decor.clientWidth || innerWidth);
      const animation = bat.animate(flight.frames, {
        duration: flight.duration,
        easing: 'linear',
        delay: first ? 0 : 600 + Math.random() * 6000,
        fill: 'backwards',
      });
      if (first) animation.currentTime = flight.duration * (0.1 + index * 0.27 % 0.8);
      animation.finished.then(() => fly(false), () => {});
    };
    fly(true);
  });
}

const SWOOPS = [
  // снизу слева — вверх направо (как раньше)
  [[-140, 70, 0.6, 0], [35, 35, 1.15, 0.4], [65, 22, 0.9, 0.7], [110, -10, 0.5, 1]],
  // снизу справа — вверх налево
  [[110, 75, 0.6, 0], [62, 40, 1.15, 0.4], [30, 26, 0.9, 0.7], [-140, -10, 0.5, 1]],
  // сверху слева пикирует к середине и уходит вверх направо
  [[-140, 5, 0.5, 0], [30, 45, 1.2, 0.45], [60, 50, 1.1, 0.6], [110, 8, 0.5, 1]],
  // сверху справа — ныряет и улетает налево вниз
  [[110, 10, 0.5, 0], [60, 30, 1.1, 0.4], [35, 40, 1.2, 0.65], [-140, 80, 0.6, 1]],
  // прямо на зрителя: из глубины в центре, растёт и уходит вниз
  [[45, 20, 0.2, 0], [42, 30, 0.6, 0.35], [38, 45, 1.3, 0.75], [30, 110, 2, 1]],
];

/** Выключили анимации — мыши замирают там, где их ставит halloween.css. */
export function landBats() {
  document.querySelectorAll('.hw-bat, .hw-swoop').forEach((el) => el.getAnimations?.().forEach((a) => a.cancel()));
}

/** Маршрут большой мыши: x в vw (числа меньше −100 — пиксели за краем), y в vh. */
export function swoopFlight(rand = Math.random) {
  const route = SWOOPS[Math.floor(rand() * SWOOPS.length)];
  const x = (value) => (value <= -100 ? `${value}px` : value >= 110 ? 'calc(100vw + 140px)' : `${value}vw`);
  return route.map(([vx, vy, scale, offset], i) => {
    const next = route[Math.min(i + 1, route.length - 1)];
    const prev = route[Math.max(i - 1, 0)];
    const dx = (next[0] < -100 ? -20 : next[0]) - (prev[0] < -100 ? -20 : prev[0]);
    return { offset, transform: `translate(${x(vx)}, ${vy}vh) rotate(${tilt(dx, next[1] - prev[1])}deg) scale(${scale})` };
  });
}

/** Большая мышь с горящими глазами пролетает через экран по одному из
 *  маршрутов. Возвращает элемент (или null, если анимировать нечем). */
export function swoopOnce() {
  const bat = document.createElement('div');
  bat.className = 'hw-swoop';
  bat.ariaHidden = 'true';
  if (!bat.animate) return null;
  document.body.append(bat);
  bat.animate(swoopFlight(), { duration: 2400, easing: 'cubic-bezier(.45, .05, .55, .95)', fill: 'both' })
    .finished.then(() => bat.remove(), () => bat.remove());
  return bat;
}
