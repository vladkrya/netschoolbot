// Подкидной дурак на 36 карт: вы и 1–3 бота. Ходят по кругу; подкидывать
// можно карты тех достоинств, что уже на столе, но не больше, чем карт у
// отбивающегося (и не больше шести). Кто первым избавился от карт при пустой
// колоде — вышел; последний с картами — дурак. Боты играют на одном из трёх
// уровней; партия сохраняется и доигрывается после выхода.
'use strict';

import {
  askRestart, best, bindChips, cardHtml, chipsRow, deck, flip, isRed, load, overlay, popText, rand, save, saveBest, sfx, shake, tap, wait,
} from './kit.js';

export const LEVELS = { easy: 'Лёгкий', medium: 'Средний', hard: 'Сложный' };

export function beats(attack, defend, trump) {
  if (defend.s === attack.s) return defend.r > attack.r;
  return defend.s === trump && attack.s !== trump;
}

const weight = (card, trump) => card.r + (card.s === trump ? 20 : 0);

// ───────────── Правила ─────────────

export function createGame(names, cards = deck(6, 14)) {
  const g = {
    players: names.map((name, i) => ({ name, bot: i > 0, hand: [], out: false })),
    deck: cards,
    trumpCard: cards[0],
    trump: cards[0].s,
    table: [],
    taking: false,
    passed: new Set(),
    attacker: 0,
    defender: 1,
    limit: 6,
    finished: [],
    // Отбой: сложный бот помнит вышедшие карты и не «раздаёт» их соперникам.
    discard: [],
    over: false,
    loser: null,
  };
  for (let round = 0; round < 6; round += 1) g.players.forEach((p) => p.hand.push(g.deck.pop()));
  // Первым ходит тот, у кого младший козырь.
  let lowest = null;
  g.players.forEach((p, i) => p.hand.forEach((card) => {
    if (card.s === g.trump && (!lowest || card.r < lowest.r)) lowest = { r: card.r, i };
  }));
  g.attacker = lowest ? lowest.i : 0;
  g.defender = nextActive(g, g.attacker);
  g.limit = Math.min(6, g.players[g.defender].hand.length);
  return g;
}

export function nextActive(g, i) {
  const n = g.players.length;
  for (let step = 1; step <= n; step += 1) {
    const j = (i + step) % n;
    if (!g.players[j].out) return j;
  }
  return i;
}

const undefended = (g) => g.table.filter((pair) => !pair.d).length;

export function canAdd(g) {
  return g.table.length < g.limit && undefended(g) < g.players[g.defender].hand.length;
}

/** Можно ли игроку p выложить карту: первый ход или подкидывание. */
export function canPlay(g, p, card) {
  if (g.over || p === g.defender) return false;
  if (!g.table.length) return p === g.attacker;
  return canAdd(g) && g.table.some((pair) => pair.a.r === card.r || pair.d?.r === card.r);
}

/** Подкидывают все, кроме отбивающегося, начиная с того, кто ходил. */
export function throwers(g) {
  const list = [];
  let i = g.attacker;
  do {
    if (i !== g.defender && !g.players[i].out) list.push(i);
    i = (i + 1) % g.players.length;
  } while (i !== g.attacker);
  return list;
}

/** Чей сейчас ход: { who, kind } — open, defend, throw — или { kind: 'end' }. */
export function pending(g) {
  if (g.over) return { kind: 'over' };
  if (!g.table.length) return { who: g.attacker, kind: 'open' };
  if (!g.taking && undefended(g)) return { who: g.defender, kind: 'defend' };
  if (!canAdd(g)) return { kind: 'end' };
  const who = throwers(g).find((i) => !g.passed.has(i));
  return who === undefined ? { kind: 'end' } : { who, kind: 'throw' };
}

function removeCard(hand, card) {
  const i = hand.findIndex((c) => c.r === card.r && c.s === card.s);
  if (i >= 0) hand.splice(i, 1);
  return i >= 0;
}

export function play(g, p, card) {
  if (!canPlay(g, p, card) || !removeCard(g.players[p].hand, card)) return false;
  g.table.push({ a: card, d: null });
  g.passed.clear();
  return true;
}

export function defend(g, card, index) {
  const pair = g.table[index];
  if (g.taking || !pair || pair.d || !beats(pair.a, card, g.trump)) return false;
  if (!removeCard(g.players[g.defender].hand, card)) return false;
  pair.d = card;
  return true;
}

export function take(g) {
  g.taking = true;
  g.passed.clear();
}

export function pass(g, p) {
  g.passed.add(p);
}

/** Конец кона: бито или забрал; добор из колоды; кто вышел; кто ходит. */
export function endBout(g) {
  const defender = g.defender;
  const cards = g.table.flatMap((pair) => (pair.d ? [pair.a, pair.d] : [pair.a]));
  if (g.taking) g.players[defender].hand.push(...cards);
  else (g.discard ||= []).push(...cards);
  const tookIt = g.taking;
  // Добирают: тот, кто ходил, другие подкидывавшие, отбивавшийся последним.
  for (const i of [...throwers(g), defender]) {
    const hand = g.players[i].hand;
    while (hand.length < 6 && g.deck.length) hand.push(g.deck.pop());
  }
  g.table = [];
  g.taking = false;
  g.passed.clear();
  g.players.forEach((p, i) => {
    if (!p.out && !p.hand.length && !g.deck.length) {
      p.out = true;
      g.finished.push(i);
    }
  });
  const left = g.players.map((p, i) => i).filter((i) => !g.players[i].out);
  if (left.length <= 1) {
    g.over = true;
    g.loser = left.length ? left[0] : null;
    return;
  }
  // Отбился — ходит он сам; забрал — ходит следующий за ним.
  let attacker = tookIt ? nextActive(g, defender) : defender;
  if (g.players[attacker].out) attacker = nextActive(g, attacker);
  g.attacker = attacker;
  g.defender = nextActive(g, attacker);
  g.limit = Math.min(6, g.players[g.defender].hand.length);
}

// ───────────── Боты ─────────────

// Уровни: лёгкий бот ходит почти наугад и часто берёт зря; средний — самой
// младшей картой; сложный перебирает ходы, доигрывая партию в уме (ниже).

const byWeight = (g) => (a, b) => weight(a, g.trump) - weight(b, g.trump);
const same = (a, b) => a.r === b.r && a.s === b.s;

export function botOpen(g, p, level = 'medium') {
  const hand = [...g.players[p].hand].sort(byWeight(g));
  if (level === 'easy') return hand[rand(Math.min(3, hand.length))];
  return hand[0];
}

export function botThrow(g, p, level = 'medium') {
  if (level === 'easy' && Math.random() < 0.5) return null;
  const ranks = new Set(g.table.flatMap((pair) => (pair.d ? [pair.a.r, pair.d.r] : [pair.a.r])));
  const options = g.players[p].hand
    .filter((card) => ranks.has(card.r))
    // Пока колода не кончилась, козыри и старшие карты бот бережёт.
    .filter((card) => !g.deck.length || (card.s !== g.trump && card.r <= (g.taking ? 12 : 10)))
    .sort(byWeight(g));
  return options[0] || null;
}

/** Чем отбиться: { card, index } или null — забрать. */
export function botDefend(g, level = 'medium') {
  const hand = g.players[g.defender].hand;
  const index = g.table.findIndex((pair) => !pair.d);
  const attack = g.table[index].a;
  const options = hand.filter((card) => beats(attack, card, g.trump)).sort(byWeight(g));
  if (level === 'easy') {
    if (!options.length || Math.random() < 0.25) return null;
    return { card: options[rand(options.length)], index };
  }
  const card = options[0];
  if (!card) return null;
  // Старший козырь на мелочь в начале игры не тратит — лучше забрать.
  if (card.s === g.trump && card.r >= 13 && attack.s !== g.trump && g.deck.length > 8) return null;
  return { card, index };
}

/** Ход: { kind: 'play' | 'pass' | 'take' | 'defend', card?, index? }. */
export function applyMove(g, p, move) {
  if (move.kind === 'play') return play(g, p, move.card);
  if (move.kind === 'defend') return defend(g, move.card, move.index);
  if (move.kind === 'take') take(g);
  else pass(g, p);
  return true;
}

/** Ход бота лёгкого или среднего уровня. */
export function botMove(g, p, level = 'medium') {
  const turn = pending(g);
  if (turn.kind === 'defend') {
    const move = botDefend(g, level);
    return move ? { kind: 'defend', ...move } : { kind: 'take' };
  }
  const card = turn.kind === 'open' ? botOpen(g, p, level) : botThrow(g, p, level);
  return card && canPlay(g, p, card) ? { kind: 'play', card } : { kind: 'pass' };
}

// ───────────── Сложный бот: перебор с доигрыванием ─────────────
// Для каждого своего варианта хода бот много раз доигрывает партию в уме:
// чужие руки и колоду раздаёт наугад из карт, которых не видел (известны
// своя рука, отбой, стол и козырь под колодой), а в доигровке все ходят
// как средний бот, изредка ошибаясь. Выбирает вариант, после которого
// реже остаётся дураком.
// Чужих карт не подсматривает — только помнит отбой.

const copyGame = (g) => ({
  ...g,
  players: g.players.map((player) => ({ ...player, hand: [...player.hand] })),
  deck: [...g.deck],
  table: g.table.map((pair) => ({ ...pair })),
  passed: new Set(g.passed),
  finished: [...g.finished],
  discard: [...(g.discard || [])],
});

/** Копия партии, где чужие руки и колода розданы наугад из невиданных карт. */
function guessWorld(g, p) {
  const copy = copyGame(g);
  const seen = [...copy.players[p].hand, ...copy.discard, ...copy.table.flatMap((pair) => (pair.d ? [pair.a, pair.d] : [pair.a]))];
  if (copy.deck.length) seen.push(copy.trumpCard);
  const unseen = deck(6, 14).filter((card) => !seen.some((c) => same(c, card)));
  copy.players.forEach((player, i) => { if (i !== p) player.hand = unseen.splice(0, player.hand.length); });
  if (copy.deck.length) copy.deck = [copy.trumpCard, ...unseen.splice(0, copy.deck.length - 1)];
  return copy;
}

// Доля «лёгких» ходов в доигровке. Без неё бот подстраивался под ровную
// игру среднего бота и проигрывал непредсказуемым соперникам; с ней — бьёт
// и тех и других (проверено тысячами партий в симуляции).
const ROLLOUT_SLIPS = 0.15;

/** Доиграть партию средними ботами, иногда ошибающимися; вернуть номер дурака. */
function playOut(g) {
  for (let steps = 0; !g.over && steps < 2000; steps += 1) {
    const turn = pending(g);
    if (turn.kind === 'end') endBout(g);
    else applyMove(g, turn.who, botMove(g, turn.who, Math.random() < ROLLOUT_SLIPS ? 'easy' : 'medium'));
  }
  return g.loser;
}

/** Ход сложного бота: вариант, после которого он реже всего дурак. */
export function smartMove(g, p, samples = 48) {
  const turn = pending(g);
  const hand = g.players[p].hand;
  const moves = [];
  if (turn.kind === 'defend') {
    moves.push({ kind: 'take' });
    g.table.forEach((pair, index) => {
      if (!pair.d) hand.forEach((card) => { if (beats(pair.a, card, g.trump)) moves.push({ kind: 'defend', card, index }); });
    });
  } else {
    if (turn.kind === 'throw') moves.push({ kind: 'pass' });
    hand.forEach((card) => { if (canPlay(g, p, card)) moves.push({ kind: 'play', card }); });
  }
  if (moves.length < 2) return moves[0] || botMove(g, p);
  // Одни и те же расклады для всех вариантов — сравнение честнее.
  const worlds = Array.from({ length: samples }, () => guessWorld(g, p));
  let best = moves[0];
  let bestScore = -1;
  for (const move of moves) {
    let score = 0;
    for (const world of worlds) {
      const copy = copyGame(world);
      applyMove(copy, p, move);
      const loser = playOut(copy);
      score += loser === p ? 0 : loser === null ? 0.5 : 1;
    }
    if (score > bestScore) {
      bestScore = score;
      best = move;
    }
  }
  return best;
}

// ───────────── Экран ─────────────

const SUIT_ORDER = ['♠', '♣', '♥', '♦'];
const BOTS = [
  { name: 'Вася', face: '🤖', color: '#ff7a59' },
  { name: 'Маша', face: '👾', color: '#b06bff', fem: true },
  { name: 'Петя', face: '🦊', color: '#2fb7a3' },
];
/** Глагол в роде бота: «вышел» / «вышла». */
const said = (bot, male, female) => (bot.fem ? female : male);
const PHRASES = {
  take: ['Беру!', 'Забираю', 'Ладно, беру'],
  pass: ['Бито', 'Пас', 'Хватит'],
  beat: ['Отбился!', 'Легко', 'Ха!'],
  fool: ['Эх…', 'Ну вот', 'Нечестно!'],
};
const pick = (list) => list[Math.floor(Math.random() * list.length)];

/** Партия в JSON и обратно: Set пасовавших — массивом. */
const pack = (g) => ({ ...g, passed: [...g.passed] });
const unpack = (data) => ({ ...data, passed: new Set(data.passed) });

export function start(root, ctx) {
  let bots = Number(load('durak-bots', 1)) || 1;
  let level = LEVELS[load('durak-level')] ? load('durak-level') : 'medium';
  let g;
  // Сделан ли в партии хоть один ход: новую можно начать, не спрашивая.
  let touched = false;
  let target = -1;
  let busy = false;
  // Номер партии: новая партия обрывает ходы ботов в старой.
  let gen = 0;
  let discarded = 0;
  let status = '';
  const bubbles = {};
  const origin = new Map();

  root.innerHTML = `<div class="game durak">
      ${chipsRow('bots', [['1', '1 бот'], ['2', '2 бота'], ['3', '3 бота']], String(bots))}
      ${chipsRow('level', Object.entries(LEVELS), level)}
      <div class="game-stage felt dk-stage" data-stage></div>
      <div class="dk-foot dk-foot-row"><span>🏆 Побед: <b data-best>${best('durak')}</b></span>
        <button class="game-btn ghost small" data-restart>Новая партия</button></div></div>`;
  const stage = root.querySelector('[data-stage]');

  const sorted = (hand) => [...hand].sort((a, b) => ((a.s === g.trump) - (b.s === g.trump))
    || SUIT_ORDER.indexOf(a.s) - SUIT_ORDER.indexOf(b.s) || a.r - b.r);
  const key = (card) => `${card.r}${card.s}`;
  const rectOf = (sel) => stage.querySelector(sel)?.getBoundingClientRect() || null;

  function say(i, text) {
    bubbles[i] = { text, until: performance.now() + 1500 };
  }

  function html() {
    const turn = pending(g);
    const me = g.players[0];
    const now = performance.now();
    const role = (i) => {
      const bot = BOTS[i - 1];
      if (g.players[i].out) return g.finished.indexOf(i) === 0 ? said(bot, 'вышел первым', 'вышла первой') : said(bot, 'вышел', 'вышла');
      if (i === g.defender) return 'отбивается';
      if (i === g.attacker) return 'ходит';
      return '';
    };
    const opponents = g.players.slice(1).map((p, k) => {
      const i = k + 1;
      const bot = BOTS[k];
      const bubble = bubbles[i]?.until > now ? `<span class="bubble">${bubbles[i].text}</span>` : '';
      const fan = Math.min(p.hand.length, 7);
      return `<div class="dk-opp${turn.who === i ? ' thinking' : ''}${p.out ? ' out' : ''}${i === g.defender ? ' defending' : ''}" data-opp="${i}">
          ${bubble}
          <div class="dk-avatar" style="--c:${bot.color}">${p.out ? '🏁' : bot.face}<span class="dk-badge">${p.hand.length}</span></div>
          <div class="dk-backs">${Array.from({ length: fan }, (_, j) => `<span class="pc back mini" style="rotate:${(j - (fan - 1) / 2) * 7}deg"></span>`).join('')}</div>
          <div class="dk-name">${bot.name}</div><div class="dk-role">${role(i)}</div></div>`;
    }).join('');
    const table = g.table.map((pair, i) => `<div class="dk-pair${i === target && !pair.d ? ' target' : ''}" data-pair="${i}">
        ${cardHtml(pair.a, { key: key(pair.a) })}${pair.d ? cardHtml(pair.d, { key: key(pair.d), cls: 'dk-cover' }) : ''}</div>`).join('');
    const actions = [];
    if (turn.who === 0 && turn.kind === 'defend') actions.push('<button class="game-btn warn" data-act="take">Беру</button>');
    if (turn.who === 0 && turn.kind === 'throw') {
      actions.push(`<button class="game-btn" data-act="pass">${g.taking ? 'Пусть берёт' : 'Бито'}</button>`);
    }
    const mine = sorted(me.hand);
    const n = mine.length;
    const hand = mine.map((card, i) => {
      const playable = (turn.who === 0 && (turn.kind === 'open' || turn.kind === 'throw') && canPlay(g, 0, card))
        || (turn.who === 0 && turn.kind === 'defend' && g.table.some((pair) => !pair.d && beats(pair.a, card, g.trump)));
      const angle = (i - (n - 1) / 2) * Math.min(5, 40 / n);
      return cardHtml(card, {
        key: key(card),
        attrs: `data-card="${key(card)}" style="rotate:${angle}deg;--lift:${Math.abs(angle) * 0.9}px"`,
        cls: `${playable ? 'playable' : turn.who === 0 ? 'dim' : ''}${card.s === g.trump ? ' trump' : ''}`,
      });
    }).join('');
    const myTurn = turn.who === 0 && !me.out;
    return `<div class="dk-opps">${opponents}</div>
      <div class="dk-mid">
        <div class="dk-deck" data-deck>${g.deck.length
          ? `<span class="dk-trump-badge${isRed(g.trumpCard) ? ' red' : ''}">козырь ${g.trump}</span>${cardHtml(g.trumpCard, { cls: 'dk-trump' })}${g.deck.length > 1 ? '<span class="pc back dk-pile"></span><span class="pc back dk-pile two"></span>' : ''}<span class="dk-count">${g.deck.length}</span>`
          : `<span class="dk-suit${['♥', '♦'].includes(g.trump) ? ' red' : ''}">${g.trump}</span><span class="dk-count">козырь</span>`}</div>
        <div class="dk-table">${table || '<span class="dk-empty">Стол пуст</span>'}</div>
        <div class="dk-discard" data-discard>${discarded ? '<span class="pc back dk-pile tilt"></span><span class="pc back dk-pile tilt two"></span>' : ''}<span class="dk-count">${discarded ? 'бито' : ''}</span></div>
      </div>
      <div class="dk-status${myTurn ? ' mine' : ''}">${status}</div>
      <div class="dk-actions">${actions.join('')}</div>
      <div class="dk-hand" style="--n:${n}">${hand}</div>`;
  }

  function statusText() {
    const turn = pending(g);
    const me = g.players[0];
    if (g.over) return '';
    if (me.out) return 'Вы вышли — боты доигрывают';
    if (turn.who === 0 && turn.kind === 'open') return 'Ваш ход — выложите карту';
    if (turn.who === 0 && turn.kind === 'defend') return 'Отбивайтесь — или заберите карты';
    if (turn.who === 0 && turn.kind === 'throw') return g.taking ? 'Можно подкинуть ещё' : 'Подкиньте карту или «Бито»';
    if (turn.who !== undefined) return `${BOTS[turn.who - 1].name} думает…`;
    return '';
  }

  /** Перерисовка с перелётом карт: из руки на стол, с колоды в руку, от бота на стол. */
  function render() {
    save('durak-game', g.over ? null : { g: pack(g), bots, level, discarded, touched });
    status = statusText();
    const deckRect = rectOf('[data-deck]');
    flip(stage, () => { stage.innerHTML = html(); }, {
      duration: 380,
      stagger: 70,
      enterFrom: (el) => {
        if (el.closest('.dk-hand')) return deckRect;
        const who = origin.get(el.dataset.k);
        return who ? rectOf(`[data-opp="${who}"] .dk-avatar`) : null;
      },
    });
    root.querySelector('[data-best]').textContent = String(best('durak'));
  }

  /** Карты со стола улетают к тому, кто забрал, или в отбой. */
  function sweep(toSelector) {
    const to = rectOf(toSelector);
    if (!to) return;
    stage.querySelectorAll('.dk-table .pc').forEach((el, i) => {
      const from = el.getBoundingClientRect();
      const ghost = el.cloneNode(true);
      ghost.classList.add('ghost-card');
      Object.assign(ghost.style, { left: `${from.left}px`, top: `${from.top}px`, width: `${from.width}px`, height: `${from.height}px` });
      document.body.append(ghost);
      const anim = ghost.animate?.([
        { translate: '0 0', rotate: '0deg', opacity: 1 },
        { translate: `${to.left + to.width / 2 - from.left - from.width / 2}px ${to.top + to.height / 2 - from.top - from.height / 2}px`, rotate: `${(i % 2 ? 1 : -1) * 25}deg`, opacity: 0.2, scale: '0.6' },
      ], { duration: 420, delay: i * 30, easing: 'cubic-bezier(.4, 0, .6, 1)', fill: 'forwards' });
      if (anim) anim.onfinish = () => ghost.remove();
      else ghost.remove();
    });
  }

  async function advance() {
    if (busy) return;
    busy = true;
    const mine = gen;
    const pause = async (ms) => {
      await wait(ctx, ms);
      if (mine !== gen) throw new Error('новая партия');
    };
    try {
      for (;;) {
        render();
        const turn = pending(g);
        if (turn.kind === 'over') {
          finish();
          return;
        }
        const fast = g.players[0].out;
        if (turn.kind === 'end') {
          await pause(fast ? 250 : 600);
          const taker = g.defender;
          if (g.taking) {
            if (taker !== 0) {
              say(taker, pick(PHRASES.take));
              sweep(`[data-opp="${taker}"] .dk-avatar`);
            } else {
              popText(stage, 'Вы забираете', { y: '62%' });
            }
          } else {
            discarded += g.table.length * 2;
            if (taker !== 0) say(taker, pick(PHRASES.beat));
            else popText(stage, 'Отбились! 💪', { y: '62%', cls: 'gold' });
            sweep('[data-discard]');
          }
          sfx('deal');
          // Взявший человек забирает карты в руку — это покажет перелёт.
          if (g.taking && taker === 0) {
            endBout(g);
          } else {
            stage.querySelectorAll('.dk-table .pc').forEach((el) => { el.style.visibility = 'hidden'; });
            endBout(g);
          }
          target = -1;
          await pause(120);
          continue;
        }
        if (turn.who === 0) {
          // Подкинуть нечего — пасуем за человека, чтобы не жать кнопку зря.
          if (turn.kind === 'throw' && !g.players[0].hand.some((card) => canPlay(g, 0, card))) {
            await pause(450);
            pass(g, 0);
            continue;
          }
          return;
        }
        // Бот «думает»: сначала доигрывается перелёт карт, потом считается
        // ход — перебор сложного бота занимает процессор и не дёргает анимацию.
        const think = fast ? 250 : 700 + Math.random() * 400;
        await pause(Math.min(think, 400));
        const started = performance.now();
        const move = level === 'hard' ? smartMove(g, turn.who) : botMove(g, turn.who, level);
        await pause(Math.max(0, think - 400 - (performance.now() - started)));
        touched = true;
        if (move.card) origin.set(key(move.card), turn.who);
        applyMove(g, turn.who, move);
        if (move.card) sfx('card');
        else if (move.kind === 'take') {
          say(turn.who, pick(PHRASES.take));
          sfx('tap');
        } else {
          say(turn.who, pick(PHRASES.pass));
        }
        tap('soft');
      }
    } catch {
      /* игру закрыли — боты замолкают */
    } finally {
      if (mine === gen) busy = false;
    }
  }

  function finish() {
    const won = g.loser !== null && g.loser !== 0;
    if (won) saveBest('durak', best('durak') + 1);
    save('durak-game', null);
    if (g.loser && g.loser !== 0) say(g.loser, pick(PHRASES.fool));
    render();
    const title = g.loser === null ? 'Ничья' : won ? 'Победа!' : 'Вы — дурак';
    const fool = BOTS[g.loser - 1];
    const note = g.loser === null ? 'Все вышли одновременно' : won ? `Дураком ${said(fool, 'остался', 'осталась')} ${fool.name}` : 'Повезёт в следующий раз';
    setTimeout(() => {
      if (ctx.signal.aborted) return;
      overlay(stage, `<div class="game-over-title">${title}</div><div class="game-over-text">${note}</div>`,
        [{ label: 'Ещё партию', run: newGame }], { icon: won ? '🏆' : g.loser === null ? '🤝' : '🃏', win: won, lose: g.loser === 0 });
    }, 700);
  }

  function newGame() {
    gen += 1;
    busy = false;
    g = createGame(['Вы', ...BOTS.slice(0, bots).map((b) => b.name)]);
    target = -1;
    discarded = 0;
    touched = false;
    origin.clear();
    stage.innerHTML = '';
    stage.querySelector('.game-over')?.remove();
    render();
    for (let i = 0; i < 4; i += 1) setTimeout(() => sfx('deal'), i * 110);
    if (g.attacker !== 0) {
      say(g.attacker, `Хожу первым — у меня младший козырь`);
      render();
    }
    setTimeout(advance, g.attacker === 0 ? 300 : 1400);
  }

  stage.addEventListener('click', (event) => {
    if (busy || !g || g.over) return;
    const turn = pending(g);
    if (turn.who !== 0) return;
    const act = event.target.closest('[data-act]');
    if (act) {
      tap();
      sfx('tap');
      touched = true;
      if (act.dataset.act === 'take') take(g);
      else pass(g, 0);
      advance();
      return;
    }
    const pair = event.target.closest('[data-pair]');
    if (pair && turn.kind === 'defend') {
      const index = Number(pair.dataset.pair);
      if (!g.table[index].d) {
        target = index;
        tap('soft');
        sfx('tap');
        render();
      }
      return;
    }
    const cardEl = event.target.closest('[data-card]');
    if (!cardEl) return;
    const card = g.players[0].hand.find((c) => key(c) === cardEl.dataset.card);
    if (turn.kind === 'defend') {
      // Бьём выбранную карту, иначе первую, которую эта карта бьёт.
      const options = g.table.map((p, i) => i).filter((i) => !g.table[i].d && beats(g.table[i].a, card, g.trump));
      const index = options.includes(target) ? target : options[0];
      if (index === undefined || !defend(g, card, index)) return shake(cardEl);
    } else if (!play(g, 0, card)) {
      return shake(cardEl);
    }
    sfx('card');
    tap('light');
    target = -1;
    touched = true;
    advance();
  });

  const guard = () => !touched || g.over || askRestart(stage);
  bindChips(root, 'bots', (value) => {
    bots = Number(value);
    save('durak-bots', bots);
    newGame();
  }, { guard });
  bindChips(root, 'level', (value) => {
    level = value;
    save('durak-level', level);
    newGame();
  }, { guard });
  root.querySelector('[data-restart]').addEventListener('click', async () => {
    tap();
    if (await guard()) newGame();
  });
  // Неоконченная партия — с того же места, боты продолжают свой ход.
  const saved = load('durak-game');
  if (saved?.g?.players?.length === bots + 1 && saved.level === level && !saved.g.over) {
    g = unpack(saved.g);
    discarded = saved.discarded || 0;
    touched = saved.touched ?? true;
    render();
    setTimeout(advance, 400);
  } else {
    newGame();
  }
}
