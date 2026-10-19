// Блэкджек против дилера-бота; за столом ещё два бота со своими фишками.
// Дилер берёт до 17, блэкджек платит 3:2, ничья возвращает ставку.
'use strict';

import {
  addChips, bindChips, cardHtml, chips, chipsRow, deck, load, notify, refillChips, save, tap, wait,
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

export function start(root, ctx) {
  let bet = Number(load('blackjack-bet', 50)) || 50;
  let shoe = deck(2, 14, 4);
  let dealer = [];
  let seats = [];
  let phase = 'bet';
  let message = '';

  const BOTS = [{ name: 'Бот Вася', chips: 1000 }, { name: 'Бот Маша', chips: 1000 }];

  root.innerHTML = `<div class="game blackjack">
      <div class="game-stats"><span>Фишки: <b data-chips>${chips()}</b></span><span data-result></span></div>
      <div class="game-stage bj-table" data-table></div>
      <div class="bj-controls" data-controls></div></div>`;
  const table = root.querySelector('[data-table]');
  const controls = root.querySelector('[data-controls]');

  const draw = () => {
    if (shoe.length < 30) shoe = deck(2, 14, 4);
    return shoe.pop();
  };

  function hand(cards, hideSecond = false) {
    return `<div class="bj-cards">${cards.map((card, i) => cardHtml(hideSecond && i === 1 ? { down: true } : card)).join('')}</div>`;
  }

  function render() {
    // Вторая карта дилера закрыта, пока играют за столом.
    const hidden = ['dealing', 'play', 'bots'].includes(phase);
    const dealerScore = hidden ? (dealer[0] ? handValue([dealer[0]]).total : '') : handValue(dealer).total;
    const seatHtml = seats.map((seat) => {
      const score = seat.cards.length ? handValue(seat.cards).total : '';
      return `<div class="bj-seat${seat.me ? ' me' : ''}${seat.active ? ' active' : ''}">
          <div class="bj-name">${seat.name} <span class="muted">· ставка ${seat.bet}</span></div>
          ${hand(seat.cards)}<div class="bj-score">${score}${seat.result ? ` · ${seat.result}` : ''}</div></div>`;
    }).join('');
    table.innerHTML = `<div class="bj-dealer"><div class="bj-name">Дилер-бот</div>${hand(dealer, hidden)}
        <div class="bj-score">${dealer.length ? dealerScore : ''}</div></div>
      <div class="bj-seats">${seatHtml}</div>
      <div class="bj-message">${message}</div>`;
    root.querySelector('[data-chips]').textContent = String(chips());
    if (phase === 'bet' || phase === 'done') {
      const have = chips();
      controls.innerHTML = have < 10
        ? '<button class="btn" data-act="refill">Взять 1000 фишек у банка</button>'
        : `<div class="drawer-sub">Ставка</div>${chipsRow('bet', [['10', '10'], ['50', '50'], ['100', '100'], ['250', '250']], String(bet))}
          <button class="btn" data-act="deal" ${have < bet ? 'disabled' : ''}>${phase === 'done' ? 'Ещё раздача' : 'Раздать'}</button>`;
      bindChips(controls, 'bet', (value) => {
        bet = Number(value);
        save('blackjack-bet', bet);
        render();
      });
    } else if (phase === 'play') {
      const me = seats.find((seat) => seat.me);
      const canDouble = me.cards.length === 2 && chips() >= me.bet;
      controls.innerHTML = `<div class="bj-buttons">
          <button class="btn" data-act="hit">Ещё</button>
          <button class="btn outline" data-act="stand">Хватит</button>
          <button class="btn outline" data-act="double" ${canDouble ? '' : 'disabled'}>Удвоить</button></div>`;
    } else {
      controls.innerHTML = '';
    }
  }

  async function deal() {
    if (chips() < bet) return;
    addChips(-bet);
    seats = [
      { name: BOTS[0].name, bot: BOTS[0], bet: Math.min(BOTS[0].chips, [25, 50, 100][Math.floor(Math.random() * 3)]), cards: [] },
      { name: 'Вы', me: true, bet, cards: [] },
      { name: BOTS[1].name, bot: BOTS[1], bet: Math.min(BOTS[1].chips, [25, 50, 100][Math.floor(Math.random() * 3)]), cards: [] },
    ];
    seats.forEach((seat) => {
      if (!seat.bot) return;
      // Боту, который всё проиграл, банк тоже выдаёт новые фишки.
      if (seat.bot.chips < 25) seat.bot.chips = 1000;
      seat.bot.chips -= seat.bet;
    });
    dealer = [];
    message = '';
    root.querySelector('[data-result]').textContent = '';
    phase = 'dealing';
    render();
    try {
      for (let round = 0; round < 2; round += 1) {
        for (const seat of seats) {
          seat.cards.push(draw());
          tap('soft');
          render();
          await wait(ctx, 180);
        }
        dealer.push(draw());
        render();
        await wait(ctx, 180);
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
    } catch {
      /* игру закрыли */
    }
  }

  async function playBots(from, to) {
    for (const seat of seats.slice(from, to)) {
      if (!seat.bot) continue;
      seat.active = true;
      render();
      await wait(ctx, 500);
      while (handValue(seat.cards).total < 21 && botHits(seat.cards, dealer[0])) {
        seat.cards.push(draw());
        render();
        await wait(ctx, 550);
      }
      seat.active = false;
    }
  }

  async function myTurnOver() {
    const me = seats[1];
    me.active = false;
    phase = 'bots';
    render();
    await playBots(2, 3);
    await finishRound();
  }

  async function finishRound() {
    phase = 'dealer';
    render();
    const anyAlive = seats.some((seat) => handValue(seat.cards).total <= 21 && !isBlackjack(seat.cards));
    await wait(ctx, 500);
    while (anyAlive && handValue(dealer).total < 17) {
      dealer.push(draw());
      tap('soft');
      render();
      await wait(ctx, 600);
    }
    for (const seat of seats) {
      const times = settle(seat.cards, dealer);
      const won = Math.round(seat.bet * times);
      seat.result = times === 0 ? (handValue(seat.cards).total > 21 ? 'перебор' : 'проигрыш')
        : times === 1 ? 'ничья' : times === 2.5 ? `блэкджек +${won - seat.bet}` : `+${won - seat.bet}`;
      if (seat.me) {
        addChips(won);
        const line = root.querySelector('[data-result]');
        line.innerHTML = times > 1 ? `<b class="win">+${won - seat.bet}</b>` : times === 1 ? 'ничья' : `<b class="lose">−${seat.bet}</b>`;
        notify(times > 1 ? 'success' : times === 1 ? 'warning' : 'error');
      } else {
        seat.bot.chips += won;
      }
    }
    const dealerTotal = handValue(dealer).total;
    message = dealerTotal > 21 ? 'У дилера перебор' : `У дилера ${dealerTotal}`;
    phase = 'done';
    render();
  }

  controls.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-act]');
    if (!button || button.disabled) return;
    const me = seats.find((seat) => seat.me);
    tap();
    try {
      switch (button.dataset.act) {
        case 'refill':
          refillChips();
          render();
          break;
        case 'deal':
          await deal();
          break;
        case 'hit':
          me.cards.push(draw());
          render();
          if (handValue(me.cards).total >= 21) await myTurnOver();
          break;
        case 'stand':
          await myTurnOver();
          break;
        case 'double':
          addChips(-me.bet);
          me.bet *= 2;
          me.cards.push(draw());
          await myTurnOver();
          break;
        default:
      }
    } catch {
      /* игру закрыли посреди раздачи */
    }
  });
  render();
}
