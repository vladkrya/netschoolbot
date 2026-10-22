// Вкладка «Игры»: пасьянс, змейка, тетрис, судоку, дурак, Paper.io, слоты,
// рулетка, русская рулетка, блэкджек и покер.
//
// Всё работает без сети: соперники — боты, рекорды и фишки хранятся на
// устройстве. Каждая игра — свой модуль в js/games/, открывается экраном
// поверх вкладки на весь экран: нижняя панель на время игры прячется.
// Модули не в списке предзагрузки страницы (только js/*.js), поэтому
// warmGames качает их в фоне — service worker кладёт их в кэш, и игра
// откроется даже в метро.
'use strict';

import { $, esc } from './core.js';
import { markScreen, pushScreen } from './screens.js';
import { tap } from './haptics.js';
import { best, chips, clock, isMuted, setMuted, sfx } from './games/kit.js';

export const GAMES = [
  { id: 'solitaire', name: 'Пасьянс', emoji: '🃏', note: 'Косынка, три уровня', best: (v) => `🏆 ${clock(v)}` },
  { id: 'durak', name: 'Дурак', emoji: '♠️', note: 'Подкидной, 1–3 бота, три уровня', best: (v) => `🏆 побед: ${v}` },
  { id: 'snake', name: 'Змейка', emoji: '🐍', note: 'Одна или против ботов', best: (v) => `🏆 ${v}` },
  { id: 'tetris', name: 'Тетрис', emoji: '🧱', note: 'Собирай линии', best: (v) => `🏆 ${v}` },
  { id: 'paperio', name: 'Paper.io', emoji: '🟧', note: 'Захвати поле у ботов', best: (v) => `🏆 ${v}%` },
  { id: 'sudoku', name: 'Судоку', emoji: '🔢', note: 'Три уровня сложности', best: (v) => `🏆 ${clock(v)}` },
  { id: 'blackjack', name: 'Блэкджек', emoji: '♥️', note: 'Против дилера-бота', chips: true },
  { id: 'poker', name: 'Покер', emoji: '♣️', note: 'Холдем против ботов', chips: true },
  { id: 'slots', name: 'Слоты', emoji: '🎰', note: 'Крути барабаны', chips: true },
  { id: 'casino', name: 'Рулетка', emoji: '🎡', note: 'Красное, чёрное или число', chips: true },
  { id: 'roulette', name: 'Русская рулетка', emoji: '🔫', note: 'Кто останется последним', best: (v) => `🏆 побед: ${v}` },
];

export function loadGames() {
  const root = $('gamesContent');
  root.innerHTML = `<div class="games-hero">
      <div><div class="games-title">Игры</div><div class="games-sub">Без интернета · соперники — боты</div></div>
      <div class="games-chips" title="Игровые фишки">🪙 ${chips()}</div></div>
    ${GAMES.map((game, i) => {
    const record = best(game.id);
    const line = game.chips ? `🪙 ${chips()}` : record ? game.best(record) : game.note;
    return `<button class="game-tile" data-game="${game.id}" style="--d:${i * 35}ms">
        <span class="game-art" aria-hidden="true"><span>${game.emoji}</span></span>
        <span class="game-name">${esc(game.name)}</span>
        <span class="game-note">${esc(line)}</span></button>`;
  }).join('')}
    <div class="games-foot">Фишки игровые — их нельзя купить или вывести.</div>`;
  root.onclick = (event) => {
    const tile = event.target.closest('[data-game]');
    if (tile) openGame(tile.dataset.game);
  };
}

const load = (id) => import(`./games/${id}.js`);

function soundButton() {
  const button = document.createElement('button');
  button.className = 'game-sound';
  const paint = () => {
    button.textContent = isMuted() ? '🔇' : '🔊';
    button.setAttribute('aria-label', isMuted() ? 'Включить звук' : 'Выключить звук');
  };
  paint();
  button.addEventListener('click', () => {
    setMuted(!isMuted());
    paint();
    tap();
    sfx('tap');
  });
  return button;
}

export async function openGame(id) {
  const game = GAMES.find((item) => item.id === id);
  if (!game) return;
  tap();
  const body = pushScreen(game.name);
  markScreen(body, 'game', [id]);
  const screen = body.closest('.screen');
  screen.classList.add('game-screen');
  screen.dataset.game = id;
  screen.querySelector('.screen-head').append(soundButton());
  body.classList.add('game-body');
  // Свайпы в игре — её управление, а не жест «назад».
  body.dataset.ownGesture = '';
  // Игра на весь экран: нижняя панель и размытие под ней прячутся.
  document.documentElement.classList.add('in-game');
  // Игра живёт, пока её экран в документе: закрыли — останавливаем
  // таймеры и снимаем обработчики клавиатуры.
  const controller = new AbortController();
  const watch = setInterval(() => {
    if (!body.isConnected) {
      controller.abort();
      clearInterval(watch);
    }
  }, 250);
  controller.signal.addEventListener('abort', () => {
    if (!document.querySelector('.game-screen')) document.documentElement.classList.remove('in-game');
    // Рекорд или фишки могли измениться — плитка на вкладке покажет новое.
    if ($('gamesContent').childElementCount) loadGames();
  });
  try {
    const module = await load(id);
    if (!controller.signal.aborted) module.start(body, { signal: controller.signal, id });
  } catch (error) {
    if (!navigator.onLine || /import|fetch|module/i.test(String(error?.message))) {
      body.innerHTML = `<div class="empty big">Игра ещё не скачана на это устройство.<br>
        Откройте её один раз с интернетом — потом она будет работать и без него.</div>`;
    } else {
      body.innerHTML = '<div class="empty big">Игра не запустилась. Попробуйте открыть её ещё раз.</div>';
      setTimeout(() => { throw error; });
    }
  }
}

/** Скачать все игры заранее, в фоне: worker сохранит их для работы без сети. */
export function warmGames() {
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 200));
  idle(() => GAMES.forEach((game) => load(game.id).catch(() => {})));
}
