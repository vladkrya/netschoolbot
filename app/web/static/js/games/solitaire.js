// Пасьянс «Косынка» по одной карте. Касание открытой карты само переносит
// её (со всеми картами сверху) туда, куда можно: сначала в «дом», потом на
// другую стопку. Касание колоды — следующая карта. Есть отмена хода.
'use strict';

import { best, cardHtml, clock, deck, isRed, notify, overlay, saveBest, shake, tap } from './kit.js';

/** Можно ли положить карту на стопку поля (по убыванию, чередуя цвет). */
export function canStack(card, onto) {
  if (!onto) return card.r === 13;
  return !onto.down && isRed(card) !== isRed(onto) && onto.r === card.r + 1;
}

/** Можно ли положить карту в «дом» (по возрастанию одной масти от туза). */
export function canFound(card, pile) {
  const top = pile[pile.length - 1];
  return top ? top.s === card.s && top.r + 1 === card.r : card.r === 1;
}

export function newDeal() {
  const cards = deck(1, 13);
  const tableau = [];
  for (let i = 0; i < 7; i += 1) {
    tableau.push(cards.splice(0, i + 1).map((card, j) => ({ ...card, down: j < i })));
  }
  return { stock: cards.map((card) => ({ ...card, down: true })), waste: [], found: [[], [], [], []], tableau };
}

export function start(root, ctx) {
  let game;
  let history;
  let moves;
  let seconds;
  let started;
  let ticker = 0;

  root.innerHTML = `<div class="game solitaire">
      <div class="game-stats"><span>Время: <b data-time>0:00</b></span><span>Ходы: <b data-moves>0</b></span><span>Рекорд: <b data-best></b></span></div>
      <div class="game-stage"><div class="sol-board" data-board></div></div>
      <div class="sudoku-tools">
        <button class="chip" data-undo>↶ Отменить</button>
        <button class="chip" data-auto hidden>Собрать всё</button>
        <button class="chip" data-new>Новая раздача</button>
      </div></div>`;
  const board = root.querySelector('[data-board]');
  const stage = root.querySelector('.game-stage');
  const showBest = () => {
    const record = best('solitaire');
    root.querySelector('[data-best]').textContent = record ? clock(record) : '—';
  };

  function deal() {
    game = newDeal();
    history = [];
    moves = 0;
    seconds = 0;
    started = false;
    root.querySelector('.game-over')?.remove();
    render();
  }

  const snapshot = () => history.push(JSON.stringify(game));

  function render() {
    const top = (pile) => pile[pile.length - 1];
    const stock = game.stock.length
      ? cardHtml({ down: true }, { attrs: 'data-stock' })
      : '<span class="pc empty recycle" data-stock>↻</span>';
    const waste = game.waste.length ? cardHtml(top(game.waste), { attrs: 'data-waste' }) : cardHtml(null);
    const found = game.found.map((pile, i) => (pile.length
      ? cardHtml(top(pile), { attrs: `data-found="${i}"` })
      : `<span class="pc empty" data-found="${i}">A</span>`)).join('');
    const columns = game.tableau.map((pile, col) => `<div class="sol-col" data-col="${col}">${pile.length
      ? pile.map((card, i) => cardHtml(card, { attrs: `data-col="${col}" data-i="${i}"`, cls: card.down ? 'stack-down' : 'stack-up' })).join('')
      : cardHtml(null, { attrs: `data-col="${col}"` })}</div>`).join('');
    board.innerHTML = `<div class="sol-top">${stock}${waste}<span class="sol-gap"></span>${found}</div>
      <div class="sol-cols">${columns}</div>`;
    root.querySelector('[data-moves]').textContent = String(moves);
    root.querySelector('[data-time]').textContent = clock(seconds);
    root.querySelector('[data-undo]').disabled = !history.length;
    const ready = !game.stock.length && !game.waste.length && game.tableau.every((pile) => pile.every((card) => !card.down));
    root.querySelector('[data-auto]').hidden = !ready || won();
  }

  const won = () => game.found.every((pile) => pile.length === 13);

  function afterMove() {
    started = true;
    moves += 1;
    // Открываем верхние закрытые карты.
    game.tableau.forEach((pile) => {
      const last = pile[pile.length - 1];
      if (last?.down) last.down = false;
    });
    render();
    if (won()) {
      notify('success');
      const record = saveBest('solitaire', seconds, true);
      showBest();
      overlay(stage, `<div class="game-over-title">${record ? 'Новый рекорд!' : 'Пасьянс сошёлся!'}</div>
          <div class="muted">${clock(seconds)} · ходов: ${moves}</div>`, [{ label: 'Новая раздача', run: deal }]);
    }
  }

  /** Куда перенести карты: в «дом» (только одну), иначе на стопку поля. */
  function moveFrom(cards, fromCol) {
    if (cards.length === 1) {
      const target = game.found.findIndex((pile) => canFound(cards[0], pile));
      if (target >= 0) return { found: target };
    }
    // Сначала непустые стопки; на пустую — только короля, и не с другой пустой.
    const order = game.tableau.map((pile, col) => col).filter((col) => col !== fromCol)
      .sort((a, b) => Number(!game.tableau[a].length) - Number(!game.tableau[b].length));
    for (const col of order) {
      const pile = game.tableau[col];
      if (!pile.length && fromCol !== undefined && game.tableau[fromCol].indexOf(cards[0]) === 0) continue;
      if (canStack(cards[0], pile[pile.length - 1])) return { col };
    }
    return null;
  }

  function apply(target, cards) {
    if (target.found !== undefined) game.found[target.found].push(...cards);
    else game.tableau[target.col].push(...cards);
  }

  board.addEventListener('click', (event) => {
    const el = event.target.closest('[data-stock], [data-waste], [data-col], [data-found]');
    if (!el || won()) return;
    if (el.hasAttribute('data-stock')) {
      if (!game.stock.length && !game.waste.length) return;
      snapshot();
      if (game.stock.length) {
        game.waste.push({ ...game.stock.pop(), down: false });
      } else {
        game.stock = game.waste.reverse().map((card) => ({ ...card, down: true }));
        game.waste = [];
      }
      tap('soft');
      afterMove();
      return;
    }
    if (el.hasAttribute('data-waste')) {
      const card = game.waste[game.waste.length - 1];
      const target = moveFrom([card]);
      if (!target) return shake(el);
      snapshot();
      game.waste.pop();
      apply(target, [card]);
      tap('light');
      afterMove();
      return;
    }
    if (el.dataset.found !== undefined && el.dataset.i === undefined) {
      // Карту из «дома» можно вернуть на поле.
      const pile = game.found[Number(el.dataset.found)];
      const card = pile[pile.length - 1];
      if (!card) return;
      const col = game.tableau.findIndex((p) => canStack(card, p[p.length - 1]));
      if (col < 0) return shake(el);
      snapshot();
      pile.pop();
      game.tableau[col].push(card);
      tap('light');
      afterMove();
      return;
    }
    if (el.dataset.i === undefined) return;
    const col = Number(el.dataset.col);
    const index = Number(el.dataset.i);
    const pile = game.tableau[col];
    if (pile[index].down) return shake(el);
    const cards = pile.slice(index);
    const target = moveFrom(cards, col);
    if (!target) return shake(el);
    snapshot();
    pile.splice(index);
    apply(target, cards);
    tap('light');
    afterMove();
  });

  root.querySelector('[data-undo]').addEventListener('click', () => {
    if (!history.length) return;
    tap();
    game = JSON.parse(history.pop());
    moves += 1;
    render();
  });
  root.querySelector('[data-new]').addEventListener('click', () => { tap(); deal(); });
  root.querySelector('[data-auto]').addEventListener('click', async () => {
    tap();
    snapshot();
    // Карты по одной летят в «дом», пока все не соберутся.
    while (!won() && !ctx.signal.aborted) {
      let moved = false;
      for (const pile of game.tableau) {
        const card = pile[pile.length - 1];
        const target = card && game.found.findIndex((f) => canFound(card, f));
        if (card && target >= 0) {
          game.found[target].push(pile.pop());
          moved = true;
          break;
        }
      }
      if (!moved) break;
      moves += 1;
      render();
      await new Promise((resolve) => { setTimeout(resolve, 60); });
    }
    moves -= 1;
    afterMove();
  });

  ticker = setInterval(() => {
    if (!started || document.hidden || won()) return;
    seconds += 1;
    root.querySelector('[data-time]').textContent = clock(seconds);
  }, 1000);
  ctx.signal.addEventListener('abort', () => clearInterval(ticker));
  showBest();
  deal();
}
