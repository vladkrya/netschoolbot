import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/web/static/js/appearance.js', import.meta.url), 'utf8')
  .replace(/^import [^;]*;\n/gm, '').replace(/^export /gm, '');

function setup(prefs = {}, holiday = null) {
  const attrs = new Map(holiday ? [['data-holiday', holiday]] : []);
  const styles = new Map();
  let removedDecor = 0;
  const root = {
    style: { setProperty: (k, v) => styles.set(k, v), removeProperty: (k) => styles.delete(k) },
    getAttribute: (k) => attrs.get(k) ?? null,
    setAttribute: (k, v) => attrs.set(k, v), removeAttribute: (k) => attrs.delete(k),
  };
  const context = vm.createContext({
    getPref: (k) => prefs[k] ?? null, setPref: (k, v) => { prefs[k] = v; },
    initData: '', tg: null,
    window: { matchMedia: () => ({ addEventListener() {} }) },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: {
      documentElement: root, querySelector: () => null,
      querySelectorAll: (selector) => selector.startsWith('.hw-')
        ? [{ remove() { removedDecor++; } }] : [],
    },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
  });
  vm.runInContext(`${source}\nglobalThis.api = { saveUnity, setUnityAccess, setHalloweenAccess, applyAccent, applyHalloween, isHalloween };`, context);
  return { ...context.api, attrs, styles, prefs, removed: () => removedDecor };
}

test('unity replaces Halloween decor and restores the chosen accent when disabled', () => {
  const app = setup({ accent: '#a8434f' }, 'halloween');
  app.setHalloweenAccess(false);
  app.saveUnity(true);
  assert.equal(app.attrs.get('data-holiday'), 'unity');
  assert.equal(app.styles.get('--head'), '#315fba');
  assert.equal(app.isHalloween(), false);
  assert.ok(app.removed() > 0);
  app.saveUnity(false);
  assert.equal(app.attrs.has('data-holiday'), false);
  assert.equal(app.styles.get('--head'), '#a8434f');
  assert.equal(app.prefs.accent, '#a8434f');
});

test('server Halloween access cannot override a restored unity choice', () => {
  const app = setup({ unity: 'on', theme: 'dark' }, 'unity');
  app.setHalloweenAccess(true);
  app.applyAccent();
  assert.equal(app.attrs.get('data-holiday'), 'unity');
  assert.equal(app.styles.get('--head'), '#315fba');
  assert.equal(app.prefs.theme, 'dark');
  // A setting synced from another device is applied through the same entry point.
  app.prefs.unity = 'off';
  app.prefs.halloween = 'off';
  app.applyHalloween();
  assert.equal(app.attrs.has('data-holiday'), false);
});

test('a non-admin profile disables a cached unity choice', () => {
  const app = setup({ unity: 'on', halloween: 'off', accent: '#a8434f' }, 'unity');
  app.setUnityAccess(false);
  assert.equal(app.attrs.has('data-holiday'), false);
  assert.equal(app.styles.get('--head'), '#a8434f');
  app.setUnityAccess(true);
  assert.equal(app.attrs.get('data-holiday'), 'unity');
});
