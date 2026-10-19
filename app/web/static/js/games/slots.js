// Слоты: игровой автомат с тремя барабанами на игровые фишки. Три
// одинаковых в центральной линии — выигрыш по таблице, две вишни — ставка
// вдвое, одна — ставка обратно. Барабаны крутятся с размытием и
// останавливаются по очереди с отскоком, выигрыш сыплется монетами.
'use strict';

import {
  addChips, bump, chips, load, notify, quake, rand, refillChips, save, sfx, tap, wait,
} from './kit.js';

// Символ, вес (чем больше, тем чаще) и множитель за три в ряд.
export const SYMBOLS = [
  ['🍒', 30, 5], ['🍋', 25, 8], ['🍇', 20, 10], ['🔔', 14, 15], ['⭐', 9, 25], ['💎', 5, 50], ['7️⃣', 3, 100],
];
const TOTAL = SYMBOLS.reduce((sum, [, w]) => sum + w, 0);
const BETS = [10, 25, 50, 100, 250, 500];

function spinOne() {
  let roll = rand(TOTAL);
  for (const [symbol, w] of SYMBOLS) {
    if (roll < w) return symbol;
    roll -= w;
  }
  return SYMBOLS[0][0];
}

/** Во сколько раз умножается ставка (0 — проигрыш). */
export function payout(reels) {
  const [a, b, c] = reels;
  if (a === b && b === c) return SYMBOLS.find(([s]) => s === a)[2];
  const cherries = reels.filter((s) => s === '🍒').length;
  if (cherries === 2) return 2;
  if (cherries === 1) return 1;
  return 0;
}

export function start(root, ctx) {
  let bet = Number(load('slots-bet', 25)) || 25;
  if (!BETS.includes(bet)) bet = 25;
  let spinning = false;
  const visible = [0, 1, 2].map(() => [spinOne(), spinOne(), spinOne()]);

  root.innerHTML = `<div class="game slots">
      <div class="slot-cabinet">
        <div class="slot-marquee"><span class="slot-lights"></span><b>LUCKY 7</b><span class="slot-lights"></span></div>
        <div class="slot-window">
          ${visible.map((col, i) => `<div class="slot-reel"><div class="slot-strip" data-reel="${i}">${col.map((s) => `<span>${s}</span>`).join('')}</div></div>`).join('')}
          <div class="slot-line"></div>
        </div>
        <div class="slot-display">
          <div><span>Фишки</span><b data-chips>${chips()}</b></div>
          <div class="slot-win"><span>Выигрыш</span><b data-win>0</b></div>
        </div>
        <div class="slot-controls">
          <div class="slot-bet"><button data-bet="-1" aria-label="Меньше">−</button><div><span>Ставка</span><b data-bet-value>${bet}</b></div><button data-bet="1" aria-label="Больше">+</button></div>
          <button class="slot-spin" data-spin>КРУТИТЬ</button>
        </div>
        <button class="slot-lever" data-lever aria-label="Дёрнуть рычаг"><i></i></button>
      </div>
      <button class="game-btn" data-refill hidden>🏦 Взять 1000 фишек у банка</button>
      <div class="slot-table">${SYMBOLS.map(([s, , m]) => `<span>${s}${s}${s}<b>×${m}</b></span>`).join('')}<span>🍒🍒<b>×2</b></span><span>🍒<b>×1</b></span></div></div>`;
  const chipsEl = root.querySelector('[data-chips]');
  const winEl = root.querySelector('[data-win]');
  const spinBtn = root.querySelector('[data-spin]');
  const refill = root.querySelector('[data-refill]');
  const cabinet = root.querySelector('.slot-cabinet');
  const strips = [...root.querySelectorAll('[data-reel]')];

  const sync = () => {
    const have = chips();
    bump(chipsEl, have, { count: true });
    root.querySelector('[data-bet-value]').textContent = String(bet);
    refill.hidden = have >= BETS[0];
    spinBtn.disabled = spinning || have < bet;
    cabinet.classList.toggle('busy', spinning);
  };

  function coinRain(amount) {
    const count = Math.min(40, 6 + Math.round(amount / 25));
    for (let i = 0; i < count; i += 1) {
      const coin = document.createElement('span');
      coin.className = 'coin-drop';
      coin.textContent = '🪙';
      coin.style.left = `${10 + Math.random() * 80}%`;
      coin.style.animationDelay = `${Math.random() * 0.6}s`;
      coin.style.animationDuration = `${0.9 + Math.random() * 0.6}s`;
      cabinet.append(coin);
      coin.addEventListener('animationend', () => coin.remove());
      if (i < 10) setTimeout(() => sfx('coin'), i * 90);
    }
  }

  async function spin() {
    if (spinning || chips() < bet) return;
    spinning = true;
    addChips(-bet);
    bump(winEl, 0);
    cabinet.classList.remove('won', 'big');
    root.querySelectorAll('.slot-strip span.hit').forEach((el) => el.classList.remove('hit'));
    sync();
    tap('medium');
    sfx('spin');
    const result = [spinOne(), spinOne(), spinOne()];
    const size = strips[0].firstElementChild.getBoundingClientRect().height || 72;
    const stops = [];
    strips.forEach((strip, i) => {
      const filler = Array.from({ length: 18 + i * 7 }, spinOne);
      const finalCol = [spinOne(), result[i], spinOne()];
      const items = [...visible[i], ...filler, ...finalCol];
      strip.innerHTML = items.map((s) => `<span>${s}</span>`).join('');
      strip.style.transition = 'none';
      strip.style.transform = 'translateY(0)';
      void strip.offsetHeight;
      const duration = 1.2 + i * 0.45;
      strip.classList.add('blur');
      strip.style.transition = `transform ${duration}s cubic-bezier(.25, .1, .25, 1.06)`;
      strip.style.transform = `translateY(${-(items.length - 3) * size}px)`;
      visible[i] = finalCol;
      stops.push(setTimeout(() => {
        strip.classList.remove('blur');
        sfx('stop');
        tap('soft');
      }, duration * 1000 - 120));
    });
    // Пока крутится — тиканье.
    const ticker = setInterval(() => sfx('tick'), 90);
    try {
      await wait(ctx, 1200 + 2 * 450 + 150);
    } catch {
      clearInterval(ticker);
      stops.forEach(clearTimeout);
      return;
    } finally {
      clearInterval(ticker);
    }
    // После остановки на барабане остаются только три видимых символа.
    strips.forEach((strip, i) => {
      strip.style.transition = 'none';
      strip.style.transform = 'translateY(0)';
      strip.innerHTML = visible[i].map((s) => `<span>${s}</span>`).join('');
    });
    const times = payout(result);
    if (times) {
      const won = bet * times;
      addChips(won);
      bump(winEl, won, { count: true });
      // Совпавшие символы в линии подсвечиваются.
      strips.forEach((strip, i) => {
        if (times > 2 || result[i] === '🍒') strip.children[1].classList.add('hit');
      });
      if (times >= 5) {
        cabinet.classList.add('won');
        if (times >= 25) {
          cabinet.classList.add('big');
          quake(cabinet, 6);
          sfx('win');
          notify('success');
        } else {
          sfx('good');
          notify('warning');
        }
        coinRain(won);
      } else {
        sfx('coin');
      }
    }
    spinning = false;
    sync();
  }

  spinBtn.addEventListener('click', spin);
  // Рычаг: касание или рывок вниз — то же, что кнопка.
  const lever = root.querySelector('[data-lever]');
  lever.addEventListener('click', () => {
    if (spinning || chips() < bet) return;
    lever.classList.remove('pull');
    void lever.offsetWidth;
    lever.classList.add('pull');
    spin();
  });
  root.querySelector('.slot-bet').addEventListener('click', (event) => {
    const button = event.target.closest('[data-bet]');
    if (!button || spinning) return;
    const i = Math.max(0, Math.min(BETS.length - 1, BETS.indexOf(bet) + Number(button.dataset.bet)));
    bet = BETS[i];
    save('slots-bet', bet);
    sfx('chip');
    tap('soft');
    sync();
  });
  refill.addEventListener('click', () => { tap(); sfx('coin'); refillChips(); sync(); });
  document.addEventListener('keydown', (event) => { if (event.code === 'Space') { event.preventDefault(); spin(); } }, { signal: ctx.signal });
  sync();
}
