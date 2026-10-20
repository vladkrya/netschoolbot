// Пасьянс «Косынка». Три уровня: по одной карте из колоды, по три и по три
// с тремя проходами колоды. Карты можно перетаскивать пальцем, а касание само
// переносит карту (со всеми картами сверху) туда, куда можно: сначала в
// «дом», потом на другую стопку; повторное касание везёт её на следующее
// подходящее место. Касание колоды — следующие карты. Есть отмена хода,
// автосбор в конце и подсказка, что ходов больше нет. Партия сохраняется.
//
// Каждая карта — постоянный элемент со своими координатами: при ходе он
// плавно переезжает (transition на left/top), закрытая карта
// переворачивается.
'use strict';

import {
  askRestart, bindChips, bump, cardHtml, chipsRow, clock, deck, hud, isRed, load, overlay, recordText, save, saveBest, sfx, shake, tap,
} from './kit.js';

/** Уровни: сколько карт открывает колода и сколько раз её можно пройти. */
export const LEVELS = {
  easy: { label: 'Лёгкий', draw: 1, passes: Infinity },
  medium: { label: 'Средний', draw: 3, passes: Infinity },
  hard: { label: 'Сложный', draw: 3, passes: 3 },
};

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
  return { stock: cards.map((card) => ({ ...card, down: true })), waste: [], found: [[], [], [], []], tableau, pass: 1 };
}

/** Карты колоды, до которых можно добраться, листая её (по draw штук). */
export function reachable(game, draw, passesLeft) {
  // Колода листается с конца массива stock; сброс — уже открытое.
  const order = [...game.waste, ...[...game.stock].reverse()];
  const seen = new Set();
  if (game.waste.length) seen.add(game.waste.length - 1);
  // Текущий проход: из оставшейся колоды наверх сброса попадёт каждая draw-я.
  for (let i = game.waste.length - 1 + draw; i < order.length; i += draw) seen.add(i);
  if (game.stock.length) seen.add(order.length - 1);
  if (passesLeft > 0 && order.length) {
    for (let i = draw - 1; i < order.length; i += draw) seen.add(i);
    seen.add(order.length - 1);
    if (draw === 1) order.forEach((_, i) => seen.add(i));
  }
  return [...seen].map((i) => order[i]);
}

/** Есть ли полезный ход: не перекладывание карт туда-сюда между стопками. */
export function hasMoves(game, draw, passesLeft) {
  const tops = game.tableau.map((pile) => pile[pile.length - 1]);
  const fits = (card, fromCol = -1) => game.found.some((pile) => canFound(card, pile))
    || tops.some((top, col) => col !== fromCol && top && canStack(card, top));
  for (let col = 0; col < 7; col += 1) {
    const pile = game.tableau[col];
    for (let i = 0; i < pile.length; i += 1) {
      if (pile[i].down) continue;
      if (i === pile.length - 1 && game.found.some((f) => canFound(pile[i], f))) return true;
      // Полезно, только если откроется закрытая карта или освободится место.
      const frees = i === 0 ? pile[i].r !== 13 : pile[i - 1].down;
      if (!frees) continue;
      if (tops.some((top, c) => c !== col && (top ? canStack(pile[i], top) : pile[i].r === 13))) return true;
    }
  }
  return reachable(game, draw, passesLeft).some((card) => fits(card)
    || (card.r === 13 && game.tableau.some((pile) => !pile.length)));
}

const keyOf = (card) => `${card.r}${card.s}`;

export function start(root, ctx) {
  let level = LEVELS[load('solitaire-level')] ? load('solitaire-level') : 'easy';
  let game;
  let history;
  let moves;
  let seconds;
  let started;
  let ticker = 0;
  let busy = false;
  const rules = () => LEVELS[level];
  const passesLeft = () => rules().passes - (game.pass || 1);

  root.innerHTML = `<div class="game solitaire">
      ${chipsRow('level', Object.entries(LEVELS).map(([key, { label }]) => [key, label]), level)}
      ${hud([['time', '⏱', '0:00'], ['moves', 'Ходы', 0], ['best', '🏆', '—']])}
      <div class="game-stage sol-stage"><div class="sol-board" data-board></div></div>
      <div class="sol-tools">
        <button class="tool" data-undo><span>↶</span>Отменить</button>
        <button class="tool" data-auto hidden><span>✨</span>Собрать</button>
        <button class="tool" data-new><span>🂠</span>Раздать</button>
      </div></div>`;
  const board = root.querySelector('[data-board]');
  const stage = root.querySelector('.game-stage');
  const els = new Map();

  // Размеры: семь колонок на ширину поля.
  const width = Math.min(stage.clientWidth || 360, 520);
  const gap = Math.max(4, Math.round(width * 0.015));
  const cw = (width - gap * 6) / 7;
  const ch = cw * 1.4;
  const top2 = ch + gap * 3;
  const maxHeight = Math.max(ch * 4, innerHeight - 240 - top2);
  board.style.width = `${width}px`;
  board.style.setProperty('--cw', `${cw}px`);
  const colX = (i) => i * (cw + gap);

  // Пустые места: колода, сброс, четыре «дома», семь колонок.
  const slots = [
    `<div class="sol-slot stock" style="left:${colX(0)}px;top:0" data-slot="stock"><span>↻</span></div>`,
    ...[0, 1, 2, 3].map((i) => `<div class="sol-slot found" style="left:${colX(3 + i)}px;top:0" data-slot="found${i}"><span>A</span></div>`),
    ...[0, 1, 2, 3, 4, 5, 6].map((i) => `<div class="sol-slot" style="left:${colX(i)}px;top:${top2}px" data-slot="col${i}"></div>`),
  ];
  board.innerHTML = slots.join('');

  const showBest = () => {
    root.querySelector('[data-best]').textContent = recordText(`solitaire-${level}`, clock);
  };

  /** Партия переживает выход из игры: доиграть можно потом. */
  const store = () => save('solitaire-game', won() ? null : { level, game, moves, seconds, history: history.slice(-30) });

  function cardEl(card) {
    const key = keyOf(card);
    let el = els.get(key);
    if (!el) {
      el = document.createElement('div');
      el.className = 'sc';
      el.dataset.key = key;
      board.append(el);
      els.set(key, el);
    }
    const face = card.down ? 'down' : 'up';
    if (el.dataset.face !== face) {
      const wasDown = el.dataset.face === 'down';
      el.innerHTML = cardHtml(card);
      el.dataset.face = face;
      if (wasDown && face === 'up') {
        el.animate?.([{ transform: 'rotateY(90deg)' }, { transform: 'rotateY(0)' }], { duration: 220, easing: 'ease-out' });
        sfx('flip');
      }
    }
    return el;
  }

  /** Где лежит каждая карта: координаты и слой. */
  function layout() {
    const places = [];
    game.stock.forEach((card, i) => places.push([card, colX(0), -Math.min(i, 6) * 0.3, i]));
    // Из сброса видны три верхние карты веером.
    const fan = cw * 0.22;
    game.waste.forEach((card, i) => {
      const fromTop = game.waste.length - 1 - i;
      places.push([card, colX(1) + Math.max(0, 2 - fromTop) * fan * (fromTop < 3 ? 1 : 0), 0, 100 + i]);
    });
    game.found.forEach((pile, f) => pile.forEach((card, i) => places.push([card, colX(3 + f), 0, 200 + i])));
    game.tableau.forEach((pile, col) => {
      const downs = pile.filter((c) => c.down).length;
      const ups = pile.length - downs;
      let down = ch * 0.12;
      let up = ch * 0.27;
      const need = downs * down + Math.max(0, ups - 1) * up + ch;
      if (need > maxHeight && ups > 1) up = Math.max(ch * 0.14, (maxHeight - ch - downs * down) / (ups - 1));
      let y = top2;
      pile.forEach((card, i) => {
        places.push([card, colX(col), y, 300 + i]);
        y += card.down ? down : up;
      });
    });
    return places;
  }

  function render(instant = false) {
    const seen = new Set();
    layout().forEach(([card, x, y, z]) => {
      const el = cardEl(card);
      seen.add(el.dataset.key);
      if (instant) el.classList.add('instant');
      el.style.left = `${x}px`;
      el.style.top = `${y}px`;
      el.style.zIndex = String(z);
    });
    els.forEach((el, key) => { if (!seen.has(key)) { el.remove(); els.delete(key); } });
    const bottom = Math.max(top2 + ch, ...[...els.values()].map((el) => parseFloat(el.style.top) + ch));
    board.style.height = `${bottom + gap}px`;
    root.querySelector('[data-moves]').textContent = String(moves);
    root.querySelector('[data-time]').textContent = clock(seconds);
    root.querySelector('[data-undo]').disabled = !history.length;
    root.querySelector('[data-auto]').hidden = !readyToFinish() || won();
    const stockSlot = board.querySelector('[data-slot="stock"]');
    const left = passesLeft();
    stockSlot.classList.toggle('empty', !game.stock.length);
    stockSlot.querySelector('span').textContent = !game.waste.length || left <= 0 ? '✕' : Number.isFinite(left) ? `↻${left}` : '↻';
  }

  const won = () => game.found.every((pile) => pile.length === 13);
  const readyToFinish = () => !game.stock.length && !game.waste.length && game.tableau.every((pile) => pile.every((card) => !card.down));

  function deal() {
    game = newDeal();
    history = [];
    moves = 0;
    seconds = 0;
    started = false;
    cycle = null;
    save('solitaire-game', null);
    stage.querySelector('.game-over')?.remove();
    els.forEach((el) => el.remove());
    els.clear();
    // Раздача: все карты вылетают из колоды по очереди.
    render(true);
    const order = [];
    for (let row = 0; row < 7; row += 1) {
      for (let col = row; col < 7; col += 1) order.push(game.tableau[col][row]);
    }
    order.forEach((card, i) => {
      const el = els.get(keyOf(card));
      const dx = colX(0) - parseFloat(el.style.left);
      const dy = -parseFloat(el.style.top);
      el.animate?.([{ translate: `${dx}px ${dy}px` }, { translate: '0 0' }],
        { duration: 300, delay: i * 28, easing: 'cubic-bezier(.2,.8,.25,1)', fill: 'backwards' });
    });
    for (let i = 0; i < 6; i += 1) setTimeout(() => sfx('deal'), i * 120);
    requestAnimationFrame(() => els.forEach((el) => el.classList.remove('instant')));
  }

  const snapshot = () => history.push(JSON.stringify(game));

  function afterMove() {
    started = true;
    moves += 1;
    // Открываем верхние закрытые карты.
    game.tableau.forEach((pile) => {
      const last = pile[pile.length - 1];
      if (last?.down) last.down = false;
    });
    render();
    bump(root.querySelector('[data-moves]'), moves);
    store();
    if (won()) celebrate();
    else if (readyToFinish()) root.querySelector('[data-auto]').hidden = false;
    else if (!hasMoves(game, rules().draw, passesLeft())) stuck();
  }

  function stuck() {
    overlay(stage, `<div class="game-over-title">Ходов больше нет</div>
        <div class="game-over-text">Отмените последние ходы или начните новую раздачу.</div>`,
    [{ label: 'Новая раздача', run: deal }, { label: 'Отменить ход', outline: true, run: undo }], { icon: '🃏', lose: true });
  }

  function celebrate() {
    const record = saveBest(`solitaire-${level}`, seconds, true);
    saveBest('solitaire', seconds, true);
    save('solitaire-game', null);
    showBest();
    // Карты из «домов» подпрыгивают волной.
    [...els.values()].forEach((el, i) => el.animate?.([{ translate: '0 0' }, { translate: '0 -24px' }, { translate: '0 0' }],
      { duration: 500, delay: (i % 13) * 50, easing: 'ease-in-out' }));
    setTimeout(() => {
      if (ctx.signal.aborted) return;
      overlay(stage, `<div class="game-over-title">${record ? 'Новый рекорд!' : 'Пасьянс сошёлся!'}</div>
          <div class="game-over-text">${rules().label} · время <b>${clock(seconds)}</b> · ходов: <b>${moves}</b></div>`,
      [{ label: 'Новая раздача', run: deal }], { icon: record ? '🏆' : '🎉', win: true });
    }, 900);
  }

  /** Где сейчас карта: { pile, index } в стопках игры. */
  function locate(key) {
    for (const [name, pile] of [['waste', game.waste], ['stock', game.stock]]) {
      const index = pile.findIndex((c) => keyOf(c) === key);
      if (index >= 0) return { type: name, pile, index };
    }
    for (let f = 0; f < 4; f += 1) {
      const index = game.found[f].findIndex((c) => keyOf(c) === key);
      if (index >= 0) return { type: 'found', f, pile: game.found[f], index };
    }
    for (let col = 0; col < 7; col += 1) {
      const index = game.tableau[col].findIndex((c) => keyOf(c) === key);
      if (index >= 0) return { type: 'col', col, pile: game.tableau[col], index };
    }
    return null;
  }

  /** Можно ли взять карты с этого места: открытые, а из сброса и «дома» — только верхнюю. */
  function movable(at) {
    if (!at || at.type === 'stock') return null;
    const card = at.pile[at.index];
    if (card.down) return null;
    if (at.type !== 'col' && at.index !== at.pile.length - 1) return null;
    return at.pile.slice(at.index);
  }

  function legal(cards, target, at) {
    if (target.type === 'found') return cards.length === 1 && at.type !== 'found' && canFound(cards[0], game.found[target.f]);
    if (target.type === 'col') {
      if (at.type === 'col' && at.col === target.col) return false;
      const pile = game.tableau[target.col];
      return canStack(cards[0], pile[pile.length - 1]);
    }
    return false;
  }

  /** Куда перенести карты по касанию: в «дом», иначе на стопку поля. Повторное
   *  касание той же карты везёт её дальше по кругу — на следующее место. */
  function autoTarget(cards, at) {
    const again = cycle && cycle.key === keyOf(cards[0]) && cycle.moves === moves;
    if (cards.length === 1 && !again) {
      for (let f = 0; f < 4; f += 1) if (legal(cards, { type: 'found', f }, at)) return { type: 'found', f };
    }
    const from = at.type === 'col' ? at.col + 1 : 0;
    const cols = [0, 1, 2, 3, 4, 5, 6].map((i) => (from + i) % 7)
      .sort((a, b) => Number(!game.tableau[a].length) - Number(!game.tableau[b].length));
    for (const col of cols) {
      // Короля с пустого места на другое пустое не переносим — бессмысленно.
      if (!game.tableau[col].length && at.type === 'col' && at.index === 0) continue;
      if (legal(cards, { type: 'col', col }, at)) return { type: 'col', col };
    }
    return null;
  }

  // Последняя карта, перенесённая касанием: для перебора мест.
  let cycle = null;

  function apply(at, target, byTap = false) {
    snapshot();
    const cards = at.pile.splice(at.index);
    cycle = byTap ? { key: keyOf(cards[0]), moves: moves + 1 } : null;
    if (target.type === 'found') game.found[target.f].push(...cards);
    else game.tableau[target.col].push(...cards);
    sfx(target.type === 'found' ? 'good' : 'card');
    tap('light');
    afterMove();
  }

  function drawStock() {
    if (!game.stock.length && (!game.waste.length || passesLeft() <= 0)) {
      shake(board.querySelector('[data-slot="stock"]'));
      return;
    }
    snapshot();
    if (game.stock.length) {
      for (let i = 0; i < rules().draw && game.stock.length; i += 1) game.waste.push({ ...game.stock.pop(), down: false });
      sfx('card');
    } else {
      game.stock = game.waste.reverse().map((card) => ({ ...card, down: true }));
      game.waste = [];
      game.pass = (game.pass || 1) + 1;
      sfx('deal');
    }
    tap('soft');
    afterMove();
  }

  /** Куда бросили карты: колонка под пальцем, вверху — «дом». */
  function dropTarget(x, y) {
    const rect = board.getBoundingClientRect();
    const col = Math.max(0, Math.min(6, Math.floor((x - rect.left + gap / 2) / (cw + gap))));
    if (y - rect.top < top2 - gap && col >= 3) return { type: 'found', f: col - 3 };
    return { type: 'col', col };
  }

  // Касание и перетаскивание.
  let drag = null;
  board.addEventListener('pointerdown', (event) => {
    if (busy || won()) return;
    const slot = event.target.closest('[data-slot="stock"]');
    const el = event.target.closest('.sc');
    if (slot && !el) {
      drawStock();
      return;
    }
    if (!el) return;
    const at = locate(el.dataset.key);
    if (at?.type === 'stock') {
      drawStock();
      return;
    }
    const cards = movable(at);
    if (!cards) {
      shake(el);
      return;
    }
    drag = {
      at, cards, x: event.clientX, y: event.clientY, moved: false,
      els: cards.map((c) => els.get(keyOf(c))),
    };
    board.setPointerCapture?.(event.pointerId);
  });
  board.addEventListener('pointermove', (event) => {
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 7) return;
    if (!drag.moved) {
      drag.moved = true;
      drag.els.forEach((el) => el.classList.add('dragging'));
    }
    drag.els.forEach((el) => { el.style.translate = `${dx}px ${dy}px`; });
  });
  const finish = (event) => {
    if (!drag) return;
    const current = drag;
    drag = null;
    if (!current.moved) {
      const target = autoTarget(current.cards, current.at);
      if (target) apply(current.at, target, true);
      else shake(current.els[0]);
      return;
    }
    // Карты долетают от пальца до места — на новое, если ход возможен, иначе обратно.
    const from = current.els.map((el) => el.getBoundingClientRect());
    current.els.forEach((el) => {
      el.classList.remove('dragging');
      el.classList.add('instant');
      el.style.translate = '';
    });
    const target = dropTarget(event.clientX, event.clientY);
    if (legal(current.cards, target, current.at)) apply(current.at, target);
    else sfx('error');
    current.els.forEach((el, i) => {
      const now = el.getBoundingClientRect();
      el.animate?.([{ translate: `${from[i].left - now.left}px ${from[i].top - now.top}px` }, { translate: '0 0' }],
        { duration: 220, easing: 'cubic-bezier(.2, .8, .25, 1)' });
    });
    requestAnimationFrame(() => current.els.forEach((el) => el.classList.remove('instant')));
  };
  board.addEventListener('pointerup', finish);
  board.addEventListener('pointercancel', finish);

  function undo() {
    if (!history.length || busy) return;
    tap();
    sfx('tap');
    stage.querySelector('.game-over')?.remove();
    game = JSON.parse(history.pop());
    moves += 1;
    cycle = null;
    render();
    store();
  }
  root.querySelector('[data-undo]').addEventListener('click', undo);
  const inProgress = () => started && !won();
  root.querySelector('[data-new]').addEventListener('click', async () => {
    tap();
    if (inProgress() && !(await askRestart(stage))) return;
    deal();
  });
  bindChips(root, 'level', (value) => {
    level = value;
    save('solitaire-level', level);
    showBest();
    deal();
  }, { guard: () => !inProgress() || askRestart(stage) });
  root.querySelector('[data-auto]').addEventListener('click', async () => {
    if (busy) return;
    busy = true;
    tap();
    snapshot();
    // Карты по одной летят в «дом», пока все не соберутся.
    while (!won() && !ctx.signal.aborted) {
      let moved = false;
      const lowest = game.tableau.map((pile, col) => ({ card: pile[pile.length - 1], col }))
        .filter((item) => item.card).sort((a, b) => a.card.r - b.card.r);
      for (const { card, col } of lowest) {
        const f = game.found.findIndex((pile) => canFound(card, pile));
        if (f >= 0) {
          game.found[f].push(game.tableau[col].pop());
          moved = true;
          break;
        }
      }
      if (!moved) break;
      moves += 1;
      sfx('card');
      render();
      await new Promise((resolve) => { setTimeout(resolve, 70); });
    }
    busy = false;
    moves -= 1;
    afterMove();
  });

  ticker = setInterval(() => {
    if (!started || document.hidden || won()) return;
    seconds += 1;
    root.querySelector('[data-time]').textContent = clock(seconds);
  }, 1000);
  ctx.signal.addEventListener('abort', () => {
    clearInterval(ticker);
    if (started) store();
  });
  showBest();
  const saved = load('solitaire-game');
  if (saved?.game?.tableau && saved.level === level) {
    ({ game, moves, seconds } = saved);
    history = saved.history || [];
    started = true;
    render(true);
    requestAnimationFrame(() => els.forEach((el) => el.classList.remove('instant')));
  } else {
    deal();
  }
}
