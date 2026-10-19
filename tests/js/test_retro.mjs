import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/web/static/js/retro.js', import.meta.url), 'utf8')
  .replace(/^import .*;\n/gm, '').replace(/^export /gm, '');

test('retro requires explicit opt-in and respects the return to the ordinary app', () => {
  const local = new Map();
  const session = new Map();
  const context = vm.createContext({
    localStorage: { getItem: key => local.get(key) ?? null },
    sessionStorage: { getItem: key => session.get(key) ?? null },
  });
  const wantsRetro = vm.runInContext(`${source}\nwantsRetro`, context);
  for (const value of [null, 'off', 'true', '', 'classic']) {
    if (value === null) local.delete('ns-retro');
    else local.set('ns-retro', value);
    assert.equal(wantsRetro(), false);
  }
  local.set('ns-retro', 'on');
  assert.equal(wantsRetro(), true);
  session.set('ns-retro-skip', '1');
  assert.equal(wantsRetro(), false);
});

const bridge = readFileSync(new URL('../../app/web/static/retro/bridge.js', import.meta.url), 'utf8');
for (const settings of [false, true]) {
  test(`visible return button disables retro and opens ${settings ? 'settings' : 'ordinary app'}`, async () => {
    const local = new Map([['ns-retro', 'on']]);
    const session = new Map();
    const listeners = [];
    const requests = [];
    let destination;
    const context = vm.createContext({
      window: {
        RETRO: { base: '/app', app_url: '/app/?token=test' },
        fetch: async (url, options) => { requests.push([url, JSON.parse(options.body)]); return new Response('{}'); },
      },
      localStorage: { setItem: (k, v) => local.set(k, v), removeItem: k => local.delete(k) },
      sessionStorage: { getItem: k => session.get(k), setItem: (k, v) => session.set(k, v) },
      document: { addEventListener: (name, handler) => listeners.push(handler) },
      location: { replace: url => { destination = url; } },
      Headers, Response, URL, setTimeout: () => 0,
    });
    vm.runInContext(bridge, context);
    await listeners[0]({ preventDefault() {}, target: { closest: () => ({ dataset: { retroExit: settings ? 'settings' : '' } }) } });
    assert.equal(local.get('ns-retro'), 'off');
    assert.equal(session.get('ns-retro-skip'), '1');
    assert.deepEqual(requests, [['/app/api/prefs', { retro: 'off' }]]);
    assert.equal(destination, `/app/?token=test${settings ? '#settings' : ''}`);
  });
}

test('expired retro requests prompt for PIN and retry with the new unlock secret', async () => {
  const requests = [];
  let prompts = 0;
  const session = new Map([['ns-retro-unlock', 'old']]);
  const window = {
    RETRO: { base: '/app' },
    fetch: async (url, options) => {
      const secret = options.headers.get('X-Netschool-Unlock');
      requests.push(secret);
      return new Response(JSON.stringify(secret === 'new' ? { entries: [] } : { error: 'locked' }), { status: secret === 'new' ? 200 : 423 });
    },
  };
  vm.runInNewContext(bridge, {
    window, Headers, Response, URL,
    localStorage: { removeItem() {} },
    sessionStorage: { getItem: k => session.get(k), removeItem: k => session.delete(k) },
    document: { addEventListener() {} },
  });
  assert.equal(session.has('ns-retro-unlock'), false);
  window.retroBridge.ensureUnlocked = async () => { prompts++; window.retroBridge.setUnlock('new'); };
  const response = await window.fetch('/api/guestbook');
  assert.equal(response.status, 200);
  assert.equal(prompts, 1);
  assert.deepEqual(requests, ['old', 'new']);
});

const settingsSource = readFileSync(new URL('../../app/web/static/retro/settings.js', import.meta.url), 'utf8')
  .replace("await import('./app.js');", 'window.appLoaded = true;');
const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
function settingsHarness({ transferred = '', failStatus = false } = {}) {
  const nodes = new Map();
  const storage = new Map();
  const classes = new Set(['retro-locked']);
  const visibility = {};
  const button = { disabled: false };
  const fields = { secret: { value: '', focus() {} }, pin: { value: '' }, confirm: { value: '' } };
  const elements = Object.assign([fields.pin, fields.confirm, button], fields);
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      open: false, elements, value: '', textContent: '',
      showModal() { this.open = true; }, close() { this.open = false; },
      addEventListener() {}, querySelector: () => button, reset() {},
    });
    return nodes.get(id);
  };
  let secret = transferred;
  const calls = [];
  const bridge = {
    setUnlock: value => { secret = value; }, hasUnlock: () => Boolean(secret), toApp() {},
    request: async (path, body) => {
      calls.push([path, body]);
      if (path === 'lock/status') {
        if (failStatus) throw new Error('Нет подключения');
        return { required: true, enabled: true, pin: true };
      }
      if (path === 'prefs') {
        if (secret !== 'valid') throw Object.assign(new Error('locked'), { status: 423 });
        return {};
      }
      if (path === 'lock/unlock') {
        if (body.pin !== '1234') throw new Error('Неверный PIN');
        return { unlock: 'valid' };
      }
      return {};
    },
  };
  const window = { retroBridge: bridge, RETRO: {}, addEventListener() {} };
  const document = {
    hidden: false, getElementById: node,
    documentElement: { classList: { add: name => classes.add(name), remove: name => classes.delete(name), toggle: (name, on) => on ? classes.add(name) : classes.delete(name) } },
    addEventListener: (name, fn) => { visibility[name] = fn; },
  };
  vm.runInNewContext(settingsSource, {
    window, document,
    localStorage: { getItem: key => storage.get(key), setItem: (k, v) => storage.set(k, v) },
  });
  const submit = async pin => {
    fields.secret.value = pin;
    await node('retroUnlockForm').onsubmit({ preventDefault() {}, currentTarget: node('retroUnlockForm') });
    await settle();
  };
  return { window, document, node, classes, storage, visibility, calls, submit };
}

test('direct retro launch loads no diary until PIN succeeds; incorrect PIN keeps it hidden', async () => {
  const h = settingsHarness();
  await settle();
  assert.equal(h.window.appLoaded, undefined);
  assert.equal(h.node('retroLock').open, true);
  await h.submit('0000');
  assert.equal(h.node('retroLockError').textContent, 'Неверный PIN');
  assert.equal(h.classes.has('retro-locked'), true);
  await h.submit('1234');
  assert.equal(h.window.appLoaded, true);
  assert.equal(h.classes.has('retro-locked'), false);
  assert.equal(h.node('retroLock').open, false);
});

test('transferred unlock is verified and return-to-app protection asks for PIN again', async () => {
  const h = settingsHarness({ transferred: 'valid' });
  await settle();
  assert.equal(h.window.appLoaded, true);
  assert.equal(h.calls.some(([path]) => path === 'prefs'), true);
  h.storage.set('lockOnReturn', '1');
  h.document.hidden = true;
  h.visibility.visibilitychange();
  assert.equal(h.classes.has('retro-locked'), true);
  h.document.hidden = false;
  h.visibility.visibilitychange();
  await settle();
  assert.equal(h.node('retroLock').open, true);
  await h.submit('1234');
  assert.equal(h.classes.has('retro-locked'), false);
});

test('failed protection check never reveals the diary', async () => {
  const h = settingsHarness({ failStatus: true });
  await settle();
  assert.equal(h.window.appLoaded, undefined);
  assert.equal(h.classes.has('retro-locked'), true);
  assert.equal(h.node('retroLockError').textContent, 'Нет подключения');
});
