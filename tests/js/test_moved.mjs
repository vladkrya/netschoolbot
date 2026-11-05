import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const source = readFileSync(new URL('../../app/web/static/moved.js', import.meta.url), 'utf8');

async function run({ standalone = false, telegram = false, ua = 'Mozilla Safari', path = '/', query = '?open=settings&token=secret', hash = '#screen' } = {}) {
  const redirects = [];
  const location = { pathname: path, search: query, hash, replace: url => redirects.push(url) };
  const window = { matchMedia: mode => ({ matches: standalone && mode.includes('standalone') }), Telegram: { WebApp: { initData: telegram ? 'signed' : '' } } };
  const document = { currentScript: { dataset: { to: 'https://sgoshka.ru', base: '' }, src: 'https://ns.ikrya.ru/static/moved.js' },
    createElement: () => ({ style: {} }), body: { append() {} } };
  const context = vm.createContext({ window, document, navigator: { userAgent: ua, maxTouchPoints: 0 }, location,
    URL, URLSearchParams, Date, localStorage: { getItem: () => String(Date.now()) }, setTimeout, clearTimeout });
  new vm.Script(source, { importModuleDynamically: () => Promise.reject(new Error('no module')) }).runInContext(context);
  await new Promise(resolve => setImmediate(resolve));
  return { redirects, moving: window.SGOSHKA_MOVING };
}

test('browser moves to sgoshka, retaining route and removing persistent token', async () => {
  const { redirects, moving } = await run();
  assert.equal(moving, true);
  assert.equal(redirects.length, 1);
  const url = new URL(redirects[0]);
  assert.equal(url.origin, 'https://sgoshka.ru');
  assert.equal(url.searchParams.get('open'), 'settings');
  assert.equal(url.searchParams.has('token'), false);
  assert.equal(url.hash, '#screen');
});

test('Android browser also moves; native WebView stays', async () => {
  assert.equal((await run({ ua: 'Mozilla Android Chrome' })).redirects.length, 1);
  assert.equal((await run({ ua: 'Mozilla Android; wv) Chrome' })).redirects.length, 0);
});

test('installed PWA and Telegram retain the old origin', async () => {
  for (const options of [{ standalone: true }, { telegram: true }, { hash: '#tgWebAppData=signed' }]) {
    const { moving, redirects } = await run(options);
    assert.equal(redirects.length, 0);
    assert.equal(moving, undefined);
  }
});

test('a double slash in the path cannot redirect outside sgoshka', async () => {
  const { redirects } = await run({ path: '//other.example/path', query: '' });
  assert.equal(new URL(redirects[0]).origin, 'https://sgoshka.ru');
});
