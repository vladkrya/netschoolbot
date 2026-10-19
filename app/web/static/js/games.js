// Вкладка «Игры»: пасьянс, змейка, тетрис, судоку, дурак, Paper.io, слоты,
// русская рулетка и блэкджек.
//
// Всё работает без сети: соперники — боты, рекорды и фишки хранятся на
// устройстве. Каждая игра — свой модуль в js/games/, открывается экраном
// поверх вкладки. Модули не в списке предзагрузки страницы (только js/*.js),
// поэтому warmGames качает их в фоне — service worker кладёт их в кэш, и
// игра откроется даже в метро.
'use strict';

import { $, esc } from './core.js';
import { pushScreen } from './screens.js';
import { tap } from './haptics.js';
import { best, chips, clock } from './games/kit.js';

export const GAMES = [
  { id: 'solitaire', name: 'Пасьянс', emoji: '🃏', note: 'Косынка', best: (v) => `рекорд ${clock(v)}` },
  { id: 'snake', name: 'Змейка', emoji: '🐍', note: 'Одна или против ботов', best: (v) => `рекорд ${v}` },
  { id: 'tetris', name: 'Тетрис', emoji: '🧱', note: 'Собирай линии', best: (v) => `рекорд ${v}` },
  { id: 'sudoku', name: 'Судоку', emoji: '🔢', note: 'Три уровня сложности', best: (v) => `рекорд ${clock(v)}` },
  { id: 'durak', name: 'Дурак', emoji: '🂡', note: 'Подкидной, 1–3 бота', best: (v) => `побед ${v}` },
  { id: 'paperio', name: 'Paper.io', emoji: '🟧', note: 'Захвати поле у ботов', best: (v) => `рекорд ${v}%` },
  { id: 'slots', name: 'Слоты', emoji: '🎰', note: 'На игровые фишки', chips: true },
  { id: 'roulette', name: 'Русская рулетка', emoji: '🔫', note: 'Кто останется последним', best: (v) => `побед ${v}` },
  { id: 'blackjack', name: 'Блэкджек', emoji: '♠️', note: 'Против дилера-бота', chips: true },
];

export function loadGames() {
  const root = $('gamesContent');
  root.innerHTML = `${GAMES.map((game) => {
    const record = best(game.id);
    const line = game.chips ? `${chips()} фишек` : record ? game.best(record) : game.note;
    return `<button class="game-tile" data-game="${game.id}">
        <span class="game-emoji" aria-hidden="true">${game.emoji}</span>
        <span class="game-name">${esc(game.name)}</span>
        <span class="game-note">${esc(line)}</span></button>`;
  }).join('')}
    <div class="games-foot muted small">Играются без интернета. Фишки — игровые, их нельзя купить или вывести.</div>`;
  root.onclick = (event) => {
    const tile = event.target.closest('[data-game]');
    if (tile) openGame(tile.dataset.game);
  };
}

const load = (id) => import(`./games/${id}.js`);

export async function openGame(id) {
  const game = GAMES.find((item) => item.id === id);
  if (!game) return;
  tap();
  const body = pushScreen(game.name);
  body.classList.add('game-body');
  // Свайпы в игре — её управление, а не жест «назад».
  body.dataset.ownGesture = '';
  // Игра живёт, пока её экран в документе: закрыли — останавливаем
  // таймеры и снимаем обработчики клавиатуры.
  const controller = new AbortController();
  const watch = setInterval(() => {
    if (!body.isConnected) {
      controller.abort();
      clearInterval(watch);
    }
  }, 300);
  try {
    const module = await load(id);
    if (!controller.signal.aborted) module.start(body, { signal: controller.signal, id });
  } catch {
    body.innerHTML = `<div class="empty big">Игра ещё не скачана на это устройство.<br>
      Откройте её один раз с интернетом — потом она будет работать и без него.</div>`;
  } finally {
    // Рекорд или фишки могли измениться — плитка на вкладке покажет новое.
    controller.signal.addEventListener('abort', () => { if ($('gamesContent').childElementCount) loadGames(); });
  }
}

/** Скачать все игры заранее, в фоне: worker сохранит их для работы без сети. */
export function warmGames() {
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 200));
  idle(() => GAMES.forEach((game) => load(game.id).catch(() => {})));
}
