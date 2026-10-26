import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
const code = readFileSync(new URL('../../app/web/static/js/pinpad.js', import.meta.url), 'utf8')
  .replace(/^import .*;$/gm, '').replace('export function', 'function');
function setup() {
  const animations = [];
  const element = () => ({classList: {add(){}, remove(){}, toggle(){}}, hidden: false,
    before(){}, after(){}, setAttribute(){}, addEventListener(){}, removeEventListener(){},
    getAnimations(){return [];}, animate(frames, options) {
      const a = {target: this, options, cancelled: false, cancel(){this.cancelled = true;}};
      animations.push(a); return a;
    }});
  const dots = Array.from({length: 4}, element), buttons = Array.from({length: 12}, element);
  const entries = new Map();
  for (const selector of ['.pin-hint', '.pin-dots', '.pin-progress', '.pin-emblem', '.lock-error', '[data-delete]', '[data-biometric]']) entries.set(selector, element());
  for (let i = 0; i < 10; i++) { buttons[i].dataset = {digit: String(i)}; entries.set(`[data-digit="${i}"]`, buttons[i]); }
  const field = element();
  field.querySelector = selector => entries.get(selector);
  field.querySelectorAll = selector => selector === '.pin-dots i' ? dots : selector === '[data-digit]' ? buttons.slice(0, 10) : buttons;
  const root = element();
  root.querySelector = selector => selector === '.lock-fields' ? field : selector === 'h2' ? element() : entries.get(selector);
  root.focus = () => {};
  const context = {window: {webkit: {messageHandlers: {sgoshkaFaceId: {}}}}, stillMotion: () => false,
    tap(){}, notify(){}, setTimeout, HTMLInputElement: class {}};
  vm.createContext(context); vm.runInContext(code + '; globalThis.mount = mountPinpad;', context);
  return {root, dots, buttons, animations, mount: context.mount};
}
test('Native Face ID submits PIN without animating digit keys, and cleans up loading dots', async () => {
  const t = setup(); let submitted, release;
  const pad = t.mount(t.root, pin => { submitted = pin; return new Promise(resolve => {release = resolve;}); });
  assert.equal(t.animations.length, 0);
  const pending = t.root.submitNativePin('1234');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(submitted, '1234');
  assert.equal(t.animations.filter(a => t.buttons.includes(a.target)).length, 0);
  assert.equal(t.animations.filter(a => a.options.iterations === Infinity).length, 4);
  release(); await pending;
  assert.ok(t.animations.every(a => a.cancelled));
  pad.destroy(); assert.equal(t.root.submitNativePin, undefined);
});
test('Partial PIN survives status handoff and is submitted only at four digits', async () => {
  const t = setup(); let submitted;
  t.mount(t.root, async pin => {submitted = pin;});
  await t.root.submitNativePin('12');
  assert.equal(submitted, undefined); assert.equal(t.root.getPinValue(), '12');
  t.buttons[3].onclick(); t.buttons[4].onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(submitted, '1234');
});
