// Блэкджек против дилера-бота; за столом ещё два бота со своими фишками.
// Дилер берёт до 17, блэкджек платит 3:2, ничья возвращает ставку. Ставка
// собирается фишками, карты вылетают из шуза, закрытая карта дилера
// переворачивается в конце раздачи.
'use strict';

import {
  addChips, bump, cardHtml, chips, deck, flip, load, overlay, popText, refillChips, save, sfx, tap, wait,
} from './kit.js';

/** Очки руки: туз — 11 или 1, картинки — 10. */
export function handValue(cards) {
  let total = 0;
  let aces = 0;
  for (const card of cards) {
    if (card.r === 14) {
      aces += 1;
      total += 11;
    } else {
      total += Math.min(card.r, 10);
    }
  }
  while (total > 21 && aces) {
    total -= 10;
    aces -= 1;
  }
  return { total, soft: aces > 0 };
}

export const isBlackjack = (cards) => cards.length === 2 && handValue(cards).total === 21;

/** Решение бота-игрока по упрощённой «базовой стратегии». */
export function botHits(cards, dealerUp) {
  const { total, soft } = handValue(cards);
  const up = dealerUp.r === 14 ? 11 : Math.min(dealerUp.r, 10);
  if (soft) return total <= 17 || (total === 18 && up >= 9);
  if (total <= 11) return true;
  if (total === 12) return up < 4 || up > 6;
  if (total <= 16) return up >= 7;
  return false;
}

/** Итог руки против дилера: во сколько раз вернуть ставку (0, 1, 2, 2.5). */
export function settle(cards, dealer) {
  const mine = handValue(cards).total;
  const theirs = handValue(dealer).total;
  if (mine > 21) return 0;
  if (isBlackjack(cards) && !isBlackjack(dealer)) return 2.5;
  if (isBlackjack(dealer) && !isBlackjack(cards)) return 0;
  if (theirs > 21 || mine > theirs) return 2;
  return mine === theirs ? 1 : 0;
}


const CHIP_VALUES = [10, 25, 50, 100, 250];
const BOTS = [{ name: 'Вася', face: '🤖' }, { name: 'Маша', face: '👾' }];

export function start(root, ctx) {
  let lastBet = Number(load('blackjack-bet', 50)) || 50;
  let bet = 0;
  let shoe = deck(2, 14, 4);
  let dealer = [];
  let seats = [];
  let phase = 'bet';
  let message = '';
  let reveal = false;
  const botChips = [1000, 1000];

  root.innerHTML = `<div class="game blackjack">
      <div class="bj-bank">🪙 <b data-chips>${chips()}</b></div>
      <div class="game-stage felt bj-table" data-table></div>
      <div class="bj-controls" data-controls></div></div>`;
  const table = root.querySelector('[data-table]');
  const controls = root.querySelector('[data-controls]');
  const stage = table;

  const draw = () => {
    if (shoe.length < 30) shoe = deck(2, 14, 4);
    return shoe.pop();
  };

  const chipHtml = (value, attrs = '') => `<span class="chip-c c${value}" ${attrs}>${value}</span>`;
  /** Ставка стопкой фишек — от крупных к мелким. */
  function stack(amount) {
    const list = [];
    let left = amount;
    for (const value of [...CHIP_VALUES].reverse()) {
      while (left >= value && list.length < 8) {
        list.push(value);
        left -= value;
      }
    }
    return `<div class="chip-stack">${list.map((v, i) => chipHtml(v, `style="--i:${i}"`)).join('')}</div>`;
  }

  const key = (card) => `${card.r}${card.s}${card.copy}`;

  function hand(cards, hide = false) {
    return `<div class="bj-cards">${cards.map((card, i) => cardHtml(hide && i === 1 ? { down: true } : card,
      { key: hide && i === 1 ? 'hole' : key(card), cls: i === 1 && !hide && reveal ? 'reveal' : '' })).join('')}</div>`;
  }

  function score(cards, hide) {
    if (!cards.length) return '';
    const v = handValue(hide ? [cards[0]] : cards);
    const label = isBlackjack(cards) && !hide ? 'BJ' : v.soft && v.total < 21 && !hide ? `${v.total - 10}/${v.total}` : v.total;
    return `<span class="bj-score${v.total > 21 ? ' bust' : ''}">${label}</span>`;
  }

  function tableHtml() {
    const hidden = ['dealing', 'play', 'bots'].includes(phase);
    const seatHtml = seats.map((seat) => `<div class="bj-seat${seat.me ? ' me' : ''}${seat.active ? ' active' : ''}">
        ${seat.result ? `<span class="bj-result ${seat.tone}">${seat.result}</span>` : ''}
        ${hand(seat.cards)}${score(seat.cards)}
        <div class="bj-name">${seat.face} ${seat.name}</div>${stack(seat.bet)}</div>`).join('');
    return `<div class="bj-shoe" data-shoe><span class="pc back"></span><span class="pc back"></span></div>
      <div class="bj-dealer"><div class="bj-name">🎩 Дилер</div>${hand(dealer, hidden)}${score(dealer, hidden)}</div>
      <div class="bj-arc">BLACKJACK ПЛАТИТ 3 К 2<small>Дилер берёт до 17</small></div>
      <div class="bj-message">${message}</div>
      <div class="bj-seats">${seats.length ? seatHtml : `<div class="bj-seat me"><div class="bj-betspot">${bet ? stack(bet) : '<span>Ставка</span>'}</div><div class="bj-name">😎 Вы · ${bet}</div></div>`}</div>`;
  }

  function render() {
    const shoeRect = table.querySelector('[data-shoe]')?.getBoundingClientRect() || null;
    flip(table, () => { table.innerHTML = tableHtml(); }, { enterFrom: (el) => (el.classList.contains('reveal') ? null : shoeRect), duration: 360 });
    bump(root.querySelector('[data-chips]'), chips(), { count: true });
    if (phase === 'bet' || phase === 'done') {
      const have = chips();
      controls.innerHTML = have < CHIP_VALUES[0] && !bet
        ? '<button class="game-btn" data-act="refill">🏦 Взять 1000 фишек у банка</button>'
        : `<div class="chip-row">${CHIP_VALUES.map((v) => `<button class="chip-btn" data-chip="${v}" ${have - bet < v ? 'disabled' : ''}>${chipHtml(v)}</button>`).join('')}</div>
          <div class="bj-buttons">
            <button class="game-btn ghost" data-act="clear" ${bet ? '' : 'disabled'}>Сбросить</button>
            <button class="game-btn" data-act="deal" ${bet ? '' : 'disabled'}>Раздать</button>
          </div>`;
    } else if (phase === 'play') {
      const me = seats.find((seat) => seat.me);
      const canDouble = me.cards.length === 2 && chips() >= me.bet;
      controls.innerHTML = `<div class="bj-buttons three">
          <button class="game-btn" data-act="hit">Ещё</button>
          <button class="game-btn ghost" data-act="stand">Хватит</button>
          <button class="game-btn gold" data-act="double" ${canDouble ? '' : 'disabled'}>×2</button></div>`;
    } else {
      controls.innerHTML = '<div class="bj-wait">…</div>';
    }
  }

  async function deal() {
    if (!bet || chips() < bet) return;
    addChips(-bet);
    lastBet = bet;
    save('blackjack-bet', bet);
    const botBet = () => [25, 50, 100][Math.floor(Math.random() * 3)];
    seats = [
      { name: BOTS[0].name, face: BOTS[0].face, bot: 0, bet: botBet(), cards: [] },
      { name: 'Вы', face: '😎', me: true, bet, cards: [] },
      { name: BOTS[1].name, face: BOTS[1].face, bot: 1, bet: botBet(), cards: [] },
    ];
    seats.forEach((seat) => {
      if (seat.bot === undefined) return;
      // Боту, который всё проиграл, банк тоже выдаёт новые фишки.
      if (botChips[seat.bot] < seat.bet) botChips[seat.bot] = 1000;
      botChips[seat.bot] -= seat.bet;
    });
    dealer = [];
    message = '';
    reveal = false;
    phase = 'dealing';
    render();
    for (let round = 0; round < 2; round += 1) {
      for (const seat of seats) {
        seat.cards.push(draw());
        sfx('deal');
        render();
        await wait(ctx, 230);
      }
      dealer.push(draw());
      sfx('deal');
      render();
      await wait(ctx, 230);
    }
    await playBots(0, 1);
    const me = seats[1];
    if (isBlackjack(me.cards)) {
      message = 'Блэкджек!';
      await finishRound();
      return;
    }
    me.active = true;
    phase = 'play';
    render();
  }

  async function playBots(from, to) {
    for (const seat of seats.slice(from, to)) {
      if (seat.bot === undefined) continue;
      seat.active = true;
      render();
      await wait(ctx, 600);
      while (handValue(seat.cards).total < 21 && botHits(seat.cards, dealer[0])) {
        seat.cards.push(draw());
        sfx('deal');
        render();
        await wait(ctx, 600);
      }
      seat.active = false;
    }
  }

  async function myTurnOver() {
    const me = seats[1];
    me.active = false;
    if (handValue(me.cards).total > 21) {
      sfx('lose');
      popText(stage, 'Перебор!', { y: '62%' });
    }
    phase = 'bots';
    render();
    await playBots(2, 3);
    await finishRound();
  }

  async function finishRound() {
    phase = 'dealer';
    reveal = true;
    sfx('flip');
    render();
    reveal = false;
    const anyAlive = seats.some((seat) => handValue(seat.cards).total <= 21 && !isBlackjack(seat.cards));
    await wait(ctx, 650);
    while (anyAlive && handValue(dealer).total < 17) {
      dealer.push(draw());
      sfx('deal');
      render();
      await wait(ctx, 650);
    }
    let mine = 0;
    for (const seat of seats) {
      const times = settle(seat.cards, dealer);
      const won = Math.round(seat.bet * times);
      const busted = handValue(seat.cards).total > 21;
      seat.result = times === 0 ? (busted ? 'Перебор' : 'Проигрыш') : times === 1 ? 'Ничья' : times === 2.5 ? `BJ +${won - seat.bet}` : `+${won - seat.bet}`;
      seat.tone = times === 0 ? 'lose' : times === 1 ? 'push' : 'win';
      if (seat.me) {
        addChips(won);
        mine = times;
      } else {
        botChips[seat.bot] += won;
      }
    }
    const dealerTotal = handValue(dealer).total;
    message = dealerTotal > 21 ? 'У дилера перебор!' : `У дилера ${dealerTotal}`;
    phase = 'done';
    bet = Math.min(lastBet, chips());
    render();
    if (mine > 1) {
      sfx(mine === 2.5 ? 'win' : 'coin');
      tap('medium');
      popText(stage, mine === 2.5 ? 'Блэкджек! 🎉' : 'Победа!', { y: '58%', cls: 'gold' });
    } else if (mine === 0) {
      sfx('lose');
    }
    if (!chips() && !bet) {
      overlay(stage, '<div class="game-over-title">Фишки закончились</div><div class="game-over-text">Банк выдаст новые 1000 — играем дальше.</div>',
        [{ label: 'Взять фишки', run: () => { refillChips(); bet = 0; render(); } }], { icon: '🏦' });
    }
  }

  controls.addEventListener('click', async (event) => {
    const chip = event.target.closest('[data-chip]');
    if (chip && !chip.disabled && (phase === 'bet' || phase === 'done')) {
      if (phase === 'done') {
        seats = [];
        dealer = [];
        message = '';
        phase = 'bet';
        bet = 0;
      }
      bet += Number(chip.dataset.chip);
      sfx('chip');
      tap('soft');
      render();
      return;
    }
    const button = event.target.closest('[data-act]');
    if (!button || button.disabled) return;
    const me = seats.find((seat) => seat.me);
    tap();
    try {
      switch (button.dataset.act) {
        case 'refill':
          refillChips();
          sfx('coin');
          render();
          break;
        case 'clear':
          bet = 0;
          if (phase === 'done') {
            seats = [];
            dealer = [];
            message = '';
            phase = 'bet';
          }
          sfx('chip');
          render();
          break;
        case 'deal':
          await deal();
          break;
        case 'hit':
          me.cards.push(draw());
          sfx('deal');
          render();
          if (handValue(me.cards).total >= 21) await myTurnOver();
          break;
        case 'stand':
          sfx('tap');
          await myTurnOver();
          break;
        case 'double':
          addChips(-me.bet);
          me.bet *= 2;
          sfx('chip');
          me.cards.push(draw());
          render();
          await wait(ctx, 400);
          await myTurnOver();
          break;
        default:
      }
    } catch {
      /* игру закрыли посреди раздачи */
    }
  });
  bet = Math.min(lastBet, chips());
  render();
}
