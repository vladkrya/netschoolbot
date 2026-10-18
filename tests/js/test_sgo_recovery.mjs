import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = (name) => readFileSync(new URL(`../../app/web/static/js/${name}.js`, import.meta.url), 'utf8')
  .replace(/^import .*;\n/gm, '').replace(/^export /gm, '');

function setup() {
  const elements = new Map();
  const storage = new Map();
  let removed = 0;
  let calls = [];
  let reply = { ok: true, data: {}, stale: false };
  let offline = false;
  const document = new EventTarget();
  document.getElementById = (id) => elements.get(id) || null;
  document.createElement = () => { throw new Error('Old outage must not create a new card'); };
  elements.set('banner', { dataset: { dismiss: '1' }, style: {}, classList: { toggle() {} } });
  elements.set('sgoDown', { dataset: {}, remove() { removed += 1; elements.delete('sgoDown'); } });
  const globals = {
    document, Event, CustomEvent: class extends Event {}, AbortController,
    window: { NETSCHOOL: { base: '', token: '', demo: false }, matchMedia: () => ({ matches: false }) },
    navigator: {}, sessionStorage: {
      getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
    setTimeout: () => 1, clearTimeout() {},
    fetch: async (path) => {
      calls.push(path);
      return { ok: true, headers: { get: (name) => name === 'X-Netschool-Offline' && offline ? '1' : null }, json: async () => reply };
    },
  };
  const context = vm.createContext({ ...globals, ICONS: {} });
  const core = vm.runInContext(`${source('core')}\n({ api, markSgoDown, markSgoUp, sgoDown });`, context);
  return { core, globals, storage, elements, calls, removed: () => removed,
    response(value, cachedOffline = false) { reply = value; offline = cachedOffline; } };
}

test('only a fresh school response clears the outage, not profile/cache/offline', async () => {
  const h = setup();
  h.elements.get('sgoDown').dataset.slow = '1';
  h.core.markSgoDown();
  await h.core.api('/api/profile');
  assert.equal(h.removed(), 0);
  assert.equal(h.core.sgoDown(), true);
  h.response({ ok: true, data: {}, stale: false, age: 10 });
  await h.core.api('/api/diary');
  assert.equal(h.removed(), 0);
  h.response({ ok: true, data: {}, stale: false }, true);
  await h.core.api('/api/diary');
  assert.equal(h.removed(), 0);
  h.response({ ok: true, data: {}, stale: false });
  await h.core.api('/api/diary');
  assert.equal(h.removed(), 1);
  assert.equal(h.core.sgoDown(), false);
});

test('manual refresh is sent to the school immediately after an outage', async () => {
  const h = setup();
  h.core.markSgoDown();
  await h.core.api('/api/diary?date=2026-10-18&refresh=1');
  assert.equal(h.calls[0], '/api/diary?date=2026-10-18&refresh=1');
  assert.equal(h.core.sgoDown(), false);
});

test('a delayed status failure cannot restore a card after the diary recovered', async () => {
  const h = setup();
  let resolve;
  const pending = new Promise((done) => { resolve = done; });
  const context = vm.createContext({ ...h.globals,
    $: (id) => h.elements.get(id), api: () => pending, demo: false,
    markSgoDown: h.core.markSgoDown,
  });
  const sgo = vm.runInContext(`${source('sgo')}\n({ checkServer });`, context);
  h.elements.delete('sgoDown');
  h.storage.set('ns-sgo-down-dismissed', '1');
  h.core.markSgoUp();
  assert.equal(h.storage.has('ns-sgo-down-dismissed'), false);
  const checking = sgo.checkServer({ failed: true });
  h.core.markSgoUp();
  resolve({ data: { state: 'down', ok: false } });
  await checking;
  assert.equal(h.elements.has('sgoDown'), false);
  assert.equal(h.core.sgoDown(), false);
});

function liveSetup() {
  const timers = new Map();
  const sockets = [];
  const document = new EventTarget();
  document.hidden = false;
  const window = new EventTarget();
  let nextTimer = 0;
  class Socket {
    constructor(url) { this.url = String(url); sockets.push(this); }
    close() { this.onclose?.(); }
  }
  const context = vm.createContext({ document, window, URL,
    location: { origin: 'https://example.test', protocol: 'https:' }, WebSocket: Socket,
    setTimeout(fn) { timers.set(++nextTimer, fn); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
  });
  const text = readFileSync(new URL('../../app/web/static/status/live.js', import.meta.url), 'utf8').replace(/^export /gm, '');
  const watchStatus = vm.runInContext(`${text}\nwatchStatus;`, context);
  return { watchStatus, document, window, sockets, timers,
    flush() { const todo = [...timers.values()]; timers.clear(); todo.forEach((fn) => fn()); } };
}

test('status socket coalesces a burst without starving updates and uses wss', () => {
  const h = liveSetup();
  let updates = 0;
  const stop = h.watchStatus('/mini', () => { updates += 1; });
  assert.equal(h.sockets[0].url, 'wss://example.test/mini/status/ws');
  for (let i = 0; i < 100; i += 1) h.sockets[0].onmessage({ data: '{"t":"status"}' });
  assert.equal(h.timers.size, 1);
  h.flush();
  assert.equal(updates, 1);
  h.sockets[0].onmessage({ data: '{"t":"status"}' });
  h.flush();
  assert.equal(updates, 2);
  stop();
  assert.equal(h.timers.size, 0);
});

test('status socket reconnects, pauses when hidden and stops cleanly', () => {
  const h = liveSetup();
  const stop = h.watchStatus('', () => {});
  h.sockets[0].close();
  h.flush();
  assert.equal(h.sockets.length, 2);
  h.document.hidden = true;
  h.document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(h.timers.size, 0);
  h.document.hidden = false;
  h.document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(h.sockets.length, 3);
  stop();
  h.window.dispatchEvent(new Event('online'));
  assert.equal(h.sockets.length, 3);
  assert.equal(h.timers.size, 0);
});
