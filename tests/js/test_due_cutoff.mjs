import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// due.js тянет DOM и экраны — берём сам модуль и подставляем зависимости.
const source = readFileSync(new URL('../../app/web/static/js/due.js', import.meta.url), 'utf8')
  .replace(/^import [^;]*;\n/gm, '').replace(/^export /gm, '');

function load({ pref = '', since = '', today = '2026-10-28' }) {
  const context = vm.createContext({
    state: { hwDoneSince: since }, getPref: () => pref, setPref: () => {},
    addDays: (iso, n) => { const d = new Date(`${iso}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); },
    isoToday: () => today, DUTY_MARK: 'т', markValue: (v) => Number(v), isAutoDone: () => false,
    isDoneItem: (item) => context.state.hwDone.has(item.key),
  });
  vm.runInContext(`${source}\nglobalThis.api = { dueCutoff, sections };`, context);
  const { dueCutoff, sections } = context.api;
  // Отметки лежат в state.hwDone, как в приложении.
  return { dueCutoff, sections: (m, p, done) => { context.state.hwDone = done; return sections(m, p, done); } };
}

const past = (...items) => ({ items });
const marks = { subjects: [], current_quarter: 'q1' };
const hw = (date, key) => ({ date, key, subject: 'Физика', classwork: false });
// Объекты из другого контекста: сравниваем через JSON.
const keys = (found) => JSON.parse(JSON.stringify(found.homework.map((item) => item.key)));

test('without any mark nothing is a debt', () => {
  const { sections } = load({});
  assert.deepEqual(keys(sections(marks, past(hw('2026-10-20', 'a')), new Set())), []);
});

test('only tasks from the first marking day count', () => {
  const { sections, dueCutoff } = load({ since: '2026-10-26' });
  assert.equal(dueCutoff(), '2026-10-26');
  const found = sections(marks, past(hw('2026-10-20', 'old'), hw('2026-10-26', 'new'), hw('2026-10-27', 'newer'), hw('2026-10-27', 'done')), new Set(['done']));
  assert.deepEqual(keys(found), ['newer', 'new']);
});

test('clear button date wins when it is later', () => {
  const { dueCutoff } = load({ pref: '2026-10-28', since: '2026-10-20' });
  assert.equal(dueCutoff(), '2026-10-28');
});

test('empty cutoff when nothing set', () => {
  assert.equal(load({}).dueCutoff(), '');
});
