import assert from 'node:assert/strict';
import test from 'node:test';

import { batFlight, swoopFlight } from '../../app/web/static/js/bats.js';

/** Предсказуемый «случай»: значения по кругу. */
function sequence(...values) {
  let i = 0;
  return () => values[i++ % values.length];
}

const xOf = (frame) => Number(/translate\((-?\d+)px/.exec(frame.transform)[1]);

test('bats fly both ways and in different kinds of routes', () => {
  const kinds = new Set();
  const sides = new Set();
  for (let i = 0; i < 400; i += 1) {
    const flight = batFlight(390);
    kinds.add(flight.kind);
    sides.add(flight.rtl);
    const offsets = flight.frames.map((frame) => frame.offset);
    assert.equal(offsets[0], 0);
    assert.equal(offsets.at(-1), 1);
    offsets.slice(1).forEach((offset, j) => assert.ok(offset >= offsets[j], `offsets go forward: ${offsets}`));
    assert.ok(flight.duration > 2000 && flight.duration < 20000, `duration ${flight.duration}`);
  }
  assert.deepEqual([...kinds].sort(), ['dip', 'loop', 'turn', 'wave', 'zigzag']);
  assert.deepEqual([...sides].sort(), [false, true]);
});

test('a bat starts and ends off screen; a turning bat leaves where it came from', () => {
  const right = batFlight(390, sequence(0.1, 0.9, 0.5));
  assert.equal(right.kind, 'wave');
  assert.equal(xOf(right.frames[0]), -60);
  assert.equal(xOf(right.frames.at(-1)), 450);
  const left = batFlight(390, sequence(0.1, 0.2, 0.5));
  assert.equal(xOf(left.frames[0]), 450);
  assert.equal(xOf(left.frames.at(-1)), -60);
  const turn = batFlight(390, sequence(0.95, 0.9, 0.5));
  assert.equal(turn.kind, 'turn');
  assert.equal(xOf(turn.frames[0]), -60);
  assert.equal(xOf(turn.frames.at(-1)), -60);
});

test('the big bat takes different routes', () => {
  const routes = new Set();
  for (const value of [0, 0.25, 0.45, 0.65, 0.9]) routes.add(swoopFlight(() => value)[0].transform);
  assert.equal(routes.size, 5);
});
