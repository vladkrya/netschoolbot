// Рулетка (европейская, одно зеро) на игровые фишки. Ставки — фишками на
// поле: на красное или чёрное (×2), на зеро (×36) и на любое число (×36);
// ставок на один спин можно сколько угодно. Колесо и шарик крутятся в
// разные стороны, шарик сваливается в лунку, выигравшие поля светятся.
'use strict';

import {
  addChips, bump, chips, confetti, load, notify, popText, quake, rand, refillChips, save, sfx, shake, tap, wait,
} from './kit.js';

// Порядок лунок на колесе по часовой стрелке, начиная с зеро.
export const WHEEL = [
  0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5,
  24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26,
];
export const RED = new Set([1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]);
const CHIP_VALUES = [10, 25, 50, 100, 250];
const POCKET = 360 / WHEEL.length;
const SPIN_MS = 5200;

export const colorOf = (n) => (n === 0 ? 'green' : RED.has(n) ? 'red' : 'black');

/** Сколько фишек вернёт ставка на поле при выпавшем числе (со ставкой). */
export function payout(field, stake, result) {
  if (field === 'red' || field === 'black') return colorOf(result) === field ? stake * 2 : 0;
  return field === `n${result}` ? stake * 36 : 0;
}

/** Всё, что вернут ставки {поле: фишки}. */
export const totalPayout = (bets, result) =>
  Object.entries(bets).reduce((sum, [field, stake]) => sum + payout(field, stake, result), 0);

const sum = (bets) => Object.values(bets).reduce((a, b) => a + b, 0);

function wheelSvg() {
  const pockets = WHEEL.map((n, i) => {
    const a0 = ((i - 0.5) * POCKET - 90) * Math.PI / 180;
    const a1 = ((i + 0.5) * POCKET - 90) * Math.PI / 180;
    const p = (r, a) => `${(r * Math.cos(a)).toFixed(2)} ${(r * Math.sin(a)).toFixed(2)}`;
    const fill = { green: '#1f9d55', red: '#d0262f', black: '#1b1b1f' }[colorOf(n)];
    return `<path d="M${p(96, a0)} A96 96 0 0 1 ${p(96, a1)} L${p(64, a1)} A64 64 0 0 0 ${p(64, a0)} Z" fill="${fill}"/>
      <text transform="rotate(${(i * POCKET).toFixed(2)}) translate(0 -84)" text-anchor="middle" dominant-baseline="middle">${n}</text>`;
  }).join('');
  return `<svg class="rl-rotor" viewBox="-100 -100 200 200" aria-hidden="true">
      <circle r="99" fill="url(#rl-rim)"/>${pockets}
      <circle r="64" fill="url(#rl-cone)"/>
      ${WHEEL.map((_, i) => `<line transform="rotate(${((i + 0.5) * POCKET).toFixed(2)})" y1="-96" y2="-64"/>`).join('')}
      <g class="rl-turret">${[0, 90, 180, 270].map((a) => `<rect transform="rotate(${a})" x="-3" y="-34" width="6" height="30" rx="3"/>`).join('')}
        <circle r="13"/><circle r="5" class="rl-cap"/></g>
      <defs>
        <radialGradient id="rl-rim"><stop offset=".9" stop-color="#5a3315"/><stop offset="1" stop-color="#2a1608"/></radialGradient>
        <radialGradient id="rl-cone"><stop offset="0" stop-color="#d9a441"/><stop offset=".55" stop-color="#7a4a1c"/><stop offset="1" stop-color="#3d2410"/></radialGradient>
      </defs></svg>`;
}

export function start(root, ctx) {
  let chip = Number(load('casino-chip', 25));
  if (!CHIP_VALUES.includes(chip)) chip = 25;
  let bets = {};
  let last = load('casino-last', {}) || {};
  let history = (load('casino-history', []) || []).slice(0, 12);
  let spinning = false;
  let rotor = 0;
  let orbit = 0;

  const numbers = Array.from({ length: 36 }, (_, i) => i + 1);
  root.innerHTML = `<div class="game casino">
      <div class="hud"><div class="hud-pill"><span>Фишки</span><b data-chips>${chips()}</b></div>
        <div class="hud-pill"><span>Ставка</span><b data-total>0</b></div>
        <div class="hud-pill"><span>Выигрыш</span><b data-win>0</b></div></div>
      <div class="rl-history" data-history></div>
      <div class="rl-stage">
        <div class="rl-wheel" data-wheel>${wheelSvg()}
          <div class="rl-orbit" data-orbit><span class="rl-ball" data-ball></span></div>
          <div class="rl-result" data-result hidden></div>
        </div>
      </div>
      <div class="rl-board" data-board>
        <div class="rl-outside">
          <button class="rl-field red" data-field="red"><span>Красное</span><small>×2</small></button>
          <button class="rl-field green" data-field="n0"><span>Зеро</span><small>×36</small></button>
          <button class="rl-field black" data-field="black"><span>Чёрное</span><small>×2</small></button>
        </div>
        <div class="rl-numbers">${numbers.map((n) =>
          `<button class="rl-field num ${colorOf(n)}" data-field="n${n}"><span>${n}</span></button>`).join('')}</div>
      </div>
      <div class="chip-row rl-chips">${CHIP_VALUES.map((v) =>
        `<button class="chip-btn" data-chip="${v}"><span class="chip-c c${v}">${v}</span></button>`).join('')}</div>
      <div class="rl-actions">
        <button class="game-btn ghost" data-clear>Очистить</button>
        <button class="game-btn ghost" data-repeat>Повторить</button>
        <button class="game-btn gold" data-spin>Крутить</button>
      </div>
      <button class="game-btn" data-refill hidden>🏦 Взять 1000 фишек у банка</button>
    </div>`;
  const $ = (sel) => root.querySelector(sel);
  const chipsEl = $('[data-chips]');
  const totalEl = $('[data-total]');
  const winEl = $('[data-win]');
  const rotorEl = $('.rl-rotor');
  const orbitEl = $('[data-orbit]');
  const ballEl = $('[data-ball]');
  const resultEl = $('[data-result]');
  const board = $('[data-board]');
  const spinBtn = $('[data-spin]');
  const refill = $('[data-refill]');

  function paintHistory() {
    $('[data-history]').innerHTML = history.map((n, i) =>
      `<span class="rl-dot ${colorOf(n)}${i === 0 ? ' fresh' : ''}">${n}</span>`).join('')
      || '<span class="rl-hint">Поставьте фишки на поле и крутите</span>';
  }

  function paintBets() {
    board.querySelectorAll('[data-field]').forEach((field) => {
      const stake = bets[field.dataset.field] || 0;
      let badge = field.querySelector('.rl-stake');
      if (!stake) { badge?.remove(); return; }
      if (!badge) {
        badge = document.createElement('b');
        badge.className = 'rl-stake';
        field.append(badge);
      }
      badge.textContent = stake >= 1000 ? `${Math.round(stake / 100) / 10}k` : String(stake);
    });
  }

  function sync() {
    const have = chips();
    const total = sum(bets);
    bump(chipsEl, have, { count: true });
    bump(totalEl, total);
    root.querySelectorAll('[data-chip]').forEach((button) => {
      button.classList.toggle('on', Number(button.dataset.chip) === chip);
    });
    spinBtn.disabled = spinning;
    $('[data-clear]').disabled = spinning || !total;
    $('[data-repeat]').disabled = spinning || !sum(last) || sum(last) > have;
    refill.hidden = spinning || have + total >= CHIP_VALUES[0];
    root.querySelector('.casino').classList.toggle('busy', spinning);
    paintBets();
  }

  function place(field) {
    if (spinning) return;
    if (sum(bets) + chip > chips()) {
      shake(board);
      popText(board, 'Не хватает фишек', { y: '45%' });
      return;
    }
    bets[field] = (bets[field] || 0) + chip;
    sfx('chip');
    tap('soft');
    root.querySelectorAll('.rl-field.hit').forEach((el) => el.classList.remove('hit'));
    sync();
  }

  async function spin() {
    const total = sum(bets);
    if (spinning) return;
    if (!total) {
      shake(board);
      popText(board, 'Сначала ставка', { y: '45%' });
      return;
    }
    if (total > chips()) { shake(board); return; }
    spinning = true;
    addChips(-total);
    last = { ...bets };
    save('casino-last', last);
    bump(winEl, 0);
    resultEl.hidden = true;
    root.querySelectorAll('.rl-field.hit').forEach((el) => el.classList.remove('hit'));
    sync();
    tap('medium');
    sfx('spin');

    const result = rand(WHEEL.length);
    const number = WHEEL[result];
    // Колесо — по часовой, шарик — против; шарик кончает над лункой.
    rotor += 360 * 3 + rand(360);
    const target = (((result * POCKET + rotor) % 360) + 360) % 360;
    const from = ((orbit % 360) + 360) % 360;
    orbit -= 360 * 5 + ((from - target + 360) % 360);
    rotorEl.style.transition = `transform ${SPIN_MS}ms cubic-bezier(.15, .55, .2, 1)`;
    rotorEl.style.transform = `rotate(${rotor}deg)`;
    orbitEl.style.transition = `transform ${SPIN_MS - 400}ms cubic-bezier(.2, .6, .25, 1)`;
    orbitEl.style.transform = `rotate(${orbit}deg)`;
    ballEl.classList.remove('dropped');
    void ballEl.offsetWidth;
    ballEl.classList.add('rolling');
    const ticks = setInterval(() => sfx('tick'), 110);
    const drop = setTimeout(() => {
      ballEl.classList.remove('rolling');
      ballEl.classList.add('dropped');
      clearInterval(ticks);
      sfx('click');
      tap('soft');
    }, SPIN_MS - 1500);
    try {
      await wait(ctx, SPIN_MS);
    } catch {
      clearInterval(ticks);
      clearTimeout(drop);
      return;
    }
    clearInterval(ticks);

    const won = totalPayout(bets, number);
    history = [number, ...history].slice(0, 12);
    save('casino-history', history);
    resultEl.className = `rl-result ${colorOf(number)}`;
    resultEl.textContent = String(number);
    resultEl.hidden = false;
    sfx('stop');
    root.querySelectorAll(`[data-field="n${number}"], [data-field="${colorOf(number)}"]`)
      .forEach((el) => el.classList.add('hit'));
    if (won) {
      addChips(won);
      bump(winEl, won, { count: true });
      popText($('.rl-stage'), `+${won}`, { cls: 'gold' });
      if (won >= total * 10) {
        quake($('.rl-wheel'), 6);
        confetti(160);
        sfx('win');
        notify('success');
      } else {
        sfx('coin');
        notify('warning');
      }
    } else {
      sfx('lose');
      notify('error');
    }
    bets = {};
    spinning = false;
    paintHistory();
    sync();
  }

  board.addEventListener('click', (event) => {
    const field = event.target.closest('[data-field]');
    if (field) place(field.dataset.field);
  });
  root.querySelector('.rl-chips').addEventListener('click', (event) => {
    const button = event.target.closest('[data-chip]');
    if (!button) return;
    chip = Number(button.dataset.chip);
    save('casino-chip', chip);
    sfx('chip');
    tap('soft');
    sync();
  });
  $('[data-clear]').addEventListener('click', () => { bets = {}; tap('soft'); sync(); });
  $('[data-repeat]').addEventListener('click', () => {
    if (spinning || sum(last) > chips()) return;
    bets = { ...last };
    sfx('chip');
    tap('soft');
    sync();
  });
  spinBtn.addEventListener('click', spin);
  refill.addEventListener('click', () => { tap(); sfx('coin'); refillChips(); sync(); });
  document.addEventListener('keydown', (event) => {
    if (event.code === 'Space') { event.preventDefault(); spin(); }
  }, { signal: ctx.signal });
  paintHistory();
  sync();
}
