import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// grading.js тянет core.js и prefs.js — берём сам модуль и подставляем средний.
const source = readFileSync(new URL('../../app/web/static/js/grading.js', import.meta.url), 'utf8')
  .replace(/^import [^;]*;\n/gm, '').replace(/^export /gm, '');
const context = vm.createContext({
  DEFAULT_THRESHOLDS: { 5: 4.6, 4: 3.6, 3: 2.6 },
  getPref: () => null,
  weightedAverage(marks) {
    let total = 0;
    let weights = 0;
    for (const m of marks) {
      const value = Number(m.mark);
      if (!(value >= 1 && value <= 5)) continue;
      const weight = Number(m.weight) > 0 ? Number(m.weight) : 1;
      total += value * weight;
      weights += weight;
    }
    return weights ? total / weights : null;
  },
});
vm.runInContext(`${source}\nglobalThis.api = { typicalWeight, neededMarks };`, context);
const { typicalWeight, neededMarks } = context.api;

const marks = (...pairs) => pairs.map(([mark, weight]) => ({ mark, weight }));

test('typical weight is the most common one', () => {
  assert.equal(typicalWeight(marks([4, 20], [4, 20], [5, 1])), 20);
  assert.equal(typicalWeight(marks([5, 1], [4, 1])), 1);
  assert.equal(typicalWeight([]), 1);
});

test('equal weights tie goes to the larger one', () => {
  assert.equal(typicalWeight(marks([4, 10], [5, 70])), 70);
});

test('goal count follows the real weight, not weight 1', () => {
  const journal = marks(...Array.from({ length: 8 }, () => [4, 20]), [4, 1]);
  const withOne = neededMarks(journal, 4.6, 5, 1);
  const withReal = neededMarks(journal, 4.6, 5, typicalWeight(journal));
  assert.ok(withOne === null || withOne > 50, `with weight 1 it takes ${withOne}`);
  assert.ok(withReal < 20, `with the real weight it takes ${withReal}`);
});
