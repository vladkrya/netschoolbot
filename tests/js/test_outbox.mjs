import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/web/static/js/outbox.js', import.meta.url), 'utf8')
  .replace(/^import .*;\n/gm, '').replace(/^export /gm, '');

const unreachable = () => Object.assign(new Error('Нет подключения к интернету'), { source: 'app', reason: 'server' });

function setup(saved = null) {
  const storage = new Map(saved ? [['ns-outbox', JSON.stringify(saved)]] : []);
  const sent = [];
  const state = { offline: false, reply: (path, body) => ({ ok: true, data: body }) };
  const document = new EventTarget();
  const context = vm.createContext({
    document, Event, CustomEvent: class extends Event { constructor(type, init) { super(type); this.detail = init?.detail; } },
    localStorage: {
      getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
    setTimeout: () => 0, setInterval: () => 0,
    demo: false,
    banner() {},
    isOffline: () => state.offline,
    api: async (path, options) => {
      if (state.offline) throw unreachable();
      const body = JSON.parse(options.body);
      sent.push([path, body]);
      const reply = state.reply(path, body);
      if (reply instanceof Error) throw reply;
      return reply;
    },
  });
  const outbox = vm.runInContext(`${source}\n({ send, flush, pendingOp, pendingOps, realId, dequeue });`, context);
  const queued = () => JSON.parse(storage.get('ns-outbox') || '[]');
  return { outbox, state, sent, queued, document };
}

test('offline changes wait on the device, the latest one per item', async () => {
  const h = setup();
  h.state.offline = true;
  assert.equal((await h.outbox.send('hw:a', '/api/homework/done', { key: 'a', done: true })).queued, true);
  await h.outbox.send('hw:b', '/api/homework/done', { key: 'b', done: true });
  await h.outbox.send('hw:a', '/api/homework/done', { key: 'a', done: false });
  assert.deepEqual(h.queued().map((op) => [op.id, op.body.done]), [['hw:b', true], ['hw:a', false]]);
  assert.equal(h.sent.length, 0);
});

test('the queue survives a restart and is sent in order once online', async () => {
  const first = setup();
  first.state.offline = true;
  await first.outbox.send('hw:a', '/api/homework/done', { key: 'a', done: true });
  await first.outbox.send('prefs', '/api/prefs', { theme: 'dark' });
  const h = setup(first.queued());
  await h.outbox.flush();
  assert.deepEqual(h.sent.map(([path]) => path), ['/api/homework/done', '/api/prefs']);
  assert.deepEqual(h.queued(), []);
});

test('a refused change is dropped, an unreachable server keeps the queue', async () => {
  const h = setup([
    { id: 'note:5', path: '/api/notes/5', body: { done: true }, v: 1 },
    { id: 'hw:a', path: '/api/homework/done', body: { key: 'a', done: true }, v: 1 },
  ]);
  h.state.reply = (path) => (path === '/api/notes/5'
    ? Object.assign(new Error('Заметка не найдена'), { reason: '' })
    : unreachable());
  await h.outbox.flush();
  assert.deepEqual(h.queued().map((op) => op.id), ['hw:a']);
});

test('a change made while online waits behind an earlier queued one', async () => {
  const h = setup([{ id: 'hw:a', path: '/api/homework/done', body: { key: 'a', done: true }, v: 1 }]);
  h.state.offline = true;
  await h.outbox.send('hw:a', '/api/homework/done', { key: 'a', done: false });
  h.state.offline = false;
  await h.outbox.flush();
  assert.deepEqual(h.sent, [['/api/homework/done', { key: 'a', done: false }]]);
});

test('a note created offline gets its real id, and done follows as an update', async () => {
  const h = setup();
  h.state.offline = true;
  await h.outbox.send('note:-7', '/api/notes', { date: '2026-10-20', text: 'Циркуль', done: true });
  let created = null;
  h.document.addEventListener('netschool:note-created', ({ detail }) => { created = detail; });
  h.state.offline = false;
  h.state.reply = (path, body) => ({ ok: true, data: path === '/api/notes' ? { id: 42, ...body, done: false } : body });
  await h.outbox.flush();
  assert.deepEqual(h.sent.map(([path]) => path), ['/api/notes', '/api/notes/42']);
  assert.deepEqual(h.sent[1][1], { done: true });
  assert.equal(created.temp, -7);
  assert.equal(h.outbox.realId(-7), 42);
  assert.deepEqual(h.queued(), []);
});

test('a note edited while it was being created is updated by its real id', async () => {
  const h = setup([{ id: 'note:-7', path: '/api/notes', body: { date: '2026-10-20', text: 'Циркуль' }, v: 1 }]);
  h.state.reply = (path, body) => {
    if (path === '/api/notes') {
      // Поправили, пока запрос шёл.
      h.outbox.send('note:-7', '/api/notes', { date: '2026-10-20', text: 'Циркуль и линейка' });
      return { ok: true, data: { id: 42, ...body } };
    }
    return { ok: true, data: body };
  };
  await h.outbox.flush();
  await h.outbox.flush();
  assert.deepEqual(h.sent.map(([path]) => path), ['/api/notes', '/api/notes/42']);
  assert.equal(h.sent[1][1].text, 'Циркуль и линейка');
});

test('a change undone offline leaves nothing to send', async () => {
  const h = setup();
  h.state.offline = true;
  await h.outbox.send('hw:a', '/api/homework/done', { key: 'a', done: true }, { done: false });
  assert.equal(h.queued().length, 1);
  await h.outbox.send('hw:a', '/api/homework/done', { key: 'a', done: false }, { done: true });
  assert.deepEqual(h.queued(), []);
  // Настройки: поменяли две, одну вернули — ждёт только другая.
  await h.outbox.send('prefs', '/api/prefs', { theme: 'dark' }, { theme: 'light' });
  await h.outbox.send('prefs', '/api/prefs', { theme: 'dark', weekends: '1' }, { weekends: '0' });
  await h.outbox.send('prefs', '/api/prefs', { theme: 'light', weekends: '1' }, {});
  assert.equal(h.queued().length, 1);
  await h.outbox.send('prefs', '/api/prefs', { theme: 'light', weekends: '0' }, {});
  assert.deepEqual(h.queued(), []);
});

test('undoing a change that is already on its way is still sent', async () => {
  const h = setup([{ id: 'hw:a', path: '/api/homework/done', body: { key: 'a', done: true }, base: { done: false }, v: 1 }]);
  h.state.reply = (path, body) => {
    if (body.done) h.outbox.send('hw:a', '/api/homework/done', { key: 'a', done: false }, { done: true });
    return { ok: true, data: body };
  };
  await h.outbox.flush();
  await h.outbox.flush();
  assert.deepEqual(h.sent.map(([, body]) => body.done), [true, false]);
});
