// Судоку: головоломка с единственным решением, три уровня сложности,
// заметки карандашом. Начатая партия сохраняется — её можно доиграть позже.
'use strict';

import {
  best, bindChips, chipsRow, clock, load, notify, overlay, save, saveBest, select, shake, shuffle, tap,
} from './kit.js';

const LEVELS = { easy: ['Лёгкий', 40], medium: ['Средний', 32], hard: ['Сложный', 26] };

const peers = (i) => {
  const r = Math.floor(i / 9);
  const c = i % 9;
  const br = r - (r % 3);
  const bc = c - (c % 3);
  const list = [];
  for (let k = 0; k < 9; k += 1) {
    list.push(r * 9 + k, k * 9 + c, (br + Math.floor(k / 3)) * 9 + bc + (k % 3));
  }
  return list.filter((j) => j !== i);
};
const PEERS = Array.from({ length: 81 }, (_, i) => peers(i));

const candidates = (grid, i) => {
  const used = new Set(PEERS[i].map((j) => grid[j]));
  return [1, 2, 3, 4, 5, 6, 7, 8, 9].filter((n) => !used.has(n));
};

/** Число решений, но не больше limit. Перебор с клетки, где меньше всего вариантов. */
export function countSolutions(grid, limit = 2) {
  let bestCell = -1;
  let bestOptions = null;
  for (let i = 0; i < 81; i += 1) {
    if (grid[i]) continue;
    const options = candidates(grid, i);
    if (!options.length) return 0;
    if (!bestOptions || options.length < bestOptions.length) {
      bestCell = i;
      bestOptions = options;
      if (options.length === 1) break;
    }
  }
  if (bestCell < 0) return 1;
  let count = 0;
  for (const n of bestOptions) {
    grid[bestCell] = n;
    count += countSolutions(grid, limit - count);
    if (count >= limit) break;
  }
  grid[bestCell] = 0;
  return count;
}

function fill(grid) {
  const i = grid.indexOf(0);
  if (i < 0) return true;
  for (const n of shuffle(candidates(grid, i))) {
    grid[i] = n;
    if (fill(grid)) return true;
  }
  grid[i] = 0;
  return false;
}

/** Новая головоломка: { puzzle, solution } — массивы из 81 числа, 0 — пусто. */
export function generate(clues) {
  const solution = Array(81).fill(0);
  fill(solution);
  const puzzle = [...solution];
  let filled = 81;
  for (const i of shuffle([...Array(81).keys()])) {
    if (filled <= clues) break;
    const keep = puzzle[i];
    puzzle[i] = 0;
    if (countSolutions([...puzzle]) !== 1) puzzle[i] = keep;
    else filled -= 1;
  }
  return { puzzle, solution };
}

export function start(root, ctx) {
  let level = load('sudoku-level', 'easy');
  let game = load('sudoku-game');
  let selected = -1;
  let notesMode = false;
  let ticker = 0;

  root.innerHTML = `<div class="game sudoku">
      ${chipsRow('level', Object.entries(LEVELS).map(([key, [label]]) => [key, label]), level)}
      <div class="game-stats"><span>Время: <b data-time>0:00</b></span><span>Ошибки: <b data-mistakes>0</b></span><span>Рекорд: <b data-best></b></span></div>
      <div class="game-stage"><div class="sudoku-grid" data-grid></div></div>
      <div class="sudoku-pad">${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `<button data-num="${n}">${n}</button>`).join('')}</div>
      <div class="sudoku-tools">
        <button class="chip" data-notes>✏️ Заметки</button>
        <button class="chip" data-erase>⌫ Стереть</button>
        <button class="chip" data-new>Новая игра</button>
      </div></div>`;
  const grid = root.querySelector('[data-grid]');
  const stage = root.querySelector('.game-stage');

  const showBest = () => {
    const record = best(`sudoku-${level}`);
    root.querySelector('[data-best]').textContent = record ? clock(record) : '—';
  };

  function newGame() {
    grid.innerHTML = '<div class="muted small sudoku-wait">Составляю головоломку…</div>';
    // Генерация занимает доли секунды — даём сначала показать надпись.
    setTimeout(() => {
      const { puzzle, solution } = generate(LEVELS[level][1]);
      game = { level, puzzle, solution, values: [...puzzle], notes: Array(81).fill(0), mistakes: 0, seconds: 0, done: false };
      save('sudoku-game', game);
      selected = -1;
      render();
    }, 30);
  }

  function render() {
    root.querySelector('[data-mistakes]').textContent = String(game.mistakes);
    root.querySelector('[data-time]').textContent = clock(game.seconds);
    const value = selected >= 0 ? game.values[selected] : 0;
    grid.innerHTML = game.values.map((n, i) => {
      const classes = ['sk'];
      if (game.puzzle[i]) classes.push('given');
      else if (n && n !== game.solution[i]) classes.push('wrong');
      if (i === selected) classes.push('sel');
      else if (selected >= 0 && PEERS[selected].includes(i)) classes.push('peer');
      if (value && n === value && i !== selected) classes.push('same');
      const r = Math.floor(i / 9);
      const c = i % 9;
      if (c % 3 === 2 && c < 8) classes.push('br');
      if (r % 3 === 2 && r < 8) classes.push('bb');
      const notes = !n && game.notes[i]
        ? `<span class="sk-notes">${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((k) => `<i>${game.notes[i] & (1 << k) ? k : ''}</i>`).join('')}</span>` : '';
      return `<button class="${classes.join(' ')}" data-cell="${i}">${n || notes}</button>`;
    }).join('');
    // Цифра, которую уже расставили все девять раз, бледнеет.
    root.querySelectorAll('[data-num]').forEach((button) => {
      const n = Number(button.dataset.num);
      button.classList.toggle('used', game.values.filter((v, i) => v === n && v === game.solution[i]).length === 9);
    });
    root.querySelector('[data-notes]').classList.toggle('active', notesMode);
  }

  function put(n) {
    if (selected < 0 || game.done || game.puzzle[selected]) return;
    if (notesMode && n) {
      if (game.values[selected]) return;
      game.notes[selected] ^= 1 << n;
      select();
    } else {
      game.values[selected] = n;
      game.notes[selected] = 0;
      if (n && n !== game.solution[selected]) {
        game.mistakes += 1;
        notify('error');
      } else if (n) {
        // Поставленная цифра убирает такие же заметки у соседей.
        PEERS[selected].forEach((j) => { game.notes[j] &= ~(1 << n); });
        tap('light');
      }
    }
    save('sudoku-game', game);
    render();
    if (game.values.every((v, i) => v === game.solution[i])) win();
  }

  function win() {
    game.done = true;
    save('sudoku-game', null);
    notify('success');
    const record = saveBest(`sudoku-${level}`, game.seconds, true);
    showBest();
    overlay(stage, `<div class="game-over-title">${record ? 'Новый рекорд!' : 'Решено!'}</div>
        <div class="muted">${LEVELS[level][0]} · ${clock(game.seconds)} · ошибок: ${game.mistakes}</div>`,
    [{ label: 'Новая игра', run: newGame }]);
  }

  grid.addEventListener('click', (event) => {
    const cellEl = event.target.closest('[data-cell]');
    if (!cellEl || !game) return;
    selected = Number(cellEl.dataset.cell);
    select();
    render();
  });
  root.querySelector('.sudoku-pad').addEventListener('click', (event) => {
    const button = event.target.closest('[data-num]');
    if (!button) return;
    if (selected < 0 || game?.puzzle[selected]) {
      shake(grid);
      return;
    }
    put(Number(button.dataset.num));
  });
  root.querySelector('[data-erase]').addEventListener('click', () => put(0));
  root.querySelector('[data-notes]').addEventListener('click', () => {
    notesMode = !notesMode;
    select();
    render();
  });
  root.querySelector('[data-new]').addEventListener('click', () => { tap(); newGame(); });
  bindChips(root, 'level', (value) => {
    level = value;
    save('sudoku-level', level);
    showBest();
    root.querySelector('.game-over')?.remove();
    newGame();
  });
  document.addEventListener('keydown', (event) => {
    if (/^Digit[1-9]$/.test(event.code)) put(Number(event.code.slice(5)));
    else if (event.code === 'Backspace' || event.code === 'Delete') put(0);
  }, { signal: ctx.signal });

  ticker = setInterval(() => {
    if (!game || game.done || document.hidden) return;
    game.seconds += 1;
    root.querySelector('[data-time]').textContent = clock(game.seconds);
    if (game.seconds % 5 === 0) save('sudoku-game', game);
  }, 1000);
  ctx.signal.addEventListener('abort', () => {
    clearInterval(ticker);
    if (game && !game.done) save('sudoku-game', game);
  });

  showBest();
  if (game && !game.done && game.level === level && game.values?.length === 81) render();
  else newGame();
}
