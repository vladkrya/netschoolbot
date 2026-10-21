import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// edits.js тянет core.js с окном браузера — берём только сам модуль.
const source = readFileSync(new URL('../../app/web/static/js/edits.js', import.meta.url), 'utf8')
  .replace(/^import [^;]*;\n/gm, '').replace(/^export /gm, '');
const context = vm.createContext({
  weekdayIndex: (iso) => (new Date(`${iso}T12:00:00Z`).getUTCDay() + 6) % 7,
  getPref: () => [],
});
vm.runInContext(`${source}\nglobalThis.applyEdits = applyEdits;`, context);
// Объекты из другого контекста: сравниваем через JSON.
const applyEdits = (...args) => JSON.parse(JSON.stringify(context.applyEdits(...args)));

const day = (start, end) => ({
  date: '2026-10-21',
  lessons: [{ number: 3, subject: 'Труд', start, end, time: `${start}–${end}` }],
});

test('time edit equal to the diary time is not marked as own time', () => {
  const edit = { id: 'a', kind: 'time', subject: 'Труд', number: 3, date: '2026-10-21', start: '08:30', end: '09:10' };
  const [lesson] = applyEdits(day('8:30', '9:10'), [edit]).lessons;
  assert.equal(lesson.editedBy, undefined);
  assert.equal(lesson.time, '8:30–9:10');
});

test('real time edit is still shown as own time', () => {
  const edit = { id: 'a', kind: 'time', subject: 'Труд', number: 3, date: '2026-10-21', start: '10:00', end: '10:40' };
  const [lesson] = applyEdits(day('8:30', '9:10'), [edit]).lessons;
  assert.deepEqual(lesson.editedBy, edit);
  assert.equal(lesson.time, '10:00–10:40');
  assert.equal(lesson.schoolTime, '8:30–9:10');
});

test('one-day edit back to the diary time overrides the weekly one', () => {
  const weekly = { id: 'w', kind: 'time', subject: 'Труд', number: 3, weekday: 2, start: '10:00', end: '10:40' };
  const daily = { id: 'd', kind: 'time', subject: 'Труд', number: 3, date: '2026-10-21', start: '08:30', end: '09:10' };
  const [lesson] = applyEdits(day('08:30', '09:10'), [weekly, daily]).lessons;
  assert.equal(lesson.editedBy, undefined);
  assert.equal(lesson.start, '08:30');
});
