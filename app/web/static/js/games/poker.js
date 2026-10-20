// Покер: техасский холдем без лимита против 1–3 ботов, на общих игровых
// фишках (как слоты и блэкджек). Блайнды 10/20, у ботов по 1000 фишек —
// проигравшемуся банк выдаёт новые. Ва-банк делит банк на побочные.
//
// Боты оценивают руку перебором: много раз раздают наугад карты соперников
// и недостающие общие карты и считают, как часто их рука сильнее. Лёгкий
// играет почти наугад, средний сверяет шансы с ценой колла, сложный ещё и
// блефует, подбирает размер ставки и считает перебор точнее.
//
// Раздача не сохраняется: закрыли игру посреди неё — это фолд, поставленное
// остаётся в банке.
'use strict';

import {
  addChips, askRestart, bindChips, cardHtml, chips, chipsRow, deck, flip, load, overlay, popText, rand, refillChips, save, sfx, tap,
  wait,
} from './kit.js';

// ───────────── Оценка руки ─────────────

export const HAND_NAMES = ['Старшая карта', 'Пара', 'Две пары', 'Сет', 'Стрит', 'Флеш', 'Фулл-хаус', 'Каре', 'Стрит-флеш'];

/** Сила пяти карт числом: больше — сильнее. Старший разряд — комбинация. */
function score5(cards) {
  const ranks = cards.map((c) => c.r).sort((a, b) => b - a);
  const flush = cards.every((c) => c.s === cards[0].s);
  const unique = [...new Set(ranks)];
  let high = 0;
  if (unique.length === 5) {
    if (ranks[0] - ranks[4] === 4) high = ranks[0];
    // Колесо: A-2-3-4-5, туз здесь младший.
    else if (ranks[0] === 14 && ranks[1] === 5) high = 5;
  }
  const counts = unique.map((r) => [ranks.filter((x) => x === r).length, r]).sort((a, b) => b[0] - a[0] || b[1] - a[1]);
  const shape = counts.map(([n]) => n).join('');
  let cat;
  if (high && flush) cat = 8;
  else if (shape === '41') cat = 7;
  else if (shape === '32') cat = 6;
  else if (flush) cat = 5;
  else if (high) cat = 4;
  else if (shape === '311') cat = 3;
  else if (shape === '221') cat = 2;
  else if (shape === '2111') cat = 1;
  else cat = 0;
  const kickers = high ? [high] : counts.map(([, r]) => r);
  return kickers.reduce((sum, r, i) => sum + r * 15 ** (4 - i), cat * 15 ** 5);
}

/** Лучшая пятёрка из 5–7 карт: { score, cat }. */
export function evaluate(cards) {
  let best = -1;
  const n = cards.length;
  // Перебор всех пятёрок: из семи карт их 21.
  const pick = (start, chosen) => {
    if (chosen.length === 5) {
      best = Math.max(best, score5(chosen));
      return;
    }
    for (let i = start; i <= n - (5 - chosen.length); i += 1) pick(i + 1, [...chosen, cards[i]]);
  };
  pick(0, []);
  return { score: best, cat: Math.floor(best / 15 ** 5) };
}

/** Название руки для подписи: «Пара», «Флеш», «Роял-флеш». До флопа карт
 *  меньше пяти — тогда смотрим только на одинаковые достоинства. */
export function handName(cards) {
  if (cards.length < 5) {
    const counts = cards.map((c) => cards.filter((x) => x.r === c.r).length);
    const top = Math.max(...counts);
    if (top >= 4) return HAND_NAMES[7];
    if (top === 3) return HAND_NAMES[3];
    return HAND_NAMES[counts.filter((n) => n === 2).length >= 4 ? 2 : top === 2 ? 1 : 0];
  }
  const { score, cat } = evaluate(cards);
  if (cat === 8 && score % 15 ** 5 === 14 * 15 ** 4) return 'Роял-флеш';
  return HAND_NAMES[cat];
}

// ───────────── Правила торговли ─────────────

export const SMALL = 10;
export const BIG = 20;

export function createTable(names, stacks) {
  return {
    players: names.map((name, i) => ({ name, stack: stacks[i], hand: [], bet: 0, total: 0, folded: false, allIn: false, acted: false })),
    dealer: -1,
    board: [],
    deck: [],
    street: 'idle',
    toAct: -1,
    currentBet: 0,
    minRaise: BIG,
    log: [],
  };
}

const live = (t) => t.players.filter((p) => !p.folded && !p.out);
export const pot = (t) => t.players.reduce((sum, p) => sum + p.total, 0);

function nextSeat(t, i, ok = (p) => !p.out) {
  const n = t.players.length;
  for (let step = 1; step <= n; step += 1) {
    const j = (i + step) % n;
    if (ok(t.players[j])) return j;
  }
  return -1;
}

const canAct = (p) => !p.out && !p.folded && !p.allIn;

function put(t, i, amount) {
  const p = t.players[i];
  const paid = Math.min(amount, p.stack);
  p.stack -= paid;
  p.bet += paid;
  p.total += paid;
  if (!p.stack) p.allIn = true;
  return paid;
}

/** Новая раздача: кнопка дилера двигается, блайнды, по две карты. Игрок без
 *  фишек (out) пропускает раздачу. */
export function newHand(t, cards = deck(2, 14)) {
  t.players.forEach((p) => {
    Object.assign(p, { hand: [], bet: 0, total: 0, folded: false, allIn: false, acted: false, out: p.stack <= 0 });
  });
  t.deck = cards;
  t.board = [];
  t.street = 'preflop';
  t.log = [];
  t.dealer = nextSeat(t, t.dealer);
  // Вдвоём малый блайнд ставит дилер.
  const heads = t.players.filter((p) => !p.out).length === 2;
  const sb = heads ? t.dealer : nextSeat(t, t.dealer);
  const bb = nextSeat(t, sb);
  put(t, sb, SMALL);
  put(t, bb, BIG);
  t.currentBet = BIG;
  t.minRaise = BIG;
  for (let round = 0; round < 2; round += 1) {
    let i = t.dealer;
    do {
      i = nextSeat(t, i);
      t.players[i].hand.push(t.deck.pop());
    } while (i !== t.dealer);
  }
  t.toAct = nextSeat(t, bb, canAct);
  if (bettingDone(t)) advanceStreet(t);
  return t;
}

/** Что может сделать тот, чей ход: сколько доплатить и до скольких поднять. */
export function options(t) {
  const p = t.players[t.toAct];
  const toCall = Math.min(t.currentBet - p.bet, p.stack);
  const maxTo = p.bet + p.stack;
  const minTo = Math.min(t.currentBet + t.minRaise, maxTo);
  return { toCall, canCheck: toCall === 0, canRaise: maxTo > t.currentBet, minTo, maxTo };
}

function bettingDone(t) {
  if (live(t).length <= 1) return true;
  const active = t.players.filter(canAct);
  if (!active.length) return true;
  // Один с фишками, остальные ва-банк: ему остаётся только уравнять.
  if (active.length === 1 && active[0].bet >= t.currentBet) return true;
  return active.every((p) => p.acted && p.bet === t.currentBet);
}

/** Ход: { type: 'fold' | 'check' | 'call' | 'raise', to? } — to: до скольких поднять. */
export function act(t, move) {
  const i = t.toAct;
  const p = t.players[i];
  const { toCall, minTo, maxTo } = options(t);
  if (move.type === 'fold') {
    p.folded = true;
  } else if (move.type === 'check' || (move.type === 'call' && !toCall)) {
    if (toCall) return false;
  } else if (move.type === 'call') {
    put(t, i, toCall);
  } else if (move.type === 'raise') {
    const to = Math.max(minTo, Math.min(maxTo, Math.round(move.to)));
    if (to <= t.currentBet) return false;
    const raise = to - t.currentBet;
    // Неполный ва-банк не открывает торговлю заново.
    if (raise >= t.minRaise) {
      t.minRaise = raise;
      t.players.forEach((other) => { other.acted = false; });
    }
    put(t, i, to - p.bet);
    t.currentBet = Math.max(t.currentBet, p.bet);
  } else {
    return false;
  }
  p.acted = true;
  t.log.push({ who: i, type: move.type, bet: p.bet });
  if (bettingDone(t)) advanceStreet(t);
  else t.toAct = nextSeat(t, i, canAct);
  return true;
}

function advanceStreet(t) {
  t.players.forEach((p) => {
    p.bet = 0;
    p.acted = false;
  });
  t.currentBet = 0;
  t.minRaise = BIG;
  if (live(t).length <= 1) {
    t.street = 'showdown';
    t.toAct = -1;
    return;
  }
  const order = ['preflop', 'flop', 'turn', 'river', 'showdown'];
  t.street = order[order.indexOf(t.street) + 1];
  if (t.street === 'flop') t.board.push(t.deck.pop(), t.deck.pop(), t.deck.pop());
  else if (t.street === 'turn' || t.street === 'river') t.board.push(t.deck.pop());
  if (t.street === 'showdown') {
    t.toAct = -1;
    return;
  }
  t.toAct = nextSeat(t, t.dealer, canAct);
  // Торговаться не с кем (все, кроме одного, ва-банк) — сразу следующая улица.
  if (t.toAct < 0 || bettingDone(t)) advanceStreet(t);
}

/** Раздать банк: побочные банки по вкладам; ничья делится поровну, остаток —
 *  первому от дилера. Возвращает [{ who, won, name }] и зачисляет выигрыш. */
export function settle(t) {
  const contenders = live(t);
  const results = new Map();
  if (contenders.length === 1) {
    const i = t.players.indexOf(contenders[0]);
    results.set(i, pot(t));
  } else {
    const scores = t.players.map((p) => (p.folded || p.out ? -1 : evaluate([...p.hand, ...t.board]).score));
    const levels = [...new Set(t.players.map((p) => p.total).filter(Boolean))].sort((a, b) => a - b);
    let prev = 0;
    for (const level of levels) {
      const slice = t.players.reduce((sum, p) => sum + Math.max(0, Math.min(p.total, level) - prev), 0);
      const eligible = t.players.map((p, i) => i).filter((i) => scores[i] >= 0 && t.players[i].total >= level);
      prev = level;
      if (!eligible.length || !slice) continue;
      const top = Math.max(...eligible.map((i) => scores[i]));
      // Порядок от дилера — кому достаётся лишняя фишка при делёжке.
      const winners = [];
      let i = t.dealer;
      do {
        i = (i + 1) % t.players.length;
        if (eligible.includes(i) && scores[i] === top) winners.push(i);
      } while (i !== t.dealer);
      const share = Math.floor(slice / winners.length);
      winners.forEach((w, k) => results.set(w, (results.get(w) || 0) + share + (k === 0 ? slice - share * winners.length : 0)));
    }
  }
  results.forEach((won, i) => { t.players[i].stack += won; });
  t.street = 'done';
  return [...results].map(([who, won]) => ({ who, won, name: contenders.length > 1 ? handName([...t.players[who].hand, ...t.board]) : '' }));
}

// ───────────── Боты ─────────────

/** Шанс выиграть против opponents случайных рук: доля побед в samples
 *  случайных доигровках (ничья — половина). */
export function equity(hand, board, opponents, samples = 200) {
  const used = [...hand, ...board];
  const rest = deck(2, 14).filter((c) => !used.some((u) => u.r === c.r && u.s === c.s));
  let wins = 0;
  for (let s = 0; s < samples; s += 1) {
    // Частичная перетасовка: нужны только первые карты.
    const need = opponents * 2 + 5 - board.length;
    for (let i = 0; i < need; i += 1) {
      const j = i + rand(rest.length - i);
      [rest[i], rest[j]] = [rest[j], rest[i]];
    }
    const full = [...board, ...rest.slice(opponents * 2, need)];
    const mine = evaluate([...hand, ...full]).score;
    let best = 0;
    let ties = 0;
    for (let o = 0; o < opponents; o += 1) {
      const theirs = evaluate([rest[o * 2], rest[o * 2 + 1], ...full]).score;
      if (theirs > best) {
        best = theirs;
        ties = 0;
      }
      if (theirs === best) ties += 1;
    }
    if (mine > best) wins += 1;
    else if (mine === best) wins += 1 / (ties + 1);
  }
  return wins / samples;
}

/** Уровни ботов. samples — сколько доигровок на оценку руки; press — насколько
 *  крупная чужая ставка поднимает планку для колла (большая ставка обычно
 *  значит сильную руку, а шанс считается против случайных карт); size — рейз
 *  в долях банка; bluff — как часто ставить без руки, когда все прочекали.
 *  В симуляции за 100 раздач один на один сложный выигрывает у среднего
 *  ~1000 фишек, у лёгкого ~4400; средний у лёгкого — ~2500. */
export const LEVELS = {
  easy: { label: 'Лёгкий', samples: 60 },
  medium: { label: 'Средний', samples: 150, press: 0, size: 0.66, bluff: 0.04 },
  hard: { label: 'Сложный', samples: 300, press: 0.35, size: 1, bluff: 0.06 },
};

/** Ход бота: { type, to? }. samples — для быстрых тестов. */
export function botAct(t, level = 'medium', samples = LEVELS[level].samples) {
  const p = t.players[t.toAct];
  const cfg = LEVELS[level];
  const { toCall, canCheck, canRaise, minTo, maxTo } = options(t);
  const opponents = live(t).length - 1;
  const eq = equity(p.hand, t.board, opponents, samples);
  const total = pot(t);
  const odds = toCall / (total + toCall);
  const raiseTo = (part) => Math.max(minTo, Math.min(maxTo, t.currentBet + Math.round((total * part) / 10) * 10));
  // Чем больше соперников, тем выше планка «сильной» руки.
  const strong = 1 / (opponents + 1) + (1 - 1 / (opponents + 1)) * 0.45;

  if (level === 'easy') {
    // Лёгкий: смотрит на руку грубо и часто ошибается в обе стороны.
    const r = Math.random();
    if (canRaise && eq > strong && r < 0.4) return { type: 'raise', to: raiseTo(0.5) };
    if (canCheck) return r < 0.1 && canRaise ? { type: 'raise', to: minTo } : { type: 'check' };
    if (eq + 0.2 > odds || r < 0.15) return { type: 'call' };
    return { type: 'fold' };
  }

  if (canRaise && eq > strong) return { type: 'raise', to: raiseTo(cfg.size) };
  if (canCheck) {
    const late = t.street === 'turn' || t.street === 'river';
    if (canRaise && Math.random() < cfg.bluff * (late ? 1.5 : 1)) return { type: 'raise', to: raiseTo(0.5) };
    return { type: 'check' };
  }
  const facing = Math.min(1, toCall / Math.max(total - toCall, 1));
  return eq > odds + cfg.press * facing ? { type: 'call' } : { type: 'fold' };
}

// ───────────── Экран ─────────────

const BOTS = [
  { name: 'Вася', face: '🤖', color: '#ff7a59' },
  { name: 'Маша', face: '👾', color: '#b06bff', fem: true },
  { name: 'Петя', face: '🦊', color: '#2fb7a3' },
];
const BOT_STACK = 1000;
const ACTIONS = { fold: 'Пас', check: 'Чек', call: 'Колл', raise: 'Рейз' };

export function start(root, ctx) {
  let bots = Number(load('poker-bots', 1)) || 1;
  let level = LEVELS[load('poker-level')] ? load('poker-level') : 'medium';
  let t;
  let busy = false;
  let gen = 0;
  let message = '';
  let shown = false;
  let raising = false;
  const bubbles = {};

  root.innerHTML = `<div class="game poker">
      ${chipsRow('bots', [['1', '1 бот'], ['2', '2 бота'], ['3', '3 бота']], String(bots))}
      ${chipsRow('level', Object.entries(LEVELS).map(([key, { label }]) => [key, label]), level)}
      <div class="game-stage felt pk-table" data-table></div>
      <div class="pk-controls" data-controls></div></div>`;
  const table = root.querySelector('[data-table]');
  const controls = root.querySelector('[data-controls]');
  const key = (card) => `${card.r}${card.s}`;
  const me = () => t.players[0];
  const inHand = () => t && !['idle', 'done'].includes(t.street);

  function seat(p, i) {
    const bot = BOTS[i - 1];
    const cards = p.out ? '' : p.hand.map((card) => cardHtml(shown && !p.folded ? card : { down: true }, { key: `${i}-${key(card)}`, cls: 'pk-mini' })).join('');
    const bubble = bubbles[i]?.until > performance.now() ? `<span class="bubble">${bubbles[i].text}</span>` : '';
    const state = p.out ? 'нет фишек' : p.folded ? 'пас' : p.allIn ? 'ва-банк' : '';
    return `<div class="pk-seat${t.toAct === i ? ' turn' : ''}${p.folded || p.out ? ' folded' : ''}">
        ${bubble}
        <div class="dk-avatar" style="--c:${bot.color}">${bot.face}${t.dealer === i ? '<span class="pk-dealer">D</span>' : ''}</div>
        <div class="pk-hole">${cards}</div>
        <div class="dk-name">${bot.name}</div>
        <div class="pk-stack">🪙 ${p.stack}${state ? ` · ${state}` : ''}</div>
        ${p.bet ? `<div class="pk-bet">${p.bet}</div>` : ''}</div>`;
  }

  function html() {
    const board = Array.from({ length: 5 }, (_, i) => (t.board[i] ? cardHtml(t.board[i], { key: key(t.board[i]) }) : '<span class="pc empty pk-slot"></span>')).join('');
    const mine = me();
    const name = mine.hand.length && !mine.folded ? handName([...mine.hand, ...t.board]) : '';
    return `<div class="pk-opps">${t.players.slice(1).map((p, k) => seat(p, k + 1)).join('')}</div>
      <div class="pk-mid">
        <div class="pk-board">${board}</div>
        ${inHand() ? `<div class="pk-pot">Банк: <b>${pot(t)}</b></div>` : ''}
        <div class="pk-message">${message}</div>
      </div>
      <div class="pk-me${t.toAct === 0 ? ' turn' : ''}${mine.folded ? ' folded' : ''}">
        ${mine.bet ? `<div class="pk-bet">${mine.bet}</div>` : ''}
        <div class="pk-hand">${mine.hand.map((card) => cardHtml(card, { key: `0-${key(card)}` })).join('')}${t.dealer === 0 ? '<span class="pk-dealer">D</span>' : ''}</div>
        <div class="pk-name">😎 Вы · 🪙 ${inHand() ? mine.stack : chips()}${name ? ` · <b>${name}</b>` : ''}</div>
      </div>`;
  }

  function controlsHtml() {
    if (!inHand()) {
      const have = chips();
      return have < BIG
        ? '<button class="game-btn" data-act="refill">🏦 Взять 1000 фишек у банка</button>'
        : `<button class="game-btn" data-act="deal">${t.street === 'done' ? 'Следующая раздача' : 'Раздать'}</button>`;
    }
    if (t.toAct !== 0 || busy) return `<div class="bj-wait">${me().folded ? 'Вы спасовали — ждём конца раздачи' : '…'}</div>`;
    const o = options(t);
    if (raising) {
      const total = pot(t);
      const sizes = [
        ['Мин.', o.minTo],
        ['½ банка', t.currentBet + Math.round(total / 20) * 10],
        ['Банк', t.currentBet + total],
        ['Ва-банк', o.maxTo],
      ].map(([label, to]) => [label, Math.max(o.minTo, Math.min(o.maxTo, to))])
        .filter(([, to], i, list) => list.findIndex(([, other]) => other === to) === i);
      return `<div class="pk-sizes">${sizes.map(([label, to]) => `<button class="game-btn ${to === o.maxTo ? 'warn' : ''}" data-raise="${to}">${label}<small>до ${to}</small></button>`).join('')}</div>
        <button class="game-btn ghost small" data-act="back">Назад</button>`;
    }
    return `<div class="bj-buttons three">
        <button class="game-btn ghost" data-act="fold">Пас</button>
        <button class="game-btn" data-act="call">${o.canCheck ? 'Чек' : `Колл ${o.toCall}`}</button>
        <button class="game-btn gold" data-act="raise" ${o.canRaise ? '' : 'disabled'}>${o.canCheck ? 'Ставка' : 'Рейз'}</button></div>`;
  }

  function render() {
    const deckRect = table.getBoundingClientRect();
    flip(table, () => { table.innerHTML = html(); }, {
      duration: 340,
      enterFrom: () => new DOMRect(deckRect.left + deckRect.width / 2 - 20, deckRect.top + deckRect.height / 2 - 30, 40, 60),
    });
    controls.innerHTML = controlsHtml();
  }

  /** Фишки игрока — общие игровые: стек за столом и есть они. */
  const syncChips = () => addChips(me().stack - chips());

  function say(i, text) {
    bubbles[i] = { text, until: performance.now() + 1400 };
  }

  async function play() {
    if (busy) return;
    busy = true;
    const mine = gen;
    const pause = async (ms) => {
      await wait(ctx, ms);
      if (mine !== gen) throw new Error('новая игра');
    };
    try {
      let street = t.street;
      for (;;) {
        if (t.street !== street) {
          // Новая улица: общие карты ложатся с паузой.
          street = t.street;
          if (['flop', 'turn', 'river'].includes(street)) sfx('deal');
          render();
          await pause(450);
        }
        if (t.street === 'showdown') break;
        if (t.toAct === 0) break;
        render();
        await pause(500 + Math.random() * 500);
        const who = t.toAct;
        const move = botAct(t, level);
        act(t, move);
        say(who, move.type === 'raise' ? `${ACTIONS.raise} ${t.players[who].bet}` : move.type === 'call' && t.players[who].allIn ? 'Ва-банк' : ACTIONS[move.type]);
        sfx(move.type === 'fold' ? 'tap' : 'chip');
        tap('soft');
      }
    } catch {
      return;
    } finally {
      if (mine === gen) busy = false;
    }
    if (t.street === 'showdown') finish();
    else render();
  }

  function finish() {
    shown = t.players.filter((p) => !p.folded && !p.out).length > 1;
    const results = settle(t);
    syncChips();
    t.players.forEach((p, i) => { if (i && p.stack <= 0) p.stack = BOT_STACK; });
    const mine = results.find((r) => r.who === 0);
    const names = results.map(({ who, name }) => (who ? BOTS[who - 1].name : 'вы') + (name ? ` (${name.toLowerCase()})` : ''));
    const winners = results.map(({ who }) => who);
    message = winners.includes(0)
      ? `Банк ваш: +${mine.won}${mine.name ? ` · ${mine.name}` : ''}`
      : `${winners.length > 1 ? 'Банк делят' : BOTS[winners[0] - 1].fem ? 'Банк забрала' : 'Банк забрал'} ${names.join(', ')}`;
    render();
    if (mine) {
      sfx(mine.won >= BIG * 10 ? 'win' : 'coin');
      popText(table, `+${mine.won}`, { y: '70%', cls: 'gold' });
    } else if (!me().folded) {
      sfx('lose');
    }
    if (chips() < BIG) {
      overlay(table, '<div class="game-over-title">Фишки закончились</div><div class="game-over-text">Банк выдаст новые 1000 — играем дальше.</div>',
        [{ label: 'Взять фишки', run: () => { refillChips(); render(); } }], { icon: '🏦' });
    }
  }

  function deal() {
    gen += 1;
    busy = false;
    raising = false;
    shown = false;
    message = '';
    if (!t || t.players.length !== bots + 1) {
      t = createTable(['Вы', ...BOTS.slice(0, bots).map((b) => b.name)], [chips(), ...BOTS.slice(0, bots).map(() => BOT_STACK)]);
    }
    me().stack = chips();
    newHand(t);
    syncChips();
    for (let i = 0; i < 4; i += 1) setTimeout(() => sfx('deal'), i * 90);
    render();
    play();
  }

  function newTable() {
    gen += 1;
    t = null;
    deal();
  }

  controls.addEventListener('click', (event) => {
    const button = event.target.closest('[data-act], [data-raise]');
    if (!button || button.disabled) return;
    tap();
    const what = button.dataset.act;
    if (what === 'deal') {
      deal();
      return;
    }
    if (what === 'refill') {
      refillChips();
      sfx('coin');
      render();
      return;
    }
    if (!inHand() || t.toAct !== 0 || busy) return;
    if (what === 'raise' || what === 'back') {
      raising = what === 'raise';
      sfx('tap');
      render();
      return;
    }
    const o = options(t);
    const move = button.dataset.raise ? { type: 'raise', to: Number(button.dataset.raise) }
      : what === 'call' ? { type: o.canCheck ? 'check' : 'call' } : { type: 'fold' };
    raising = false;
    act(t, move);
    syncChips();
    sfx(move.type === 'fold' ? 'tap' : 'chip');
    play();
  });

  // Сменить соперников посреди раздачи — значит её бросить: переспрашиваем.
  const guard = () => !inHand() || me().folded || askRestart(table);
  bindChips(root, 'bots', (value) => {
    bots = Number(value);
    save('poker-bots', bots);
    newTable();
  }, { guard });
  bindChips(root, 'level', (value) => {
    level = value;
    save('poker-level', level);
  });

  t = createTable(['Вы', ...BOTS.slice(0, bots).map((b) => b.name)], [chips(), ...BOTS.slice(0, bots).map(() => BOT_STACK)]);
  render();
}
