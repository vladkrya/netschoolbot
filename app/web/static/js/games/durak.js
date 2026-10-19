// Подкидной дурак на 36 карт: вы и 1–3 бота. Ходят по кругу; подкидывать
// можно карты тех достоинств, что уже на столе, но не больше, чем карт у
// отбивающегося (и не больше шести). Кто первым избавился от карт при пустой
// колоде — вышел; последний с картами — дурак.
'use strict';

import {
  best, bindChips, cardHtml, chipsRow, deck, load, notify, save, saveBest, shake, tap, wait,
} from './kit.js';

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

export function botOpen(g, p) {
  const hand = [...g.players[p].hand].sort((a, b) => weight(a, g.trump) - weight(b, g.trump));
  return hand[0];
}

export function botThrow(g, p) {
  const ranks = new Set(g.table.flatMap((pair) => (pair.d ? [pair.a.r, pair.d.r] : [pair.a.r])));
  const options = g.players[p].hand
    .filter((card) => ranks.has(card.r))
    // Пока колода не кончилась, козыри и старшие карты бот бережёт.
    .filter((card) => !g.deck.length || (card.s !== g.trump && card.r <= (g.taking ? 12 : 10)))
    .sort((a, b) => weight(a, g.trump) - weight(b, g.trump));
  return options[0] || null;
}

/** Чем отбиться: { card, index } или null — забрать. */
export function botDefend(g) {
  const hand = g.players[g.defender].hand;
  const index = g.table.findIndex((pair) => !pair.d);
  const attack = g.table[index].a;
  const options = hand.filter((card) => beats(attack, card, g.trump))
    .sort((a, b) => weight(a, g.trump) - weight(b, g.trump));
  const card = options[0];
  if (!card) return null;
  // Старший козырь на мелочь в начале игры не тратит — лучше забрать.
  if (card.s === g.trump && card.r >= 13 && attack.s !== g.trump && g.deck.length > 8) return null;
  return { card, index };
}

// ───────────── Экран ─────────────

const SUIT_ORDER = ['♠', '♣', '♥', '♦'];
const BOT_NAMES = ['Бот Вася', 'Бот Маша', 'Бот Петя'];

export function start(root, ctx) {
  let bots = Number(load('durak-bots', 1)) || 1;
  let g;
  let target = -1;
  let busy = false;
  let message = '';

  root.innerHTML = `<div class="game durak">
      ${chipsRow('bots', [['1', '1 бот'], ['2', '2 бота'], ['3', '3 бота']], String(bots))}
      <div class="game-stage dk-stage" data-stage></div>
      <div class="muted small game-hint">Побед: <b data-best>${best('durak')}</b></div></div>`;
  const stage = root.querySelector('[data-stage]');

  const sorted = (hand) => [...hand].sort((a, b) => ((a.s === g.trump) - (b.s === g.trump))
    || SUIT_ORDER.indexOf(a.s) - SUIT_ORDER.indexOf(b.s) || a.r - b.r);
  const key = (card) => `${card.r}${card.s}`;

  function render() {
    const turn = pending(g);
    const me = g.players[0];
    const role = (i) => {
      if (g.players[i].out) return g.finished.indexOf(i) === 0 ? 'вышел первым' : 'вышел';
      if (i === g.defender) return 'отбивается';
      if (i === g.attacker) return 'ходит';
      return '';
    };
    const opponents = g.players.slice(1).map((p, k) => {
      const i = k + 1;
      const thinking = turn.who === i ? ' thinking' : '';
      return `<div class="dk-bot${thinking}${p.out ? ' out' : ''}">
          <div class="dk-backs">${p.hand.slice(0, 8).map(() => '<span class="pc back mini"></span>').join('')}</div>
          <div class="dk-name">${p.name} · ${p.hand.length}</div><div class="dk-role">${role(i)}</div></div>`;
    }).join('');
    const table = g.table.map((pair, i) => `<div class="dk-pair${i === target && !pair.d ? ' target' : ''}" data-pair="${i}">
        ${cardHtml(pair.a)}${pair.d ? cardHtml(pair.d, { cls: 'dk-cover' }) : ''}</div>`).join('');
    let status = message;
    if (!status) {
      if (g.over) status = '';
      else if (me.out) status = 'Вы вышли — боты доигрывают';
      else if (turn.who === 0 && turn.kind === 'open') status = 'Ваш ход — выложите карту';
      else if (turn.who === 0 && turn.kind === 'defend') status = 'Отбивайтесь или заберите карты';
      else if (turn.who === 0 && turn.kind === 'throw') status = g.taking ? 'Можно подкинуть ещё' : 'Подкинуть или «Бито»';
      else if (turn.who !== undefined) status = `${g.players[turn.who].name} думает…`;
    }
    const actions = [];
    if (turn.who === 0 && turn.kind === 'defend') actions.push('<button class="btn outline" data-act="take">Беру</button>');
    if (turn.who === 0 && turn.kind === 'throw') {
      actions.push(`<button class="btn outline" data-act="pass">${g.taking ? 'Пусть берёт' : 'Бито'}</button>`);
    }
    const hand = sorted(me.hand).map((card) => {
      const playable = (turn.who === 0 && (turn.kind === 'open' || turn.kind === 'throw') && canPlay(g, 0, card))
        || (turn.who === 0 && turn.kind === 'defend' && g.table.some((pair) => !pair.d && beats(pair.a, card, g.trump)));
      return cardHtml(card, { attrs: `data-card="${key(card)}"`, cls: playable ? 'playable' : 'dim' });
    }).join('');
    stage.innerHTML = `<div class="dk-bots">${opponents}</div>
      <div class="dk-mid">
        <div class="dk-deck">${g.deck.length ? `${cardHtml(g.trumpCard, { cls: 'dk-trump' })}${g.deck.length > 1 ? '<span class="pc back dk-pile"></span>' : ''}` : `<span class="dk-suit">${g.trump}</span>`}
          <span class="dk-count">${g.deck.length || 'пусто'}</span></div>
        <div class="dk-table">${table || '<span class="muted small">Стол пуст</span>'}</div>
      </div>
      <div class="dk-status">${status}</div>
      <div class="dk-actions">${actions.join('')}</div>
      <div class="dk-hand${me.hand.length > 8 ? ' tight' : ''}" style="--n:${me.hand.length}">${hand}</div>`;
    root.querySelector('[data-best]').textContent = String(best('durak'));
  }

  async function advance() {
    if (busy) return;
    busy = true;
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
          await wait(ctx, fast ? 250 : 700);
          message = !g.taking ? 'Бито' : g.defender === 0 ? 'Вы забираете' : `${g.players[g.defender].name} забирает`;
          render();
          await wait(ctx, fast ? 200 : 600);
          message = '';
          endBout(g);
          target = -1;
          continue;
        }
        if (turn.who === 0) {
          // Подкинуть нечего — пасуем за человека, чтобы не жать кнопку зря.
          if (turn.kind === 'throw' && !g.players[0].hand.some((card) => canPlay(g, 0, card))) {
            await wait(ctx, 350);
            pass(g, 0);
            continue;
          }
          return;
        }
        await wait(ctx, fast ? 250 : 650 + Math.random() * 300);
        if (turn.kind === 'open') {
          play(g, turn.who, botOpen(g, turn.who));
        } else if (turn.kind === 'throw') {
          const card = botThrow(g, turn.who);
          if (card) play(g, turn.who, card);
          else pass(g, turn.who);
        } else if (turn.kind === 'defend') {
          const move = botDefend(g);
          if (move) defend(g, move.card, move.index);
          else take(g);
        }
        tap('soft');
      }
    } catch {
      /* игру закрыли — боты замолкают */
    } finally {
      busy = false;
    }
  }

  function finish() {
    const won = g.loser !== null && g.loser !== 0;
    if (won) {
      notify('success');
      saveBest('durak', best('durak') + 1);
    } else {
      notify('error');
    }
    render();
    const title = g.loser === null ? 'Ничья' : won ? 'Вы выиграли!' : 'Вы — дурак 🙃';
    const note = g.loser === null || g.loser === 0 ? '' : `Дурак — ${g.players[g.loser].name}`;
    stage.querySelector('.dk-status').innerHTML = `<b>${title}</b> ${note}
      <button class="btn" data-act="again">Ещё партию</button>`;
  }

  function newGame() {
    g = createGame(['Вы', ...BOT_NAMES.slice(0, bots)]);
    target = -1;
    message = g.attacker === 0 ? '' : `Первым ходит ${g.players[g.attacker].name} — у него младший козырь`;
    render();
    setTimeout(() => { message = ''; advance(); }, g.attacker === 0 ? 0 : 1200);
  }

  stage.addEventListener('click', (event) => {
    const again = event.target.closest('[data-act="again"]');
    if (again) {
      tap();
      newGame();
      return;
    }
    if (busy || !g || g.over) return;
    const turn = pending(g);
    if (turn.who !== 0) return;
    const act = event.target.closest('[data-act]');
    if (act) {
      tap();
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
    tap('light');
    target = -1;
    advance();
  });

  bindChips(root, 'bots', (value) => {
    bots = Number(value);
    save('durak-bots', bots);
    newGame();
  });
  newGame();
}
