import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// tabs.js тянет DOM и настройки — берём сам модуль и подставляем зависимости.
const source = readFileSync(new URL('../../app/web/static/js/tabs.js', import.meta.url), 'utf8')
  .replace(/^import [^;]*;\n/gm, '').replace(/^export /gm, '');

function load({ myschool, fresh = true, saved = null }) {
  const context = vm.createContext({
    $: () => null, isReadOnly: () => false, isMyschool: () => myschool,
    getPref: () => saved, setPref: () => {}, isNewDesign: () => fresh, navIcon: () => '',
  });
  vm.runInContext(`${source}\nglobalThis.api = { navTabs, availableTabs, defaultTabs };`, context);
  return context.api;
}

const list = (value) => JSON.parse(JSON.stringify(value));

test('regular user keeps messages', () => {
  const tabs = load({ myschool: false });
  assert.deepEqual(list(tabs.defaultTabs()), ['day', 'homework', 'totals', 'mail', 'settings']);
  assert.ok(tabs.availableTabs().includes('mail'));
});

test('myschool: games replace messages in the default set', () => {
  const tabs = load({ myschool: true });
  assert.deepEqual(list(tabs.navTabs()), ['day', 'homework', 'totals', 'games', 'settings']);
});

test('myschool: messages cannot be enabled', () => {
  const tabs = load({ myschool: true });
  assert.ok(!tabs.availableTabs().includes('mail'));
  assert.ok(tabs.availableTabs().includes('games'));
});

test('myschool: a saved set with messages turns into games without repeats', () => {
  const tabs = load({ myschool: true, saved: ['day', 'mail', 'games', 'settings'] });
  assert.deepEqual(list(tabs.navTabs()), ['day', 'games', 'settings']);
});

test('myschool: classic design swaps the last tab too', () => {
  const tabs = load({ myschool: true, fresh: false });
  assert.deepEqual(list(tabs.navTabs()), ['day', 'schedule', 'homework', 'totals', 'games']);
});
