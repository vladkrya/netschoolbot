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
