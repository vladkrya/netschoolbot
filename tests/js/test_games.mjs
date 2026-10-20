// Правила игр из вкладки «Игры»: node --test tests/js/test_games.mjs
import assert from 'node:assert/strict';
import test from 'node:test';

// Модули игр тянут haptics.js → core.js, которым нужны window и document.
const noop = () => {};
const events = { addEventListener: noop, removeEventListener: noop, dispatchEvent: noop };
globalThis.window = globalThis;
window.NETSCHOOL = {};
Object.assign(window, events);
globalThis.document = {
  ...events,
  documentElement: { classList: { contains: () => false, add: noop, remove: noop, toggle: noop }, style: { setProperty: noop } },
  querySelector: () => null,
  getElementById: () => null,
  createElement: () => ({ style: {} }),
};
globalThis.localStorage = { getItem: () => null, setItem: noop, removeItem: noop };

const games = new URL('../../app/web/static/js/games/', import.meta.url);
const durak = await import(new URL('durak.js', games));
const sudoku = await import(new URL('sudoku.js', games));
const paperio = await import(new URL('paperio.js', games));
const blackjack = await import(new URL('blackjack.js', games));
const tetris = await import(new URL('tetris.js', games));
const snake = await import(new URL('snake.js', games));
const solitaire = await import(new URL('solitaire.js', games));
const slots = await import(new URL('slots.js', games));
const poker = await import(new URL('poker.js', games));

const c = (r, s) => ({ r, s });

test('дурак: чем можно побить', () => {
  assert.equal(durak.beats(c(7, '♠'), c(9, '♠'), '♥'), true);
  assert.equal(durak.beats(c(9, '♠'), c(7, '♠'), '♥'), false);
  assert.equal(durak.beats(c(14, '♠'), c(6, '♥'), '♥'), true, 'козырь бьёт любую некозырную');
  assert.equal(durak.beats(c(10, '♥'), c(9, '♥'), '♥'), false);
  assert.equal(durak.beats(c(7, '♠'), c(9, '♣'), '♥'), false, 'другая масть не бьёт');
});

test('дурак: подкидывать только достоинства со стола и не больше карт отбивающегося', () => {
  const g = durak.createGame(['А', 'Б']);
  g.attacker = 0;
  g.defender = 1;
  g.players[0].hand = [c(7, '♠'), c(7, '♣'), c(8, '♦'), c(7, '♦')];
  g.players[1].hand = [c(9, '♠')];
  g.limit = 1;
  assert.equal(durak.play(g, 0, c(7, '♠')), true);
  assert.equal(durak.canPlay(g, 0, c(7, '♣')), false, 'у отбивающегося одна карта');
  assert.equal(durak.pending(g).kind, 'defend');
  assert.equal(durak.defend(g, c(9, '♠'), 0), true);
  assert.equal(durak.pending(g).kind, 'end', 'отбивающемуся больше нечем');
});

test('дурак: боты любого уровня доигрывают партию без потерянных карт', () => {
  const levels = Object.keys(durak.LEVELS);
  for (let round = 0; round < 120; round += 1) {
    const level = levels[round % levels.length];
    const g = durak.createGame(['А', 'Б', 'В', 'Г'].slice(0, 2 + (round % 3)));
    const onTable = () => g.table.reduce((sum, pair) => sum + 1 + Boolean(pair.d), 0);
    const total = () => g.deck.length + g.players.reduce((sum, p) => sum + p.hand.length, 0) + onTable() + g.discard.length;
    let steps = 0;
    while (!g.over) {
      steps += 1;
      assert.ok(steps < 5000, 'партия зациклилась');
      const turn = durak.pending(g);
      if (turn.kind === 'end') {
        durak.endBout(g);
      } else {
        // Сложный перебирает на малом числе раскладов — тест должен быть быстрым.
        const move = level === 'hard' ? durak.smartMove(g, turn.who, 3) : durak.botMove(g, turn.who, level);
        assert.ok(durak.applyMove(g, turn.who, move), `ход ${move.kind} не прошёл`);
      }
      assert.equal(total(), 36);
      assert.ok(g.table.length <= 6);
    }
    assert.ok(g.loser === null || !g.players[g.loser].out);
  }
});

test('дурак: сложный бот обыгрывает среднего', () => {
  let hardLost = 0;
  let mediumLost = 0;
  for (let round = 0; round < 60; round += 1) {
    const g = durak.createGame(['Сложный', 'Средний']);
    while (!g.over) {
      const turn = durak.pending(g);
      if (turn.kind === 'end') durak.endBout(g);
      else durak.applyMove(g, turn.who, turn.who === 0 ? durak.smartMove(g, 0, 16) : durak.botMove(g, 1));
    }
    if (g.loser === 0) hardLost += 1;
    if (g.loser === 1) mediumLost += 1;
  }
  assert.ok(mediumLost > hardLost, `сложный дурак ${hardLost} раз, средний — ${mediumLost}`);
});

test('судоку: у головоломки единственное решение', () => {
  const { puzzle, solution } = sudoku.generate(32);
  assert.equal(sudoku.countSolutions([...puzzle]), 1);
  puzzle.forEach((n, i) => { if (n) assert.equal(n, solution[i]); });
  for (let r = 0; r < 9; r += 1) {
    assert.equal(new Set(solution.slice(r * 9, r * 9 + 9)).size, 9);
  }
  assert.ok(puzzle.filter(Boolean).length <= 36);
});

test('paper.io: обведённое становится своим', () => {
  const n = 8;
  const owner = new Uint8Array(n * n);
  const trail = new Uint8Array(n * n);
  owner[2 * n + 2] = 1;
  // Хвост вокруг клетки (4, 4): выход из (2,2) и возвращение.
  for (const [x, y] of [[3, 2], [4, 2], [5, 2], [5, 3], [5, 4], [5, 5], [4, 5], [3, 5], [2, 5], [2, 4], [2, 3]]) {
    trail[y * n + x] = 1;
  }
  owner[4 * n + 4] = 2;
  paperio.capture(owner, trail, 1, n);
  assert.equal(owner[4 * n + 4], 1, 'чужая клетка внутри петли');
  assert.equal(owner[3 * n + 3], 1);
  assert.equal(owner[0], 0, 'снаружи — не наше');
  assert.equal(trail.some(Boolean), false);
});

test('блэкджек: очки и выплаты', () => {
  assert.deepEqual(blackjack.handValue([c(14, '♠'), c(13, '♥')]), { total: 21, soft: true });
  assert.equal(blackjack.handValue([c(14, '♠'), c(14, '♥'), c(9, '♦')]).total, 21);
  assert.equal(blackjack.handValue([c(10, '♠'), c(12, '♥'), c(5, '♦')]).total, 25);
  assert.equal(blackjack.settle([c(14, '♠'), c(13, '♥')], [c(10, '♠'), c(9, '♥')]), 2.5);
  assert.equal(blackjack.settle([c(10, '♠'), c(9, '♥')], [c(10, '♠'), c(9, '♥')]), 1);
  assert.equal(blackjack.settle([c(10, '♠'), c(5, '♥'), c(9, '♣')], [c(10, '♠'), c(9, '♥')]), 0);
  assert.equal(blackjack.settle([c(10, '♠'), c(8, '♥')], [c(10, '♠'), c(6, '♥'), c(9, '♣')]), 2);
  assert.equal(blackjack.botHits([c(10, '♠'), c(6, '♥')], c(10, '♣')), true);
  assert.equal(blackjack.botHits([c(10, '♠'), c(7, '♥')], c(10, '♣')), false);
});

test('тетрис: четыре поворота возвращают фигуру, полные линии убираются', () => {
  for (const [kind, shape] of Object.entries(tetris.SHAPES)) {
    let cells = shape.cells;
    for (let i = 0; i < 4; i += 1) cells = tetris.rotate(cells, kind);
    assert.deepEqual(cells, shape.cells);
  }
  const board = [[null, 'x'], ['x', 'x'], ['x', null]];
  const { board: after, cleared } = tetris.clearLines(board);
  assert.equal(cleared, 1);
  assert.deepEqual(after, [[null, null], [null, 'x'], ['x', null]]);
});

test('змейка: бот идёт к яблоку и не разворачивается в себя', () => {
  assert.equal(snake.botStep([5, 5], 'right', new Set(), [[9, 5]], 20), 'right');
  assert.equal(snake.botStep([5, 5], 'right', new Set(), [[5, 1]], 20), 'up');
  assert.notEqual(snake.botStep([5, 5], 'right', new Set(), [[1, 5]], 20), 'left');
});

test('пасьянс: раздача и правила стопок', () => {
  const deal = solitaire.newDeal();
  assert.equal(deal.stock.length, 24);
  assert.deepEqual(deal.tableau.map((pile) => pile.length), [1, 2, 3, 4, 5, 6, 7]);
  assert.ok(deal.tableau.every((pile) => !pile[pile.length - 1].down));
  assert.equal(solitaire.canStack(c(12, '♥'), c(13, '♠')), true);
  assert.equal(solitaire.canStack(c(12, '♥'), c(13, '♦')), false);
  assert.equal(solitaire.canStack(c(13, '♥'), undefined), true);
  assert.equal(solitaire.canFound(c(1, '♣'), []), true);
  assert.equal(solitaire.canFound(c(3, '♣'), [c(1, '♣'), c(2, '♣')]), true);
  assert.equal(solitaire.canFound(c(3, '♥'), [c(1, '♣'), c(2, '♣')]), false);
});

test('пасьянс: ходов нет — только когда и колода не поможет', () => {
  const deal = solitaire.newDeal();
  // Поле из одних королей и пустая колода: ходить некуда.
  const kings = ['♠', '♥', '♦', '♣'].map((s) => ({ r: 13, s, down: false }));
  const stuck = {
    stock: [], waste: [], found: [[], [], [], []], pass: 1,
    tableau: [[{ r: 5, s: '♠', down: true }, kings[0]], [{ r: 6, s: '♠', down: true }, kings[1]], [kings[2]], [kings[3]], [], [], []].map((p, i) => (i < 4 ? p : [{ r: 2, s: '♥', down: true }, { r: 12, s: ['♠', '♣', '♥'][i - 4], down: false }])),
  };
  assert.equal(solitaire.hasMoves(stuck, 1, Infinity), true, 'дама ложится на короля и открывает карту');
  stuck.tableau = [[kings[0]], [kings[1]], [kings[2]], [kings[3]], [{ r: 7, s: '♠', down: false }], [{ r: 7, s: '♣', down: false }], [{ r: 2, s: '♥', down: false }]];
  assert.equal(solitaire.hasMoves(stuck, 1, Infinity), false, 'перекладывать королей между пустыми местами — не ход');
  stuck.waste = [{ r: 1, s: '♦', down: false }];
  assert.equal(solitaire.hasMoves(stuck, 3, 0), true, 'туз со сброса — в «дом»');
  stuck.waste = [];
  stuck.stock = [{ r: 3, s: '♦', down: true }, { r: 1, s: '♠', down: true }, { r: 9, s: '♦', down: true }, { r: 4, s: '♣', down: true }];
  // По три: из колоды [4♣ 9♦ A♠ 3♦] (сверху 4♣) откроется 3-я карта (A♠) и последняя (3♦).
  assert.deepEqual(solitaire.reachable(stuck, 3, 0).map((card) => card.r), [1, 3]);
  assert.equal(solitaire.hasMoves(stuck, 3, 0), true);
  assert.equal(deal.pass, 1);
});

test('покер: старшинство комбинаций', () => {
  const hand = (...cards) => poker.evaluate(cards.map(([r, s]) => c(r, s)));
  const royal = hand([14, '♠'], [13, '♠'], [12, '♠'], [11, '♠'], [10, '♠'], [2, '♥'], [3, '♦']);
  const wheel = hand([14, '♠'], [2, '♥'], [3, '♦'], [4, '♣'], [5, '♠'], [9, '♥'], [12, '♦']);
  const six = hand([2, '♥'], [3, '♦'], [4, '♣'], [5, '♠'], [6, '♠'], [9, '♥'], [12, '♦']);
  const full = hand([9, '♠'], [9, '♥'], [9, '♦'], [4, '♠'], [4, '♦'], [4, '♣'], [2, '♥']);
  const flush = hand([2, '♥'], [7, '♥'], [9, '♥'], [11, '♥'], [13, '♥'], [13, '♠'], [13, '♦']);
  assert.equal(royal.cat, 8);
  assert.equal(wheel.cat, 4);
  assert.ok(six.score > wheel.score, 'стрит до шестёрки старше колеса');
  assert.equal(full.cat, 6, 'два сета — это фулл-хаус');
  assert.ok(full.score > flush.score);
  assert.equal(poker.handName([c(14, '♠'), c(13, '♠'), c(12, '♠'), c(11, '♠'), c(10, '♠')]), 'Роял-флеш');
  // Кикер решает: пара тузов с королём сильнее пары тузов с дамой.
  assert.ok(hand([14, '♠'], [14, '♥'], [13, '♦'], [7, '♣'], [3, '♠']).score > hand([14, '♦'], [14, '♣'], [12, '♦'], [7, '♥'], [3, '♥']).score);
});

test('покер: побочный банк при ва-банке', () => {
  const t = poker.createTable(['А', 'Б', 'В'], [100, 300, 300]);
  t.dealer = 2;
  t.players.forEach((p) => Object.assign(p, { hand: [], bet: 0, folded: false, allIn: false, out: false }));
  // А (100, ва-банк) — лучшая рука, Б — вторая, В — худшая; Б и В поставили по 300.
  t.players[0].total = 100;
  t.players[1].total = 300;
  t.players[2].total = 300;
  t.players.forEach((p) => { p.stack = 0; });
  t.board = [c(2, '♠'), c(7, '♥'), c(9, '♦'), c(12, '♣'), c(4, '♠')];
  t.players[0].hand = [c(14, '♠'), c(14, '♥')];
  t.players[1].hand = [c(13, '♠'), c(13, '♥')];
  t.players[2].hand = [c(3, '♦'), c(5, '♣')];
  poker.settle(t);
  assert.deepEqual(t.players.map((p) => p.stack), [300, 400, 0], 'А берёт основной банк 3×100, Б — побочный 2×200');
});

test('покер: боты всех уровней доигрывают раздачи, фишки не теряются', () => {
  const levels = Object.keys(poker.LEVELS);
  for (let round = 0; round < 9; round += 1) {
    const n = 2 + (round % 3);
    const t = poker.createTable(Array.from({ length: n }, (_, i) => `P${i}`), Array(n).fill(500));
    for (let hand = 0; hand < 15; hand += 1) {
      t.players.forEach((p) => { if (p.stack <= 0) p.stack = 500; });
      const before = t.players.reduce((sum, p) => sum + p.stack, 0);
      poker.newHand(t);
      let steps = 0;
      while (t.street !== 'showdown') {
        steps += 1;
        assert.ok(steps < 200, 'раздача зациклилась');
        const level = levels[(t.toAct + round) % levels.length];
        assert.ok(poker.act(t, poker.botAct(t, level, 20)), 'бот сделал недопустимый ход');
      }
      poker.settle(t);
      assert.equal(t.players.reduce((sum, p) => sum + p.stack, 0), before);
    }
  }
});

test('слоты: автомат в среднем забирает фишки', () => {
  const rtp = slots.rtp();
  assert.ok(rtp < 1 && rtp > 0.9, `отдача ${rtp}`);
});

test('слоты: выплаты', () => {
  assert.equal(slots.payout(['7️⃣', '7️⃣', '7️⃣']), 100);
  assert.equal(slots.payout(['🍒', '🍒', '🔔']), 2);
  assert.equal(slots.payout(['🍋', '🍒', '🔔']), 1);
  assert.equal(slots.payout(['🍋', '⭐', '🔔']), 0);
});

test('paper.io: поворот выбирает короткую сторону', () => {
  assert.ok(Math.abs(paperio.angleDiff(0.1, -0.1) - 0.2) < 1e-9);
  assert.ok(Math.abs(paperio.angleDiff(-3, 3) - (2 * Math.PI - 6)) < 1e-9, 'через ±π, а не через ноль');
});
