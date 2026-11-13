// Spring presets copied from Motion Studies: caret 700/32, morph 300/20,
// pop 520/16 and panel 260/24. They are expressed in CSS below.
'use strict';

import { base } from './core.js';

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches || document.documentElement.classList.contains('no-motion');
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

export function enhanceAuthMotion(root) {
  if (!root || root.dataset.motionReady) return;
  root.dataset.motionReady = '1';
  root.querySelectorAll('input.field').forEach((input) => {
    input.classList.add('spring-field');
    if (input.type === 'email' || input.type === 'password' || input.autocomplete === 'username') {
      springyInput(input);
    } else {
      input.addEventListener('focus', () => input.classList.add('spring-focus'));
      input.addEventListener('blur', () => input.classList.remove('spring-focus'));
    }
  });
  root.querySelectorAll('input[type="password"][autocomplete="new-password"]').forEach(passwordMeter);
  root.querySelectorAll('input[autocomplete="one-time-code"]').forEach(otpBoxes);
  root.querySelectorAll('form').forEach((form) => form.addEventListener('invalid', (event) => {
    const field = event.target;
    if (field instanceof HTMLInputElement) {
      field.classList.add('spring-invalid');
      field.animate?.([{ transform: 'translateX(0)' }, { transform: 'translateX(-7px)' }, { transform: 'translateX(6px)' }, { transform: 'translateX(0)' }], { duration: 360, easing: 'cubic-bezier(.34,1.56,.64,1)' });
    }
  }, true));
}

// Designbookmark's Springy Input, adapted around our existing real email field.
// The native input is never replaced: it still owns focus, selection, paste,
// password managers and form submission.  The transparent text is redrawn by
// a mirror solely to give every arriving character and the caret its spring.
function springyInput(input) {
  if (input.dataset.springyReady) return;
  input.dataset.springyReady = '1';
  const shell = document.createElement('span');
  shell.className = 'springy-email';
  const layer = document.createElement('span'); layer.className = 'springy-email-layer';
  const caret = document.createElement('span'); caret.className = 'springy-email-caret';
  input.before(shell); shell.append(input, layer, caret);
  let previous = input.value, focused = document.activeElement === input;
  let x = 0, velocity = 0, target = 0, frame = 0, blinkTimer = 0;
  const glyph = (char) => input.type === 'password' ? '•' : (char === ' ' ? '\u00a0' : char);

  const spans = () => [...layer.querySelectorAll('.springy-char:not(.leaving)')];
  const positionCaret = () => {
    const chars = spans(); const position = input.selectionEnd ?? chars.length;
    if (!chars.length) return 0;
    if (position < chars.length) return chars[position].offsetLeft;
    const last = chars[chars.length - 1]; return last.offsetLeft + last.offsetWidth;
  };
  const render = () => {
    frame = 0;
    target = positionCaret();
    // The same --input-caret 700/32 spring from the source token file.
    const stiffness = 700, damping = 32, dt = 1 / 60;
    const acceleration = -stiffness * (x - target) - damping * velocity;
    velocity += acceleration * dt; x += velocity * dt;
    const stretch = Math.min(Math.abs(velocity) / 80, 7);
    caret.style.width = `${2 + stretch}px`;
    caret.style.transform = `translateX(${x - (velocity > 0 ? stretch : 0)}px)`;
    if (Math.abs(target - x) > .15 || Math.abs(velocity) > .15) frame = requestAnimationFrame(render);
  };
  const moveCaret = () => {
    clearTimeout(blinkTimer); caret.classList.remove('blink');
    if (focused) blinkTimer = setTimeout(() => caret.classList.add('blink'), 550);
    if (!frame) frame = requestAnimationFrame(render);
  };
  const sync = () => {
    const next = input.value;
    let start = 0; while (start < previous.length && start < next.length && previous[start] === next[start]) start++;
    let end = 0; while (end < previous.length - start && end < next.length - start && previous.at(-1 - end) === next.at(-1 - end)) end++;
    const current = spans();
    current.slice(start, current.length - end).forEach((span) => {
      span.classList.add('leaving');
      setTimeout(() => span.remove(), 340);
    });
    const before = current[start] || null;
    [...next.slice(start, next.length - end)].forEach((char, index) => {
      const span = document.createElement('span');
      span.className = 'springy-char entering'; span.textContent = glyph(char);
      span.style.setProperty('--delay', `${Math.min(index, 30) * 18}ms`);
      layer.insertBefore(span, before);
    });
    previous = next;
    if (input.type === 'email') input.classList.toggle('spring-invalid', next.length > 0 && !input.validity.valid);
    moveCaret();
  };
  input.addEventListener('input', sync);
  input.addEventListener('focus', () => { focused = true; input.classList.add('spring-focus'); moveCaret(); });
  input.addEventListener('blur', () => { focused = false; clearTimeout(blinkTimer); caret.classList.remove('blink'); input.classList.remove('spring-focus'); moveCaret(); });
  ['keydown', 'keyup', 'click', 'select'].forEach((event) => input.addEventListener(event, moveCaret));
  input.form?.addEventListener('submit', () => {
    if (input.validity.valid) return;
    input.classList.add('spring-invalid');
    spans().forEach((span, index) => { span.style.setProperty('--delay', `${index * 14}ms`); span.classList.remove('reject'); void span.offsetWidth; span.classList.add('reject'); });
  }, true);
  sync();
}

// Exact OTP interaction model: one real input remains the source of truth;
// boxes are visual only, so paste, autofill and backspace can never lose code.
function otpBoxes(input) {
  if (input.parentElement?.classList.contains('motion-otp')) return;
  const count = Number(input.maxLength) || 6;
  const wrap = document.createElement('div'); wrap.className = 'motion-otp'; wrap.style.setProperty('--otp-count', count);
  input.before(wrap); wrap.append(input); input.classList.add('motion-otp-input');
  const boxes = Array.from({ length: count }, () => { const box = document.createElement('span'); box.className = 'motion-otp-box'; box.setAttribute('aria-hidden', 'true'); wrap.append(box); return box; });
  const sync = () => {
    // Do not write back to input.value while iOS is composing a keypress.
    // Reassigning it here was the reason later digits could disappear.
    const value = input.value.replace(/\D/g, '').slice(0, count);
    boxes.forEach((box, index) => {
      const next = value[index] || '';
      if (box.textContent !== next && next) { box.textContent = next; box.animate?.([{ transform: 'translateY(-14px)', opacity: 0 }, { transform: 'translateY(0)', opacity: 1 }], { duration: 380, easing: 'cubic-bezier(.34,1.56,.64,1)' }); }
      else if (!next) box.textContent = '';
      box.classList.toggle('active', index === Math.min(value.length, count - 1) && value.length < count);
    });
    wrap.classList.toggle('complete', value.length === count);
    if (value.length < count) wrap.classList.remove('verified', 'rejected');
  };
  input.addEventListener('input', sync); input.addEventListener('focus', sync); wrap.addEventListener('click', () => input.focus()); sync();
}

// OTP is not green merely because all cells are filled: only the server can
// accept it. On rejection we visibly clear the code, ready for a new attempt.
export function setOtpResult(input, accepted) {
  const wrap = input?.closest?.('.motion-otp');
  if (!wrap) return;
  if (accepted) { wrap.classList.remove('rejected'); wrap.classList.add('verified'); return; }
  wrap.classList.remove('verified'); wrap.classList.add('rejected');
  setTimeout(() => {
    input.value = '';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    wrap.classList.remove('rejected');
    input.focus();
  }, 430);
}

// Same rejection as the source Springy Input: the actual input remains intact,
// while its visible glyphs shake in a short wave and the field turns red.
export function rejectSpringyInput(input) {
  if (!(input instanceof HTMLInputElement)) return;
  input.classList.add('spring-invalid');
  const shell = input.closest('.springy-email');
  shell?.querySelectorAll('.springy-char:not(.leaving)').forEach((span, index) => {
    span.style.setProperty('--delay', `${index * 14}ms`);
    span.classList.remove('reject'); void span.offsetWidth; span.classList.add('reject');
  });
  input.focus();
}

function passwordMeter(input) {
  const anchor = input.closest('.springy-email') || input;
  if (anchor.nextElementSibling?.classList.contains('password-meter')) return;
  const meter = document.createElement('div');
  meter.className = 'password-meter';
  meter.innerHTML = '<span class="password-bars"><i></i><i></i><i></i><i></i></span><span class="password-word" aria-live="polite">Введите не менее 8 символов</span>';
  anchor.insertAdjacentElement('afterend', meter);
  const update = () => {
    const value = input.value;
    const score = [value.length >= 8, /[a-zа-я]/i.test(value) && /[A-ZА-Я]/.test(value), /\\d/.test(value), /[^\\w\\s]/.test(value)].filter(Boolean).length;
    meter.dataset.level = String(value ? Math.max(1, score) : 0);
    meter.querySelector('.password-word').textContent = !value ? 'Введите не менее 8 символов' : score < 2 ? 'Слабый пароль' : score < 3 ? 'Можно надёжнее' : score < 4 ? 'Хороший пароль' : 'Надёжный пароль';
  };
  input.addEventListener('input', update); update();
}

// Designbookmark Motion Studies: Sidebar Collapse component
// Shared spring engine: W (260/24), R (300/22), Y (380/28), labels (300/26 with 40ms stagger)
let updateSidebarGliderTarget = null;

export function syncSidebarGlider() {
  updateSidebarGliderTarget?.();
}

export function initSidebarCollapse() {
  const nav = document.getElementById('bottomNav');
  if (!nav) return;

  const desktopQuery = matchMedia('(min-width: 1024px)');

  // 1. Ensure the header (.side-top) exists
  let top = nav.querySelector('.side-top');
  if (!top) {
    top = document.createElement('div');
    top.className = 'side-top';
    top.id = 'sideTop';
    top.innerHTML = `
      <span class="side-logo-wrap"><img class="side-logo" src="${base}/static/icon-192.png" alt="СГОшка" width="28" height="28"></span>
      <span class="lb side-brand">СГОшка</span>
      <button class="side-col" id="sideCol" type="button" aria-label="Свернуть панель" aria-expanded="true" title="Свернуть панель">
        <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14.5 6.5 9 12l5.5 5.5"/>
        </svg>
      </button>
    `;
    nav.prepend(top);
  }

  const col = top.querySelector('.side-col') || nav.querySelector('#sideCol');
  const logo = top.querySelector('.side-logo-wrap');
  const colSvg = col?.querySelector('svg');
  const glider = nav.querySelector('.nav-glider');

  if (nav.dataset.collapseReady) {
    updateSidebarGliderTarget?.();
    return;
  }
  nav.dataset.collapseReady = '1';

  const OPEN_WIDTH = 212;
  const COLLAPSED_WIDTH = 64;

  let open = localStorage.getItem('ns-sidebar-collapsed') !== '1';
  const W = { x: open ? OPEN_WIDTH : COLLAPSED_WIDTH, v: 0, t: open ? OPEN_WIDTH : COLLAPSED_WIDTH, k: 260, c: 24 };
  const R = { x: open ? 0 : 1, v: 0, t: open ? 0 : 1, k: 300, c: 22 };
  const Y = { x: 0, v: 0, t: 0, k: 380, c: 28 };

  let labels = [];

  function collectLabels() {
    const list = [];
    const brand = top.querySelector('.side-brand');
    if (brand) list.push({ el: brand, s: { x: open ? 1 : 0, v: 0, t: open ? 1 : 0, k: 300, c: 26 } });

    nav.querySelectorAll('.nav-item').forEach((item) => {
      // Pick the currently visible label span
      const lb = item.querySelector('.lbl-new:not([style*="display: none"]), .lbl-old:not([style*="display: none"]), .lb');
      if (lb) {
        list.push({ el: lb, s: { x: open ? 1 : 0, v: 0, t: open ? 1 : 0, k: 300, c: 26 } });
      }
    });

    const menuLb = nav.querySelector('.nav-menu .lb, .nav-menu span:last-child');
    if (menuLb) {
      list.push({ el: menuLb, s: { x: open ? 1 : 0, v: 0, t: open ? 1 : 0, k: 300, c: 26 } });
    }
    labels = list;
  }

  function syncGliderTarget(immediate = false) {
    const active = nav.querySelector('.nav-item.active');
    if (active) {
      Y.t = active.offsetTop;
      if (immediate) {
        Y.x = Y.t;
        Y.v = 0;
      }
      wake();
    }
  }
  updateSidebarGliderTarget = () => {
    collectLabels();
    syncGliderTarget(false);
  };

  const timers = [];
  function after(sec, fn) {
    const t = { t: sec, fn };
    timers.push(t);
    wake();
    return t;
  }

  function setOpen(o) {
    open = o;
    document.documentElement.classList.toggle('sidebar-collapsed', !o);
    if (col) {
      col.setAttribute('aria-expanded', String(o));
      col.setAttribute('aria-label', o ? 'Свернуть панель' : 'Развернуть панель');
      col.title = o ? 'Свернуть панель' : 'Развернуть панель';
    }
    W.t = o ? OPEN_WIDTH : COLLAPSED_WIDTH;
    R.t = o ? 0 : 1;
    try {
      localStorage.setItem('ns-sidebar-collapsed', o ? '0' : '1');
    } catch {}

    // Clear pending cascade timers
    timers.length = 0;

    labels.forEach((l, i) => {
      if (o) {
        after(0.06 + i * 0.04, () => {
          if (open) l.s.t = 1;
        });
      } else {
        l.s.t = 0;
      }
    });
    wake();
  }

  col?.addEventListener('click', (e) => {
    e.stopPropagation();
    setOpen(!open);
  });

  nav.addEventListener('click', (e) => {
    const item = e.target.closest('.nav-item');
    if (item) {
      Y.t = item.offsetTop;
      wake();
    }
  });

  window.addEventListener('netschool:nav', () => {
    collectLabels();
    syncGliderTarget(false);
  });

  function step(s, dt, instant) {
    if (instant) {
      s.x = s.t;
      s.v = 0;
      return;
    }
    const a = -s.k * (s.x - s.t) - s.c * s.v;
    s.v += a * dt;
    s.x += s.v * dt;
  }

  function render() {
    if (!desktopQuery.matches) return;

    const rx = clamp(R.x, 0, 1);
    const wx = W.x;

    nav.style.width = `${wx.toFixed(2)}px`;
    document.documentElement.style.setProperty('--rail', `${(wx + 24).toFixed(2)}px`);

    if (col) {
      const rightPx = 2 + rx * 4;
      col.style.right = `${rightPx.toFixed(2)}px`;
    }
    if (colSvg) {
      colSvg.style.transform = `rotate(${(180 * rx).toFixed(2)}deg)`;
    }

    if (logo) {
      logo.style.opacity = clamp(1 - rx * 2, 0, 1).toFixed(3);
    }

    labels.forEach((l) => {
      const o = clamp(l.s.x, 0, 1);
      l.el.style.opacity = o.toFixed(3);
      l.el.style.transform = `translateX(${((1 - o) * -10).toFixed(2)}px)`;
      l.el.style.visibility = o < 0.02 ? 'hidden' : '';
    });

    if (glider && Y.x > 0) {
      glider.style.transform = `translateY(${Y.x.toFixed(2)}px)`;
    }
  }

  let raf = 0;
  let lastTime = performance.now();

  function wake() {
    if (!raf) {
      lastTime = performance.now();
      raf = requestAnimationFrame(loop);
    }
  }

  function loop(now) {
    const dt = Math.min((now - lastTime) / 1000, 1 / 30);
    lastTime = now;

    for (let i = timers.length - 1; i >= 0; i--) {
      timers[i].t -= dt;
      if (timers[i].t <= 0) {
        const fn = timers.splice(i, 1)[0].fn;
        fn();
      }
    }

    const isReduced = reduced();
    const sub = 4;
    for (let k = 0; k < sub; k++) {
      [W, R, Y].forEach((s) => step(s, dt / sub, isReduced));
      labels.forEach((l) => step(l.s, dt / sub, isReduced));
    }

    render();

    const springs = [W, R, Y, ...labels.map((l) => l.s)];
    const isMoving = springs.some((s) => Math.abs(s.x - s.t) > 0.05 || Math.abs(s.v) > 0.05);

    if (isMoving || timers.length > 0) {
      raf = requestAnimationFrame(loop);
    } else {
      springs.forEach((s) => {
        s.x = s.t;
        s.v = 0;
      });
      render();
      raf = 0;
    }
  }

  function cleanupMobile() {
    nav.style.removeProperty('width');
    document.documentElement.style.removeProperty('--rail');
    if (col) col.style.removeProperty('right');
    if (colSvg) colSvg.style.removeProperty('transform');
    if (logo) logo.style.removeProperty('opacity');
    if (glider) glider.style.removeProperty('transform');
    labels.forEach((l) => {
      l.el.style.removeProperty('opacity');
      l.el.style.removeProperty('transform');
      l.el.style.removeProperty('visibility');
    });
  }

  desktopQuery.addEventListener('change', (e) => {
    if (e.matches) {
      syncGliderTarget(true);
      render();
    } else {
      cancelAnimationFrame(raf);
      raf = 0;
      cleanupMobile();
    }
  });

  collectLabels();
  syncGliderTarget(true);
  document.documentElement.classList.toggle('sidebar-collapsed', !open);
  if (desktopQuery.matches) {
    render();
  }
}

export function typewrite(element, text) {
  if (!element || element.dataset.typed || !text) return;
  element.dataset.typed = '1'; if (reduced()) { element.textContent = text; return; }
  element.textContent = ''; element.classList.add('typewriter'); let index = 0;
  const tick = () => { element.textContent = text.slice(0, ++index); if (index < text.length) setTimeout(tick, /[,.—]/.test(text[index - 1]) ? 220 : 32); else setTimeout(() => element.classList.remove('typewriter'), 700); };
  setTimeout(tick, 180);
}

export function cycleTypewriter(element, phrases) {
  if (!element || element.dataset.cycling || !phrases?.length) return;
  element.dataset.cycling = '1'; if (reduced()) { element.textContent = phrases[0]; return; }
  let phrase = 0, shown = 0, removing = false;
  element.classList.add('typewriter');
  const tick = () => {
    const text = phrases[phrase];
    shown += removing ? -1 : 1;
    element.textContent = text.slice(0, Math.max(0, shown));
    if (!removing && shown === text.length) { removing = true; setTimeout(tick, 2200); return; }
    if (removing && shown === 0) { removing = false; phrase = (phrase + 1) % phrases.length; setTimeout(tick, 260); return; }
    setTimeout(tick, removing ? 18 : /[,.—]/.test(text[shown - 1]) ? 190 : 38);
  };
  element.textContent = ''; setTimeout(tick, 180);
}
