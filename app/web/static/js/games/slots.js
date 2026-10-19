// Слоты: три барабана на игровые фишки. Три одинаковых — выигрыш по
// таблице, две вишни — ставка вдвое, одна — ставка обратно.
'use strict';

import { addChips, bindChips, chips, chipsRow, load, notify, rand, refillChips, save, tap, wait } from './kit.js';

// Символ, вес (чем больше, тем чаще) и множитель за три в ряд.
export const SYMBOLS = [
  ['🍒', 30, 5], ['🍋', 25, 8], ['🔔', 18, 12], ['⭐', 12, 20], ['💎', 8, 40], ['7️⃣', 4, 100],
];
const TOTAL = SYMBOLS.reduce((sum, [, w]) => sum + w, 0);

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

const STRIP = 24;

export function start(root, ctx) {
  let bet = Number(load('slots-bet', 10)) || 10;
  let spinning = false;
  root.innerHTML = `<div class="game slots">
      <div class="game-stats"><span>Фишки: <b data-chips>${chips()}</b></span><span data-win></span></div>
      <div class="slot-machine">${[0, 1, 2].map((i) => `<div class="slot-reel"><div class="slot-strip" data-reel="${i}"><span>🍒</span></div></div>`).join('')}
        <div class="slot-line"></div></div>
      <div class="drawer-sub">Ставка</div>
      ${chipsRow('bet', [['10', '10'], ['50', '50'], ['100', '100'], ['250', '250']], String(bet))}
      <button class="btn slot-spin" data-spin>Крутить</button>
      <button class="btn outline" data-refill hidden>Взять 1000 фишек у банка</button>
      <div class="slot-table">${SYMBOLS.map(([s, , m]) => `<span>${s}${s}${s} ×${m}</span>`).join('')}<span>🍒🍒 ×2</span><span>🍒 ×1</span></div></div>`;
  const chipsEl = root.querySelector('[data-chips]');
  const winEl = root.querySelector('[data-win]');
  const spinBtn = root.querySelector('[data-spin]');
  const refill = root.querySelector('[data-refill]');

  const sync = () => {
    const have = chips();
    chipsEl.textContent = String(have);
    refill.hidden = have >= 10;
    spinBtn.disabled = spinning || have < bet;
  };

  async function spin() {
    if (spinning || chips() < bet) return;
    spinning = true;
    addChips(-bet);
    winEl.textContent = '';
    sync();
    tap('medium');
    const result = [spinOne(), spinOne(), spinOne()];
    const strips = [...root.querySelectorAll('[data-reel]')];
    const size = strips[0].firstElementChild.getBoundingClientRect().height || 64;
    strips.forEach((strip, i) => {
      const filler = Array.from({ length: STRIP + i * 6 }, spinOne);
      strip.innerHTML = [strip.lastElementChild.textContent, ...filler, result[i]].map((s) => `<span>${s}</span>`).join('');
      strip.style.transition = 'none';
      strip.style.transform = 'translateY(0)';
      void strip.offsetHeight;
      strip.style.transition = `transform ${1.1 + i * 0.35}s cubic-bezier(.15, .8, .25, 1)`;
      strip.style.transform = `translateY(${-(strip.children.length - 1) * size}px)`;
    });
    try {
      await wait(ctx, 1900);
    } catch {
      return;
    }
    // После остановки на барабане остаётся только выпавший символ.
    strips.forEach((strip, i) => {
      strip.style.transition = 'none';
      strip.style.transform = 'translateY(0)';
      strip.innerHTML = `<span>${result[i]}</span>`;
    });
    const times = payout(result);
    if (times) {
      addChips(bet * times);
      winEl.innerHTML = `<b class="win">+${bet * times}</b>`;
      notify(times >= 5 ? 'success' : 'warning');
    } else {
      winEl.textContent = 'мимо';
    }
    spinning = false;
    sync();
  }

  spinBtn.addEventListener('click', spin);
  refill.addEventListener('click', () => { tap(); refillChips(); sync(); });
  bindChips(root, 'bet', (value) => {
    bet = Number(value);
    save('slots-bet', bet);
    sync();
  });
  document.addEventListener('keydown', (event) => { if (event.code === 'Space') { event.preventDefault(); spin(); } }, { signal: ctx.signal });
  sync();
}
