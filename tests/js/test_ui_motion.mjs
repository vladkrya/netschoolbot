import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

let code = readFileSync(new URL('../../app/web/static/js/ui-motion.js', import.meta.url), 'utf8')
  .replace(/^import .*;$/gm, '')
  .replace(/^export /gm, '');

class MockElement {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase();
    this.nodeType = 1;
    this.children = [];
    this.classSet = new Set();
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.value = '';
    this.type = 'text';
    this.maxLength = 6;
    this.selectionEnd = 0;
    this.validity = { valid: true };
    this.offsetWidth = 200;
    this.offsetHeight = 40;
    this.clientWidth = 200;
    this.clientHeight = 40;
    this.offsetLeft = 10;
    this.offsetTop = 5;
    this._textContent = '';
    this.parentElement = null;
    this.nextElementSibling = null;

    this.classList = {
      contains: (c) => this.classSet.has(c),
      add: (...cls) => cls.forEach((c) => this.classSet.add(c)),
      remove: (...cls) => cls.forEach((c) => this.classSet.delete(c)),
      toggle: (c, force) => {
        if (force === undefined) force = !this.classSet.has(c);
        if (force) this.classSet.add(c);
        else this.classSet.delete(c);
        return force;
      },
    };

    const styleObj = {
      setProperty(k, v) { this[k] = String(v); },
      removeProperty(k) { delete this[k]; },
    };
    this.style = new Proxy(styleObj, {
      get(t, p) { return t[p] || ''; },
      set(t, p, v) { t[p] = String(v); return true; },
    });
  }

  get className() {
    return Array.from(this.classSet).join(' ');
  }
  set className(v) {
    this.classSet.clear();
    (v || '').split(/\s+/).filter(Boolean).forEach((c) => this.classSet.add(c));
  }

  get textContent() {
    if (this.children.length === 0) return this._textContent;
    return this.children.map((c) => c.textContent).join('');
  }
  set textContent(v) {
    this.children.length = 0;
    this._textContent = String(v ?? '');
  }

  get innerHTML() {
    return '';
  }
  set innerHTML(html) {
    this.children.length = 0;
    const tokens = html.match(/<[^>]+>|[^<]+/g) || [];
    const stack = [this];
    for (const token of tokens) {
      if (token.startsWith('</')) {
        if (stack.length > 1) stack.pop();
      } else if (token.startsWith('<')) {
        const tagMatch = token.match(/^<([a-zA-Z0-9]+)/);
        if (!tagMatch) continue;
        const tag = tagMatch[1];
        const node = createElement(tag);
        const classMatch = token.match(/class="([^"]+)"/);
        if (classMatch) node.className = classMatch[1];
        const textMatch = token.match(/>([^<]*)$/);
        if (textMatch && textMatch[1]) node.textContent = textMatch[1];
        const cur = stack[stack.length - 1];
        cur.appendChild(node);
        if (!token.endsWith('/>') && !['input', 'img', 'circle', 'path'].includes(tag.toLowerCase())) {
          stack.push(node);
        }
      } else if (token.trim()) {
        const cur = stack[stack.length - 1];
        cur.textContent = token.trim();
      }
    }
  }

  before(...nodes) {
    if (this.parentElement) {
      const idx = this.parentElement.children.indexOf(this);
      nodes.forEach((n) => {
        n.parentElement = this.parentElement;
        this.parentElement.children.splice(idx, 0, n);
      });
    }
  }
  prepend(...nodes) {
    nodes.forEach((n) => {
      n.parentElement = this;
      this.children.unshift(n);
    });
  }
  appendChild(node) {
    node.parentElement = this;
    this.children.push(node);
    return node;
  }
  append(...nodes) {
    nodes.forEach((n) => this.appendChild(n));
  }
  insertBefore(newNode, refNode) {
    newNode.parentElement = this;
    const idx = this.children.indexOf(refNode);
    if (idx !== -1) this.children.splice(idx, 0, newNode);
    else this.children.push(newNode);
    return newNode;
  }
  removeChild(node) {
    const idx = this.children.indexOf(node);
    if (idx !== -1) {
      this.children.splice(idx, 1);
      node.parentElement = null;
    }
    return node;
  }
  remove() {
    if (this.parentElement) this.parentElement.removeChild(this);
  }
  replaceWith(newEl) {
    if (this.parentElement) {
      const idx = this.parentElement.children.indexOf(this);
      this.parentElement.children[idx] = newEl;
      newEl.parentElement = this.parentElement;
      this.parentElement = null;
    }
  }
  replaceChildren(...nodes) {
    this.children.length = 0;
    nodes.forEach((n) => this.appendChild(n));
  }
  insertAdjacentHTML(pos, html) {
    const span = createElement('span');
    span.innerHTML = html;
    const ch = [...span.children];
    if (pos === 'beforeend') {
      ch.forEach((c) => this.appendChild(c));
    } else if (pos === 'afterend' && this.parentElement) {
      ch.forEach((c) => this.parentElement.appendChild(c));
    }
  }
  insertAdjacentElement(pos, element) {
    if (pos === 'afterend' && this.parentElement) {
      const idx = this.parentElement.children.indexOf(this);
      this.parentElement.children.splice(idx + 1, 0, element);
      element.parentElement = this.parentElement;
      this.nextElementSibling = element;
    }
    return element;
  }
  setAttribute(k, v) {
    this.attributes.set(k, String(v));
  }
  getAttribute(k) {
    return this.attributes.get(k) ?? null;
  }
  removeAttribute(k) {
    this.attributes.delete(k);
  }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }
  removeEventListener(type, fn) {
    if (this.listeners.has(type)) {
      this.listeners.set(type, this.listeners.get(type).filter((f) => f !== fn));
    }
  }
  dispatchEvent(event) {
    const list = this.listeners.get(event.type) || [];
    list.forEach((fn) => fn(event));
  }
  focus() {}
  blur() {}
  click() {
    this.dispatchEvent({ type: 'click', isTrusted: true, target: this });
  }
  contains(other) {
    let p = other;
    while (p) {
      if (p === this) return true;
      p = p.parentElement;
    }
    return false;
  }
  closest(sel) {
    let p = this;
    while (p) {
      if (matchesSimple(p, sel)) return p;
      p = p.parentElement;
    }
    return null;
  }
  querySelector(sel) {
    return findSelector(this, sel);
  }
  querySelectorAll(sel) {
    const res = [];
    findAllSelector(this, sel, res);
    return res;
  }
  cloneNode(deep) {
    const c = createElement(this.tagName.toLowerCase());
    c.className = this.className;
    c.textContent = this.textContent;
    return c;
  }
}

class MockInputElement extends MockElement {
  constructor() {
    super('input');
  }
}

function createElement(tag = 'div') {
  return tag.toLowerCase() === 'input' ? new MockInputElement() : new MockElement(tag);
}

function matchesSimple(el, sel) {
  if (!el || !el.classList || !sel) return false;
  if (sel.startsWith('.')) {
    const classes = sel.slice(1).split('.');
    return classes.every((c) => el.classList.contains(c));
  }
  if (sel.startsWith('#')) return el.id === sel.slice(1);
  if (sel === 'input[type="file"]') return el.tagName === 'INPUT' && el.type === 'file';
  if (sel === 'input[autocomplete="one-time-code"]') return el.tagName === 'INPUT' && el.getAttribute('autocomplete') === 'one-time-code';
  if (sel === 'input[type="password"][autocomplete="new-password"]') {
    return el.tagName === 'INPUT' && el.type === 'password' && el.getAttribute('autocomplete') === 'new-password';
  }
  if (sel.toLowerCase() === el.tagName.toLowerCase()) return true;
  return false;
}

function findSelector(root, sel) {
  const parts = sel.trim().split(/\s+/);
  if (parts.length === 1) return findSingle(root, parts[0]);
  let current = [root];
  for (const part of parts) {
    const next = [];
    for (const node of current) findAllSimple(node, part, next);
    if (!next.length) return null;
    current = next;
  }
  return current[0] || null;
}

function findAllSelector(root, sel, out) {
  const parts = sel.trim().split(/\s+/);
  if (parts.length === 1) {
    findAllSimple(root, parts[0], out);
    return;
  }
  let current = [root];
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    const isLast = i === parts.length - 1;
    const next = [];
    for (const node of current) findAllSimple(node, part, isLast ? out : next);
    current = next;
  }
}

function findSingle(el, sel) {
  for (const child of el.children) {
    if (matchesSimple(child, sel)) return child;
    const f = findSingle(child, sel);
    if (f) return f;
  }
  return null;
}

function findAllSimple(el, sel, out) {
  for (const child of el.children) {
    if (matchesSimple(child, sel)) out.push(child);
    findAllSimple(child, sel, out);
  }
}

function setupContext() {
  const doc = {
    documentElement: createElement('html'),
    createElement,
    getElementById(id) { return null; },
    createTextNode(t) { return { nodeType: 3, textContent: t, children: [] }; },
    activeElement: null,
  };
  let rafId = 1;
  const cbs = [];
  const timers = new Set();

  const context = {
    document: doc,
    window: {
      document: doc,
      matchMedia: () => ({ matches: false, addEventListener() {} }),
      requestAnimationFrame: (cb) => { cbs.push(cb); return rafId++; },
      cancelAnimationFrame: () => {},
      performance: { now: () => 1000 },
    },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    requestAnimationFrame: (cb) => { cbs.push(cb); return rafId++; },
    cancelAnimationFrame: () => {},
    performance: { now: () => 1000 },
    setTimeout: (fn, ms) => {
      const id = setTimeout(() => {
        timers.delete(id);
        fn();
      }, ms);
      timers.add(id);
      return id;
    },
    clearTimeout: (id) => {
      clearTimeout(id);
      timers.delete(id);
    },
    Event: class { constructor(type) { this.type = type; } },
    HTMLInputElement: MockInputElement,
    console,
    Math,
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  return { context, createElement, cleanup: () => timers.forEach(clearTimeout) };
}

test('File drop component initializes and triggers upload workflow', () => {
  const { context, createElement, cleanup } = setupContext();
  try {
    const zone = createElement('div');
    zone.className = 'mail-file-drop';
    const parent = createElement('div');
    parent.appendChild(zone);

    let uploaded = null;
    const drop = context.initFileDrop(zone, (files) => { uploaded = files; });
    assert.ok(zone.dataset.dropReady, 'dropReady marked');
    assert.ok(zone.querySelector('.mail-file-core'), 'core created');
    assert.ok(zone.querySelector('.ring'), 'ring svg created');

    drop.startUpload('homework.pdf', 1024 * 500);
    assert.equal(drop.currentTitle, 'Загружаем homework.pdf');

    drop.completeUpload();
    assert.equal(drop.currentTitle, 'Файл прикреплён');
  } finally {
    cleanup();
  }
});

test('Springy input wraps input and handles character typing', () => {
  const { context, createElement, cleanup } = setupContext();
  try {
    const parent = createElement('form');
    const input = createElement('input');
    input.type = 'text';
    input.value = 'hello';
    parent.appendChild(input);

    context.springyInput(input);
    assert.ok(input.dataset.springyReady, 'springy ready flag set');
    const shell = parent.querySelector('.springy-email');
    assert.ok(shell, 'wrapped in .springy-email');
    const layer = shell.querySelector('.springy-email-layer');
    assert.ok(layer, 'springy layer created');
    assert.equal(layer.querySelectorAll('.springy-char').length, 5, 'chars created');

    context.rejectSpringyInput(input);
    assert.ok(input.classList.contains('spring-invalid'), 'marked invalid on rejection');
  } finally {
    cleanup();
  }
});

test('Password strength meter updates score and bars', () => {
  const { context, createElement, cleanup } = setupContext();
  try {
    const parent = createElement('div');
    const input = createElement('input');
    input.type = 'password';
    input.setAttribute('autocomplete', 'new-password');
    parent.appendChild(input);

    context.passwordMeter(input);
    const meter = parent.querySelector('.password-meter');
    assert.ok(meter, 'password meter element created');

    input.value = 'Secret123!';
    input.dispatchEvent({ type: 'input' });
    assert.equal(meter.dataset.level, '4', 'strong password scored 4');
    assert.equal(meter.querySelector('.password-word').textContent, 'Надёжный пароль');
  } finally {
    cleanup();
  }
});

test('OTP code component creates 6 boxes and handles verification', () => {
  const { context, createElement, cleanup } = setupContext();
  try {
    const parent = createElement('div');
    const input = createElement('input');
    input.setAttribute('autocomplete', 'one-time-code');
    input.maxLength = 6;
    parent.appendChild(input);

    context.otpBoxes(input);
    const wrap = parent.querySelector('.motion-otp');
    assert.ok(wrap, 'otp wrapper created');
    const boxes = wrap.querySelectorAll('.motion-otp-box');
    assert.equal(boxes.length, 6, '6 boxes created');

    input.value = '123456';
    input.dispatchEvent({ type: 'input' });

    context.setOtpResult(input, true);
    assert.ok(wrap.classList.contains('verified'), 'verified class applied');

    context.setOtpResult(input, false);
    assert.ok(wrap.classList.contains('rejected'), 'rejected class applied on error');
  } finally {
    cleanup();
  }
});

test('Streaming reply component adds words with springs', () => {
  const { context, createElement, cleanup } = setupContext();
  try {
    const container = createElement('div');
    const streamer = context.createStreamingReply(container);

    assert.ok(container.classList.contains('ai-streaming-wrap'), 'streaming wrap added');
    const bubble = container.querySelector('.ai-stream-bubble');
    assert.ok(bubble, 'stream bubble added');
    assert.ok(container.querySelector('.think'), 'thinking shimmer added');

    streamer.update('Привет, ученик!');
    const words = container.querySelectorAll('.stream-text .wd');
    assert.equal(words.length, 2, 'two words generated');

    streamer.finish();
    assert.equal(words[0].style.opacity, '1', 'words settle on finish');
    streamer.destroy();
  } finally {
    cleanup();
  }
});

test('Kokonut UI typewriter renders structure and screen reader text', () => {
  const { context, createElement, cleanup } = setupContext();
  try {
    const el = createElement('span');
    const controller = context.cycleTypewriter(el, ['удобно и быстро', 'всё нужное — рядом']);

    assert.ok(el.dataset.cycling, 'cycling marked');
    assert.ok(el.querySelector('.typewriter-screen'), 'screen created');
    assert.ok(el.querySelector('.typewriter-caret'), 'caret created');
    assert.equal(el.querySelector('.sr-only').textContent, 'удобно и быстро, всё нужное — рядом');
    controller.stop();
  } finally {
    cleanup();
  }
});
