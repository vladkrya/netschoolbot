// Судоку: головоломка с единственным решением, три уровня сложности,
// заметки карандашом, подсказки и отмена хода. Заполненная строка, столбец
// или квадрат пробегает волной. Начатая партия сохраняется — её можно
// доиграть позже, даже без сети.
'use strict';

import {
  best, bindChips, chipsRow, clock, hud, load, notify, overlay, popText, save, saveBest, select, sfx, shake, shuffle, tap,
} from './kit.js';

const LEVELS = { easy: ['Лёгкий', 40], medium: ['Средний', 32], hard: ['Сложный', 26] };
const HINTS = 3;

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

/** Строка, столбец и квадрат клетки — списками клеток. */
function units(i) {
  const r = Math.floor(i / 9);
  const c = i % 9;
  const br = r - (r % 3);
  const bc = c - (c % 3);
  return [
    Array.from({ length: 9 }, (_, k) => r * 9 + k),
    Array.from({ length: 9 }, (_, k) => k * 9 + c),
    Array.from({ length: 9 }, (_, k) => (br + Math.floor(k / 3)) * 9 + bc + (k % 3)),
  ];
}

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
  let history = [];

  root.innerHTML = `<div class="game sudoku">
      ${chipsRow('level', Object.entries(LEVELS).map(([key, [label]]) => [key, label]), level)}
      ${hud([['time', '⏱', '0:00'], ['mistakes', 'Ошибки', 0], ['best', '🏆', '—']])}
      <div class="game-stage"><div class="sudoku-grid" data-grid>${Array.from({ length: 81 }, (_, i) => {
        const r = Math.floor(i / 9);
        const c = i % 9;
        const edge = `${c % 3 === 2 && c < 8 ? ' br' : ''}${r % 3 === 2 && r < 8 ? ' bb' : ''}`;
        return `<button class="sk${edge}" data-cell="${i}"></button>`;
      }).join('')}</div></div>
      <div class="sudoku-tools">
        <button class="tool" data-undo><span>↶</span>Отменить</button>
        <button class="tool" data-erase><span>⌫</span>Стереть</button>
        <button class="tool" data-notes><span>✏️</span>Заметки<em data-notes-state>выкл</em></button>
        <button class="tool" data-hint><span>💡</span>Подсказка<em data-hints>${HINTS}</em></button>
      </div>
      <div class="sudoku-pad">${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `<button data-num="${n}">${n}<small></small></button>`).join('')}</div>
      <button class="game-btn ghost small" data-new>Новая игра</button></div>`;
  const grid = root.querySelector('[data-grid]');
  const cells = [...grid.children];
  const stage = root.querySelector('.game-stage');

  const showBest = () => {
    const record = best(`sudoku-${level}`);
    root.querySelector('[data-best]').textContent = record ? clock(record) : '—';
  };

  function newGame() {
    stage.querySelector('.game-over')?.remove();
    grid.classList.add('loading');
    // Генерация занимает доли секунды — даём сначала показать затемнение.
    setTimeout(() => {
      const { puzzle, solution } = generate(LEVELS[level][1]);
      game = {
        level, puzzle, solution, values: [...puzzle], notes: Array(81).fill(0), mistakes: 0, seconds: 0, done: false, hints: HINTS,
      };
      history = [];
      save('sudoku-game', game);
      selected = -1;
      grid.classList.remove('loading');
      render();
      // Цифры условия появляются волной из центра.
      cells.forEach((el, i) => {
        if (!game.puzzle[i]) return;
        const d = Math.abs(Math.floor(i / 9) - 4) + Math.abs((i % 9) - 4);
        el.animate?.([{ opacity: 0, scale: '0.4' }, { opacity: 1, scale: '1' }], { duration: 300, delay: d * 30, easing: 'ease-out', fill: 'backwards' });
      });
    }, 40);
  }

  function render() {
    root.querySelector('[data-mistakes]').textContent = String(game.mistakes);
    root.querySelector('[data-time]').textContent = clock(game.seconds);
    root.querySelector('[data-hints]').textContent = String(game.hints ?? HINTS);
    root.querySelector('[data-notes-state]').textContent = notesMode ? 'вкл' : 'выкл';
    root.querySelector('[data-notes]').classList.toggle('on', notesMode);
    const value = selected >= 0 ? game.values[selected] : 0;
    const around = selected >= 0 ? new Set(PEERS[selected]) : new Set();
    cells.forEach((el, i) => {
      const n = game.values[i];
      el.classList.toggle('given', Boolean(game.puzzle[i]));
      el.classList.toggle('wrong', Boolean(n && n !== game.solution[i]));
      el.classList.toggle('sel', i === selected);
      el.classList.toggle('peer', around.has(i));
      el.classList.toggle('same', Boolean(value && n === value && i !== selected));
      const html = n ? String(n) : game.notes[i]
        ? `<span class="sk-notes">${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((k) => `<i>${game.notes[i] & (1 << k) ? k : ''}</i>`).join('')}</span>` : '';
      if (el.dataset.html !== html) {
        el.innerHTML = html;
        el.dataset.html = html;
      }
    });
    // Сколько каждой цифры ещё поставить; расставленная вся — бледнеет.
    root.querySelectorAll('[data-num]').forEach((button) => {
      const n = Number(button.dataset.num);
      const left = 9 - game.values.filter((v, i) => v === n && v === game.solution[i]).length;
      button.classList.toggle('used', left === 0);
      button.querySelector('small').textContent = left ? String(left) : '';
    });
  }

  function wave(list, origin) {
    list.forEach((j) => {
      const d = Math.abs(Math.floor(j / 9) - Math.floor(origin / 9)) + Math.abs((j % 9) - (origin % 9));
      cells[j].animate?.([{ background: 'rgba(255, 196, 0, .5)', scale: '1' }, { scale: '1.12', offset: 0.4 }, { scale: '1' }],
        { duration: 420, delay: d * 45, easing: 'ease-out' });
    });
  }

  function put(n, { hint = false } = {}) {
    if (selected < 0 || !game || game.done || game.puzzle[selected]) return;
    const i = selected;
    if (notesMode && n && !hint) {
      if (game.values[i]) return;
      history.push({ i, value: game.values[i], notes: game.notes[i] });
      game.notes[i] ^= 1 << n;
      select();
      sfx('tap');
    } else {
      if (game.values[i] === n) return;
      history.push({ i, value: game.values[i], notes: game.notes[i] });
      game.values[i] = n;
      game.notes[i] = 0;
      if (n && n !== game.solution[i]) {
        game.mistakes += 1;
        shake(cells[i]);
      } else if (n) {
        // Поставленная цифра убирает такие же заметки у соседей.
        PEERS[i].forEach((j) => { game.notes[j] &= ~(1 << n); });
        tap('light');
        sfx(hint ? 'good' : 'tap');
        cells[i].animate?.([{ scale: '0.5' }, { scale: '1.15' }, { scale: '1' }], { duration: 260, easing: 'ease-out' });
        const done = units(i).filter((unit) => unit.every((j) => game.values[j] === game.solution[j]));
        if (done.length) {
          sfx('good');
          wave([...new Set(done.flat())], i);
        }
      }
    }
    save('sudoku-game', game);
    render();
    if (game.values.every((v, k) => v === game.solution[k])) win();
  }

  function win() {
    game.done = true;
    save('sudoku-game', null);
    const record = saveBest(`sudoku-${level}`, game.seconds, true);
    showBest();
    wave([...Array(81).keys()], 40);
    setTimeout(() => {
      if (ctx.signal.aborted) return;
      overlay(stage, `<div class="game-over-title">${record ? 'Новый рекорд!' : 'Решено!'}</div>
          <div class="game-over-text">${LEVELS[level][0]} · <b>${clock(game.seconds)}</b> · ошибок: ${game.mistakes}</div>`,
      [{ label: 'Новая игра', run: newGame }], { icon: record ? '🏆' : '🎉', win: true });
    }, 900);
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
    sfx('tap');
    render();
  });
  root.querySelector('[data-undo]').addEventListener('click', () => {
    const last = history.pop();
    if (!last || game.done) return;
    game.values[last.i] = last.value;
    game.notes[last.i] = last.notes;
    selected = last.i;
    sfx('tap');
    tap();
    save('sudoku-game', game);
    render();
  });
  root.querySelector('[data-hint]').addEventListener('click', () => {
    if (!game || game.done) return;
    if (!game.hints) {
      shake(root.querySelector('[data-hint]'));
      return;
    }
    // Подсказка — в выбранную пустую клетку, иначе в ту, где меньше всего вариантов.
    let target = selected >= 0 && game.values[selected] !== game.solution[selected] && !game.puzzle[selected] ? selected : -1;
    if (target < 0) {
      const empty = game.values.map((v, i) => (v === game.solution[i] ? -1 : i)).filter((i) => i >= 0);
      target = empty.sort((a, b) => candidates(game.values, a).length - candidates(game.values, b).length)[0];
    }
    if (target === undefined || target < 0) return;
    game.hints -= 1;
    selected = target;
    const wasNotes = notesMode;
    notesMode = false;
    put(game.solution[target], { hint: true });
    notesMode = wasNotes;
    const rect = cells[target].getBoundingClientRect();
    const host = stage.getBoundingClientRect();
    popText(stage, '💡', { x: rect.left - host.left + rect.width / 2, y: rect.top - host.top });
  });
  root.querySelector('[data-new]').addEventListener('click', () => { tap(); sfx('tap'); newGame(); });
  bindChips(root, 'level', (value) => {
    level = value;
    save('sudoku-level', level);
    showBest();
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
  if (game && !game.done && game.level === level && game.values?.length === 81) {
    game.hints ??= HINTS;
    render();
  } else {
    newGame();
  }
}
