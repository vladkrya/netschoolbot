// Русская рулетка (понарошку): в барабане шесть гнёзд и один заряд. По
// очереди жмут на курок; перед выстрелом можно раз крутануть барабан.
// «Бах» — игрок выбывает, барабан заряжают заново. Остался один — он и
// победил. Соперники — боты.
'use strict';

import { best, bindChips, chipsRow, load, notify, rand, save, saveBest, tap, wait } from './kit.js';

const BOT_NAMES = ['Бот Вася', 'Бот Маша', 'Бот Петя', 'Бот Оля'];
export const CHAMBERS = 6;

/** Шанс, что следующий выстрел — «бах», если барабан не крутили. */
export function risk(shots) {
  return 1 / (CHAMBERS - shots);
}

export function start(root, ctx) {
  let bots = Number(load('roulette-bots', 2)) || 2;
  let players;
  let turn;
  let bullet;
  let chamber;
  let shots;
  let spun;
  let busy = false;
  let over = true;

  root.innerHTML = `<div class="game roulette">
      ${chipsRow('bots', [['1', '1 бот'], ['2', '2 бота'], ['3', '3 бота'], ['4', '4 бота']], String(bots))}
      <div class="game-stage rr-stage">
        <div class="rr-players" data-players></div>
        <div class="rr-drum" data-drum>${Array.from({ length: CHAMBERS }, (_, i) =>
          `<span class="rr-hole" style="--a:${i * 60}deg"></span>`).join('')}<span class="rr-axis"></span></div>
        <div class="rr-status" data-status></div>
        <div class="rr-odds muted small" data-odds></div>
        <div class="rr-actions">
          <button class="btn outline" data-spin>Крутануть барабан</button>
          <button class="btn" data-pull>Нажать на курок</button>
        </div>
        <button class="btn" data-again hidden>Новая игра</button>
      </div>
      <div class="muted small game-hint">Побед: <b data-best>${best('roulette')}</b> · игра понарошку</div></div>`;
  const $ = (sel) => root.querySelector(sel);
  const drum = $('[data-drum]');
  let angle = 0;

  function load6() {
    bullet = rand(CHAMBERS);
    chamber = 0;
    shots = 0;
  }

  function render(status = '') {
    $('[data-players]').innerHTML = players.map((p, i) => `<div class="rr-player${p.out ? ' out' : ''}${i === turn && !over ? ' now' : ''}">
        <span class="rr-face">${p.out ? '💥' : p.bot ? '🤖' : '😎'}</span><span>${p.name}</span></div>`).join('');
    $('[data-status]').textContent = status;
    $('[data-odds]').textContent = over ? '' : `Шанс «баха» сейчас: 1 из ${CHAMBERS - shots}`;
    const mine = !over && turn === 0 && !busy;
    $('[data-spin]').disabled = !mine || spun;
    $('[data-pull]').disabled = !mine;
    $('.rr-actions').hidden = over || players[0].out;
    $('[data-again]').hidden = !over;
  }

  function rotate(turns) {
    angle += turns * 60;
    drum.style.transform = `rotate(${angle}deg)`;
  }

  const alive = () => players.filter((p) => !p.out);
  const nextTurn = () => {
    do turn = (turn + 1) % players.length; while (players[turn].out);
    spun = false;
  };

  async function doSpin() {
    tap('medium');
    spun = true;
    bullet = rand(CHAMBERS);
    chamber = 0;
    shots = 0;
    rotate(CHAMBERS * 2 + rand(CHAMBERS));
    drum.classList.add('spinning');
    await wait(ctx, 700);
    drum.classList.remove('spinning');
  }

  async function doPull() {
    const p = players[turn];
    render(`${p.name} жмёт на курок…`);
    await wait(ctx, 650);
    const bang = chamber === bullet;
    chamber += 1;
    shots += 1;
    rotate(1);
    if (bang) {
      p.out = true;
      notify(turn === 0 ? 'error' : 'warning');
      drum.classList.add('bang');
      setTimeout(() => drum.classList.remove('bang'), 500);
      render(`💥 Бах! ${p.name} выбывает`);
      load6();
      await wait(ctx, 1300);
    } else {
      tap('light');
      render(`Щёлк — ${p.name} в игре`);
      await wait(ctx, 700);
    }
    if (alive().length === 1) {
      over = true;
      const winner = alive()[0];
      if (winner === players[0]) {
        saveBest('roulette', best('roulette') + 1);
        $('[data-best]').textContent = String(best('roulette'));
        notify('success');
      }
      render(winner === players[0] ? '🏆 Вы остались последним!' : `🏆 Победил ${winner.name}`);
      return;
    }
    nextTurn();
  }

  async function run() {
    if (busy) return;
    busy = true;
    try {
      while (!over && players[turn].bot) {
        render(`${players[turn].name} думает…`);
        await wait(ctx, 700 + rand(500));
        // Бот крутит барабан, когда шанс «баха» стал большим.
        if (!spun && shots >= 2 && Math.random() < 0.6) {
          render(`${players[turn].name} крутит барабан`);
          await doSpin();
        }
        await doPull();
      }
    } catch {
      return;
    } finally {
      busy = false;
    }
    render(over ? $('[data-status]').textContent : 'Ваш ход');
  }

  function newGame() {
    players = [{ name: 'Вы', bot: false, out: false },
      ...BOT_NAMES.slice(0, bots).map((name) => ({ name, bot: true, out: false }))];
    turn = rand(players.length);
    spun = false;
    over = false;
    load6();
    render(turn === 0 ? 'Ваш ход' : '');
    run();
  }

  $('[data-spin]').addEventListener('click', async () => {
    if (busy || over || turn !== 0 || spun) return;
    busy = true;
    try {
      await doSpin();
    } catch {
      return;
    } finally {
      busy = false;
    }
    render('Барабан раскручен — теперь курок');
  });
  $('[data-pull]').addEventListener('click', async () => {
    if (busy || over || turn !== 0) return;
    busy = true;
    try {
      await doPull();
    } catch {
      return;
    } finally {
      busy = false;
    }
    if (!over) run();
  });
  $('[data-again]').addEventListener('click', () => { tap(); newGame(); });
  bindChips(root, 'bots', (value) => {
    bots = Number(value);
    save('roulette-bots', bots);
    // Идёт выстрел — новое число ботов будет со следующей игры.
    if (!busy) newGame();
  });
  newGame();
}
