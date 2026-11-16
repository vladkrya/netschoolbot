import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/web/static/js/route-board.js', import.meta.url), 'utf8')
  .replace(/^import [^;]*;\n/gm, '').replace(/^export /gm, '');
const isoOf = (value) => [
  value.getFullYear(), String(value.getMonth() + 1).padStart(2, '0'), String(value.getDate()).padStart(2, '0'),
].join('-');
const esc = (value) => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const context = vm.createContext({ esc, isoOf });
vm.runInContext(`${source}\nglobalThis.api = { routeBoardModel, routeBoardHtml };`, context);
const { routeBoardModel, routeBoardHtml } = context.api;
const date = '2026-11-17';
const lesson = (subject, start, end, fields = {}) => ({ subject, start, end, ...fields });
const modelAt = (lessons, time, shift) => routeBoardModel(lessons, date, new Date(`${date}T${time}`), shift);
const statuses = (model) => Array.from(model.rows, ({ status }) => status);

test('route progress excludes cancelled lessons and prefers the current lesson', () => {
  const model = modelAt([
    lesson('История', '08:00', '08:40'),
    lesson('Химия', '08:45', '09:25', { moderation_cancelled: true }),
    lesson('Физика', '09:30', '10:10'),
    lesson('Биология', '10:20', '11:00'),
  ], '09:45:00');
  assert.deepEqual(statuses(model), ['finished', 'cancelled', 'current', 'upcoming']);
  assert.equal(model.total, 3);
  assert.equal(model.finished, 1);
  assert.equal(model.featured.index, 2);
});

test('the next stop follows the earliest known departure after edited or untimed lessons', () => {
  const model = modelAt([
    lesson('Без времени'),
    lesson('История', '11:30', '12:10'),
    lesson('Физика', '9:30', '10:10'),
    lesson('Опечатка', '09:61', '25:00'),
  ], '09:00:00');
  assert.equal(model.featured.index, 2);
  assert.equal(model.rows[3].start, null);
});

test('start and finish follow the bell shift at the exact boundary', () => {
  const entries = [lesson('Геометрия', '08:00', '08:40')];
  assert.equal(modelAt(entries, '08:00:04', 5).rows[0].status, 'upcoming');
  assert.equal(modelAt(entries, '08:00:05', 5).rows[0].status, 'current');
  const finished = modelAt(entries, '08:40:05', 5);
  assert.equal(finished.rows[0].status, 'finished');
  assert.equal(finished.featured, null);
});

test('history and future routes work even for lessons with no time', () => {
  const entries = [lesson('Геометрия')];
  const past = routeBoardModel(entries, '2026-11-16', new Date(`${date}T09:00:00`));
  const future = routeBoardModel(entries, '2026-11-18', new Date(`${date}T09:00:00`));
  assert.equal(past.finished, 1);
  assert.equal(past.featured, null);
  assert.equal(future.finished, 0);
  assert.equal(future.featured.index, 0);
});

test('homework excludes classwork and the board renders escaped clickable stops', () => {
  const model = modelAt([
    lesson('Классная работа', '08:00', '08:40', { homework: [{ classwork: true }] }),
    lesson('<Лаборатория>', '08:50', '09:30', { homework: [{ classwork: false }], place: '3а' }),
  ], '09:00:00');
  assert.equal(model.homework, 1);
  assert.equal(model.featured.number, '3а');
  const html = routeBoardHtml(model, '');
  assert.match(html, /&lt;Лаборатория>/);
  assert.doesNotMatch(html, /<Лаборатория>/);
  assert.match(html, /data-route-filter="homework"/);
  assert.match(html, /aria-current="step"/);
});

test('a fully cancelled route has no progress or featured jump', () => {
  const model = modelAt([lesson('Отмена', '08:00', '08:40', { moderation_cancelled: true })], '08:25:00');
  assert.equal(model.total, 0);
  assert.equal(model.featured, null);
  assert.doesNotMatch(routeBoardHtml(model, ''), /<progress|route-board__jump|data-route-filter/);
});
