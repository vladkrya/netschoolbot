// Русская рулетка (понарошку): в барабане шесть гнёзд и один заряд. По
// очереди жмут на курок; перед выстрелом можно раз крутануть барабан.
// «Бах» — игрок выбывает, барабан заряжают заново. Остался один — он и
// победил. Соперники — боты.
'use strict';

import {
  askRestart, best, bindChips, chipsRow, load, notify, overlay, quake, rand, save, saveBest, sfx, tap, wait,
} from './kit.js';

const BOTS = [
  { name: 'Вася', face: '🤖' }, { name: 'Маша', face: '👾', fem: true }, { name: 'Петя', face: '🦊' }, { name: 'Оля', face: '🐼', fem: true },
];
/** Глагол по игроку: вы — «жмёте», бот — «жмёт». */
const verb = (p, you, he) => (p.bot ? he : you);
export const CHAMBERS = 6;

/** Шанс, что следующий выстрел — «бах», если барабан не крутили. */
export function risk(shots) {
  return 1 / (CHAMBERS - shots);
}

const drumSvg = () => `<svg viewBox="-100 -100 200 200" class="rr-svg" aria-hidden="true">
    <defs>
      <radialGradient id="rrMetal" cx="35%" cy="30%" r="80%"><stop offset="0" stop-color="#d9dde3"/><stop offset=".55" stop-color="#8b939e"/><stop offset="1" stop-color="#4b525c"/></radialGradient>
      <radialGradient id="rrHole" cx="50%" cy="40%" r="60%"><stop offset="0" stop-color="#0d0f12"/><stop offset="1" stop-color="#2c3036"/></radialGradient>
      <radialGradient id="rrBrass" cx="35%" cy="35%" r="70%"><stop offset="0" stop-color="#fff1b8"/><stop offset=".5" stop-color="#d9a640"/><stop offset="1" stop-color="#8a5a12"/></radialGradient>
    </defs>
    <g class="rr-rotor">
      <circle r="92" fill="url(#rrMetal)" stroke="#3a3f46" stroke-width="3"/>
      ${Array.from({ length: CHAMBERS }, (_, i) => {
        const a = (i * 60 - 90) * Math.PI / 180;
        const x = Math.cos(a) * 54;
        const y = Math.sin(a) * 54;
        const gx = Math.cos(a + Math.PI / 6) * 82;
        const gy = Math.sin(a + Math.PI / 6) * 82;
        return `<ellipse cx="${gx}" cy="${gy}" rx="9" ry="16" transform="rotate(${i * 60 + 30 - 90 + 90} ${gx} ${gy})" fill="#5b626c" opacity=".7"/>
          <circle cx="${x}" cy="${y}" r="24" fill="url(#rrHole)" stroke="#2a2e33" stroke-width="2"/>
          <circle class="rr-bullet" data-ch="${i}" cx="${x}" cy="${y}" r="17" fill="url(#rrBrass)" stroke="#6b4510" stroke-width="2" opacity="0"/>`;
      }).join('')}
      <circle r="18" fill="#6d747e" stroke="#3a3f46" stroke-width="3"/><circle r="6" fill="#2c3036"/>
    </g>
    <path class="rr-pointer" d="M0 -100 L-10 -84 L10 -84 Z" fill="#ff5b4a"/>
  </svg>`;

export function start(root, ctx) {
  let bots = Number(load('roulette-bots', 2)) || 2;
  let players;
  let turn;
  let bullet;
  let shots;
  let spun;
  let busy = false;
  let over = true;
  let angle = 0;
  let gen = 0;
  // Нажимал ли кто-то курок: начатую партию без вопроса не сбрасываем.
  let touched = false;

  root.innerHTML = `<div class="game roulette">
      ${chipsRow('bots', [['1', '1 бот'], ['2', '2 бота'], ['3', '3 бота'], ['4', '4 бота']], String(bots))}
      <div class="game-stage rr-stage">
        <div class="rr-players" data-players></div>
        <div class="rr-drum-wrap"><div class="rr-drum" data-drum>${drumSvg()}</div></div>
        <div class="rr-status" data-status></div>
        <div class="rr-meter"><span>Шанс «баха»</span><div class="rr-bar"><i data-bar></i></div><b data-odds></b></div>
        <div class="rr-actions">
          <button class="game-btn ghost" data-spin>🌀 Крутануть</button>
          <button class="game-btn danger" data-pull>Нажать на курок</button>
        </div>
      </div>
      <div class="dk-foot">🏆 Побед: <b data-best>${best('roulette')}</b> · игра понарошку</div></div>`;
  const $ = (sel) => root.querySelector(sel);
  const stage = $('.rr-stage');
  const rotor = $('.rr-rotor');

  function setRotor(deg, ms, easing = 'cubic-bezier(.2, .9, .3, 1.15)') {
    angle = deg;
    rotor.style.transition = ms ? `transform ${ms}ms ${easing}` : 'none';
    rotor.style.transform = `rotate(${deg}deg)`;
  }

  /** Патрон в гнезде, которое выстрелит через bullet - shots нажатий. */
  function showBullet(visible) {
    root.querySelectorAll('.rr-bullet').forEach((el) => {
      el.setAttribute('opacity', visible && Number(el.dataset.ch) === bullet ? '1' : '0');
    });
  }

  function load6() {
    bullet = rand(CHAMBERS);
    shots = 0;
  }

  function render(status = '') {
    $('[data-players]').innerHTML = players.map((p, i) => `<div class="rr-player${p.out ? ' out' : ''}${i === turn && !over ? ' now' : ''}">
        <span class="rr-face">${p.out ? '💥' : p.face}</span><span>${p.name}</span></div>`).join('');
    if (status) $('[data-status]').textContent = status;
    const chance = over ? 0 : risk(shots);
    $('[data-bar]').style.width = `${Math.round(chance * 100)}%`;
    $('[data-bar]').style.background = chance > 0.4 ? '#ff4d4d' : chance > 0.2 ? '#ffb020' : '#4cd964';
    $('[data-odds]').textContent = over ? '—' : `1 из ${CHAMBERS - shots}`;
    const mine = !over && turn === 0 && !busy;
    $('[data-spin]').disabled = !mine || spun;
    $('[data-pull]').disabled = !mine;
    // Прячем, не убирая из вёрстки, — иначе всё ниже прыгало вверх.
    $('.rr-actions').classList.toggle('off', over || players[0].out);
  }

  const alive = () => players.filter((p) => !p.out);
  const nextTurn = () => {
    do turn = (turn + 1) % players.length; while (players[turn].out);
    spun = false;
  };

  async function pause(mine, ms) {
    await wait(ctx, ms);
    if (mine !== gen) throw new Error('новая игра');
  }

  /** Заряжаем: видно, в какое гнездо лёг патрон, потом барабан крутится. */
  async function reload(mine) {
    load6();
    showBullet(true);
    setRotor(angle - (angle % 360) - bullet * 60, 0);
    render('Барабан заряжен');
    await pause(mine, 700);
    await spinDrum(mine, true);
  }

  async function spinDrum(mine, quiet = false) {
    if (!quiet) tap('medium');
    sfx('spin');
    // Барабан крутится несколько оборотов и замирает с лёгким отскоком.
    setRotor(angle + 720 + rand(6) * 60, 1300, 'cubic-bezier(.15, .7, .2, 1.04)');
    await pause(mine, 350);
    showBullet(false);
    bullet = rand(CHAMBERS);
    shots = 0;
    await pause(mine, 1000);
  }

  async function pull(mine) {
    const p = players[turn];
    touched = true;
    render(`${p.name} ${verb(p, 'жмёте', 'жмёт')} на курок…`);
    stage.classList.add('aim');
    await pause(mine, 750);
    const bang = shots === bullet;
    shots += 1;
    setRotor(angle + 60, 220);
    if (bang) {
      p.out = true;
      sfx('bang');
      notify(turn === 0 ? 'error' : 'warning');
      quake(stage, 14);
      stage.classList.remove('aim');
      stage.classList.add('flash');
      setTimeout(() => stage.classList.remove('flash'), 450);
      render(`💥 Бах! ${p.name} ${verb(p, 'выбываете', 'выбывает')}`);
      await pause(mine, 1500);
      if (alive().length > 1) await reload(mine);
    } else {
      sfx('hammer');
      tap('light');
      stage.classList.remove('aim');
      render(`Щёлк! ${p.name} в игре`);
      await pause(mine, 800);
    }
    if (alive().length === 1) {
      over = true;
      const winner = alive()[0];
      const won = winner === players[0];
      if (won) saveBest('roulette', best('roulette') + 1);
      $('[data-best]').textContent = String(best('roulette'));
      const title = `${winner.fem ? 'Победила' : 'Победил'} ${winner.name}`;
      render(won ? 'Вы остались последним!' : title);
      overlay(stage, `<div class="game-over-title">${won ? 'Вы выжили!' : title}</div>
          <div class="game-over-text">${won ? 'Последний в игре — вы.' : 'В этот раз не повезло.'}</div>`,
      [{ label: 'Новая игра', run: newGame }], { icon: won ? '🏆' : winner.face, win: won, lose: !won });
      return;
    }
    nextTurn();
  }

  async function run(mine) {
    if (busy) return;
    busy = true;
    try {
      while (!over && players[turn].bot) {
        render(`${players[turn].name} думает…`);
        await pause(mine, 700 + rand(500));
        // Бот крутит барабан, когда шанс «баха» стал большим.
        if (!spun && shots >= 2 && Math.random() < 0.6) {
          spun = true;
          render(`${players[turn].name} крутит барабан`);
          await spinDrum(mine);
        }
        await pull(mine);
      }
    } catch {
      return;
    } finally {
      if (mine === gen) busy = false;
    }
    if (!over) render('Ваш ход: крутануть барабан или сразу курок');
  }

  async function newGame() {
    gen += 1;
    const mine = gen;
    busy = true;
    players = [{ name: 'Вы', face: '😎', bot: false, out: false },
      ...BOTS.slice(0, bots).map((b) => ({ ...b, bot: true, out: false }))];
    turn = rand(players.length);
    spun = false;
    over = false;
    touched = false;
    load6();
    render(' ');
    try {
      await reload(mine);
    } catch {
      return;
    }
    busy = false;
    render(turn === 0 ? 'Ваш ход: крутануть барабан или сразу курок' : '');
    run(mine);
  }

  $('[data-spin]').addEventListener('click', async () => {
    if (busy || over || turn !== 0 || spun) return;
    busy = true;
    spun = true;
    const mine = gen;
    render('Крутим…');
    try {
      await spinDrum(mine);
    } catch {
      return;
    } finally {
      if (mine === gen) busy = false;
    }
    render('Барабан раскручен — теперь курок');
  });
  $('[data-pull]').addEventListener('click', async () => {
    if (busy || over || turn !== 0) return;
    busy = true;
    const mine = gen;
    try {
      await pull(mine);
    } catch {
      return;
    } finally {
      if (mine === gen) busy = false;
    }
    if (!over) run(mine);
  });
  bindChips(root, 'bots', (value) => {
    bots = Number(value);
    save('roulette-bots', bots);
    stage.querySelector('.game-over')?.remove();
    newGame();
  }, { guard: () => over || !touched || askRestart(stage) });
  newGame();
}
