import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/web/static/js/prefs.js', import.meta.url), 'utf8')
  .replace(/^import [^;]*;\n/gm, '').replace(/^export /gm, '');

test('managed preferences override stale local and queued edits, and unlock after release', async () => {
  const local = new Map([['ns-theme', 'light']]);
  const notices = [];
  let payload = { data: { theme: 'dark' }, managed: { theme: 'dark' } };
  const context = vm.createContext({
    localStorage: { getItem: (k) => local.get(k) ?? null, setItem: (k,v) => local.set(k,v) },
    api: async () => payload, demo: false, banner: (s) => notices.push(s),
    pendingOp: () => ({body:{theme:'light'}}), send: async () => {},
    setTimeout: () => 1, clearTimeout() {},
  });
  vm.runInContext(`${source}\nglobalThis.exports = { syncPrefs, getPref, setPref };`, context);
  const app = context.exports;
  assert.equal(await app.syncPrefs(), true);
  assert.equal(app.getPref('theme'), 'dark');
  assert.equal(local.get('ns-theme'), 'dark');
  app.setPref('theme', 'light');
  assert.equal(app.getPref('theme'), 'dark');
  assert.equal(notices.length, 1);
  payload = { data: { theme: 'dark' }, managed: {} };
  assert.equal(await app.syncPrefs(), true);
  app.setPref('theme', 'light');
  assert.equal(app.getPref('theme'), 'light');
});
