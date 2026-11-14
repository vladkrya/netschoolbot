// Spring presets and animation engine based on Motion Studies:
// Caret (700 32), Morph/pill (260 24), Pop/OTP digits (520 16), Settle (280 22),
// Fade/slide (420 32), Stream words (340 26), Sidebar (260 24 / 300 22 / 380 28).
'use strict';

import { base } from './core.js';

export const reduced = () =>
  (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches) ||
  document.documentElement.classList.contains('no-motion');

export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

export const S = (x, k = 400, c = 30) => ({ x, v: 0, t: x, k, c });

export const tune = (s, k, c) => {
  s.k = k;
  s.c = reduced() ? 2 * Math.sqrt(k) : c;
};

export const step = (s, dt, instant = false) => {
  if (instant) {
    s.x = s.t;
    s.v = 0;
    return;
  }
  const a = -s.k * (s.x - s.t) - s.c * s.v;
  s.v += a * dt;
  s.x += s.v * dt;
};

/* ─────────────────────────── File Drop Component ─────────────────────────── */
// Designbookmark's File Drop:
// Zone wakes up when a file comes near (dragover), lifts fill and bobs arrow.
// On drop / pick: file shrinks into circle, which gulps (CS 280/22), ring fills
// around rolling percentage (0-100%), text slides & cross-fades (420/32),
// turns green with animated checkmark on completion, then settles back to idle.

export function initFileDrop(zone, onFiles) {
  if (!zone || zone.dataset.dropReady) return;
  zone.dataset.dropReady = '1';

  let core = zone.querySelector('.mail-file-core');
  if (!core) {
    core = document.createElement('div');
    core.className = 'mail-file-core';
    zone.prepend(core);
  }

  let g = core.querySelector('.g');
  if (!g) {
    g = document.createElement('i');
    g.className = 'g';
    core.appendChild(g);
  }

  let ring = core.querySelector('.ring');
  if (!ring) {
    core.insertAdjacentHTML('beforeend', `
      <svg class="ring" viewBox="0 0 72 72" fill="none" stroke-width="3" stroke-linecap="round">
        <circle class="track trk" cx="36" cy="36" r="33"/>
        <circle class="arc" cx="36" cy="36" r="33"/>
      </svg>
      <span class="ic up"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5"/></svg></span>
      <span class="ic pct">0</span>
      <span class="ic ok"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path class="tick" d="M5 12.5l4.5 4.5L19 7.5"/></svg></span>
    `);
  }

  const arc = core.querySelector('.arc');
  const up = core.querySelector('.ic.up');
  const pct = core.querySelector('.ic.pct');
  const ok = core.querySelector('.ic.ok');
  const tick = core.querySelector('.tick');

  let titleLine = zone.querySelector('.drop-line.t');
  if (!titleLine) {
    const existingTitle = zone.querySelector('.drop-title');
    if (existingTitle) {
      titleLine = document.createElement('div');
      titleLine.className = 'drop-line t';
      titleLine.innerHTML = `<span>${existingTitle.textContent.trim() || 'Перетащите файл сюда'}</span>`;
      existingTitle.replaceWith(titleLine);
    } else {
      titleLine = document.createElement('div');
      titleLine.className = 'drop-line t';
      titleLine.innerHTML = '<span>Перетащите файл сюда</span>';
      zone.appendChild(titleLine);
    }
  }

  let subLine = zone.querySelector('.drop-line.s');
  if (!subLine) {
    const existingSub = zone.querySelector('.drop-sub');
    if (existingSub) {
      subLine = document.createElement('div');
      subLine.className = 'drop-line s';
      subLine.innerHTML = `<span>${existingSub.textContent.trim() || 'или нажмите для выбора · до 10 МБ'}</span>`;
      existingSub.replaceWith(subLine);
    } else {
      subLine = document.createElement('div');
      subLine.className = 'drop-line s';
      subLine.innerHTML = '<span>или нажмите для выбора · до 10 МБ</span>';
      zone.appendChild(subLine);
    }
  }

  const pick = zone.querySelector('input[type="file"]') || document.getElementById('composeFile');

  const IDLE_T = 'Перетащите файл сюда';
  const IDLE_S = 'или нажмите для выбора · до 10 МБ';
  const CIRC = 207.35; // 2 * PI * 33

  // Springs from tokens.css:
  // ZS (280/22), ZH (420/32), CS (280/22), UP (420/32), PO (420/32),
  // RO (420/32), G (280/22), K (280/22), T (420/32), S (420/32).
  const ZS = S(1, 280, 22);
  const ZH = S(0, 420, 32);
  const CS = S(1, 280, 22);
  const UP = S(1, 420, 32);
  const PO = S(0, 420, 32);
  const RO = S(0, 420, 32);
  const G = S(0, 280, 22);
  const K = S(0, 280, 22);

  const T_line = { el: titleLine, cur: titleLine?.querySelector('span'), old: null, o: S(1, 420, 32) };
  const S_line = { el: subLine, cur: subLine?.querySelector('span'), old: null, o: S(1, 420, 32) };

  function say(line, text) {
    if (!line.el || !line.cur || line.cur.textContent === text) return;
    if (line.old) line.old.remove();
    line.old = line.cur;
    const fresh = document.createElement('span');
    fresh.textContent = text;
    line.el.appendChild(fresh);
    line.cur = fresh;
    line.o.x = 0;
    line.o.t = 1;
    line.o.v = 0;
    wake();
  }

  let phase = 'idle'; // idle | upload | done
  let progress = 0;
  let uploadTimer = 0;
  let totalBytes = 0;
  let fileName = '';
  let clock = 0;
  let overAt = -9;
  let resetTimeout = 0;

  function fmtBytes(b) {
    if (b < 1024) return b + ' Б';
    if (b < 1024 * 1024) return (b / 1024).toFixed(1).replace('.0', '') + ' КБ';
    return (b / (1024 * 1024)).toFixed(1).replace('.0', '') + ' МБ';
  }

  function startUploadAnim(name, size) {
    phase = 'upload';
    progress = 0;
    uploadTimer = 0;
    totalBytes = size || 1;
    fileName = name || 'файл';

    clearTimeout(resetTimeout);
    CS.x = 0.84;
    UP.t = 0;
    PO.t = 1;
    RO.t = 1;
    G.t = 0;
    K.t = 0;

    say(T_line, 'Загружаем ' + fileName);
    say(S_line, '0 КБ из ' + fmtBytes(totalBytes));
    wake();
  }

  function completeUploadAnim() {
    phase = 'done';
    progress = 1;
    PO.t = 0;
    RO.t = 0;
    G.t = 1;
    K.t = 1;
    CS.x = 0.84;

    say(T_line, 'Файл прикреплён');
    say(S_line, fileName + ' · ' + fmtBytes(totalBytes));
    wake();

    clearTimeout(resetTimeout);
    resetTimeout = setTimeout(() => {
      resetUploadAnim();
    }, 2600);
  }

  function resetUploadAnim() {
    phase = 'idle';
    progress = 0;
    G.t = 0;
    K.t = 0;
    UP.t = 1;
    PO.t = 0;
    RO.t = 0;
    say(T_line, IDLE_T);
    say(S_line, IDLE_S);
    wake();
  }

  function handleFiles(fileList) {
    if (!fileList || !fileList.length) return;
    const files = Array.from(fileList);
    const first = files[0];
    const totalSize = files.reduce((acc, f) => acc + (f.size || 0), 0);
    const label = files.length > 1 ? `${files.length} файлов` : first.name;
    startUploadAnim(label, totalSize);
    if (typeof onFiles === 'function') {
      onFiles(files);
    }
  }

  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    overAt = clock;
    ZH.t = 1;
    wake();
  });

  zone.addEventListener('dragenter', (e) => {
    e.preventDefault();
    overAt = clock;
    ZH.t = 1;
    wake();
  });

  zone.addEventListener('dragleave', (e) => {
    if (!zone.contains(e.relatedTarget)) {
      ZH.t = 0;
      wake();
    }
  });

  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    ZH.t = 0;
    overAt = -9;
    handleFiles(e.dataTransfer?.files);
  });

  zone.addEventListener('click', (e) => {
    if (pick && e.target !== pick) {
      pick.click();
    }
  });

  zone.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && pick && e.target !== pick) {
      e.preventDefault();
      pick.click();
    }
  });

  pick?.addEventListener('change', () => {
    handleFiles(pick.files);
    pick.value = '';
  });

  zone.addEventListener('pointerdown', () => {
    ZS.x = 0.975;
    wake();
  });

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
    clock += dt;

    if (phase === 'upload') {
      uploadTimer += dt;
      const dur = 1.35;
      progress = clamp(uploadTimer / dur, 0, 1);
      if (pct) pct.textContent = String(Math.round(progress * 100));
      if (S_line.cur) {
        S_line.cur.textContent = fmtBytes(progress * totalBytes) + ' из ' + fmtBytes(totalBytes);
      }
      if (uploadTimer >= dur) {
        completeUploadAnim();
      }
    }

    const near = clock - overAt < 0.2;
    ZH.t = phase !== 'upload' && phase !== 'done' && near ? 1 : 0;

    const isRed = reduced();
    const springs = [ZS, ZH, CS, UP, PO, RO, G, K, T_line.o, S_line.o];
    for (let k = 0; k < 4; k++) {
      springs.forEach((s) => step(s, dt / 4, isRed));
    }

    // Render step
    const h = clamp(ZH.x, 0, 1);
    zone.style.setProperty('--h', h.toFixed(3));
    zone.style.transform = `scale(${(ZS.x * (1 + 0.025 * h)).toFixed(4)})`;
    core.style.transform = `translateY(${(-5 * h).toFixed(2)}px) scale(${CS.x.toFixed(4)})`;

    const bob = -3.5 * h * (0.5 + 0.5 * Math.sin(clock * 7));
    if (up) {
      up.style.opacity = clamp(UP.x, 0, 1).toFixed(3);
      up.style.transform = `translateY(${(bob + (1 - UP.x) * -12).toFixed(2)}px) scale(${(0.6 + 0.4 * UP.x).toFixed(3)})`;
    }
    if (pct) {
      pct.style.opacity = clamp(PO.x, 0, 1).toFixed(3);
      pct.style.transform = `scale(${(0.7 + 0.3 * PO.x).toFixed(3)})`;
    }
    if (ring && arc) {
      ring.style.opacity = clamp(RO.x, 0, 1).toFixed(3);
      arc.style.strokeDashoffset = (CIRC * (1 - progress)).toFixed(2);
    }
    if (g) {
      g.style.transform = `scale(${Math.max(0, G.x).toFixed(4)})`;
    }
    if (ok && tick) {
      ok.style.opacity = clamp(K.x * 4, 0, 1).toFixed(3);
      tick.style.strokeDashoffset = (24 * (1 - clamp(K.x, 0, 1))).toFixed(2);
    }

    [T_line, S_line].forEach((l) => {
      const o = clamp(l.o.x, 0, 1);
      if (l.cur) {
        l.cur.style.opacity = clamp((o - 0.35) / 0.65, 0, 1).toFixed(3);
        l.cur.style.transform = `translateY(${((1 - o) * 16).toFixed(2)}px)`;
      }
      if (l.old) {
        if (o > 0.985) {
          l.old.remove();
          l.old = null;
        } else {
          l.old.style.opacity = clamp(1 - o * 1.8, 0, 1).toFixed(3);
          l.old.style.transform = `translateY(${(-o * 16).toFixed(2)}px)`;
        }
      }
    });

    const isMoving = springs.some((s) => Math.abs(s.x - s.t) > 0.005 || Math.abs(s.v) > 0.005);
    if (isMoving || phase === 'upload' || h > 0.001) {
      raf = requestAnimationFrame(loop);
    } else {
      springs.forEach((s) => {
        s.x = s.t;
        s.v = 0;
      });
      raf = 0;
    }
  }

  wake();

  return {
    startUpload: startUploadAnim,
    completeUpload: completeUploadAnim,
    reset: resetUploadAnim,
    get currentTitle() { return T_line.cur?.textContent || ''; },
    get currentSub() { return S_line.cur?.textContent || ''; },
  };
}

/* ─────────────────────────── Springy Input ─────────────────────────── */
// Designbookmark's Springy Input:
// Caret spring 700/32 with velocity stretching, idle blinking,
// entering/leaving/rejection character physics, eye password toggle,
// and horizontal scrolling keeping text in view.

export function springyInput(input) {
  if (!input || input.dataset.springyReady) return;
  input.dataset.springyReady = '1';

  const shell = document.createElement('span');
  shell.className = 'springy-email';

  const layer = document.createElement('span');
  layer.className = 'springy-email-layer';

  const caret = document.createElement('span');
  caret.className = 'springy-email-caret';

  input.before(shell);
  shell.append(input, layer, caret);

  let eyeBtn = null;
  if (input.type === 'password' || input.dataset.eyeToggle) {
    eyeBtn = document.createElement('button');
    eyeBtn.type = 'button';
    eyeBtn.className = 'springy-eye';
    eyeBtn.setAttribute('aria-label', 'Показать пароль');
    eyeBtn.setAttribute('aria-pressed', 'false');
    eyeBtn.tabIndex = -1;
    eyeBtn.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.9">
        <path class="eye-lid" d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z"/>
        <circle class="eye-pupil" cx="12" cy="12" r="2.8"/>
        <path class="eye-slash" d="M4.5 4.5l15 15"/>
      </svg>
    `;
    shell.appendChild(eyeBtn);
  }

  let previous = '';
  let focused = document.activeElement === input;
  let x = 0, velocity = 0, target = 0, frame = 0, blinkTimer = 0, scroll = 0;
  let lastMove = performance.now();

  const glyph = (char) => (input.type === 'password' ? '•' : (char === ' ' ? '\u00a0' : char));

  const spans = () => [...layer.querySelectorAll('.springy-char:not(.leaving)')];

  const positionCaret = () => {
    const chars = spans();
    const position = input.selectionEnd ?? chars.length;
    if (!chars.length) return 0;
    if (position < chars.length) return chars[position].offsetLeft;
    const last = chars[chars.length - 1];
    return last.offsetLeft + last.offsetWidth;
  };

  const render = () => {
    frame = 0;
    target = positionCaret();

    // Source tokens: --input-caret 700 32
    const stiffness = 700, damping = 32, dt = 1 / 60;
    const acceleration = -stiffness * (x - target) - damping * velocity;
    velocity += acceleration * dt;
    x += velocity * dt;

    // Horizontal scrolling when text is wide
    const box = Math.max(120, (layer.parentElement?.clientWidth || 280) - (eyeBtn ? 48 : 28));
    const want = Math.max(0, target - box + 12);
    scroll += (want - scroll) * 0.25;
    if (Math.abs(want - scroll) < 0.3) scroll = want;
    layer.style.transform = `translateX(${-scroll.toFixed(2)}px)`;
    input.scrollLeft = scroll;

    const speed = Math.abs(velocity);
    const stretch = Math.min(speed / 80, 7);
    caret.style.width = `${(2 + stretch).toFixed(2)}px`;
    caret.style.transform = `translateY(-50%) translateX(${(x - scroll - (velocity > 0 ? stretch : 0)).toFixed(2)}px)`;

    const isIdle = performance.now() - lastMove > 550 && speed < 5;
    caret.classList.toggle('blink', focused && isIdle);

    if (Math.abs(target - x) > 0.15 || speed > 0.15 || Math.abs(want - scroll) > 0.3) {
      frame = requestAnimationFrame(render);
    }
  };

  const moveCaret = () => {
    lastMove = performance.now();
    clearTimeout(blinkTimer);
    caret.classList.remove('blink');
    if (!frame) frame = requestAnimationFrame(render);
  };

  const sync = () => {
    const next = input.value;
    let start = 0;
    while (start < previous.length && start < next.length && previous[start] === next[start]) start++;
    let end = 0;
    while (
      end < previous.length - start &&
      end < next.length - start &&
      previous.at(-1 - end) === next.at(-1 - end)
    )
      end++;

    const current = spans();
    current.slice(start, current.length - end).forEach((span) => {
      const left = span.offsetLeft;
      span.classList.remove('entering', 'reject');
      span.style.left = `${left}px`;
      span.classList.add('leaving');
      setTimeout(() => span.remove(), 340);
    });

    const before = current[start] || null;
    [...next.slice(start, next.length - end)].forEach((char, index) => {
      const span = document.createElement('span');
      span.className = 'springy-char entering';
      span.textContent = glyph(char);
      span.style.setProperty('--delay', `${Math.min(index, 30) * 18}ms`);
      layer.insertBefore(span, before);
    });

    previous = next;
    if (input.type === 'email') {
      input.classList.toggle('spring-invalid', next.length > 0 && !input.validity.valid);
      shell.classList.toggle('spring-invalid', next.length > 0 && !input.validity.valid);
    }
    moveCaret();
  };

  const updateGlyphs = () => {
    const current = spans();
    const val = input.value;
    current.forEach((span, i) => {
      if (val[i]) span.textContent = glyph(val[i]);
    });
    moveCaret();
  };

  if (eyeBtn) {
    const eyeSlash = eyeBtn.querySelector('.eye-slash');
    const eyePupil = eyeBtn.querySelector('.eye-pupil');
    const slashLen = 22;
    if (eyeSlash) eyeSlash.style.strokeDasharray = String(slashLen);

    let isVisible = false;
    let eyeSpring = S(0, 300, 24);
    let eyeRaf = 0;

    const stepEye = () => {
      step(eyeSpring, 1 / 60, reduced());
      const e = clamp(eyeSpring.x, 0, 1);
      if (eyeSlash) eyeSlash.style.strokeDashoffset = (slashLen * (1 - e)).toFixed(2);
      if (eyePupil) eyePupil.style.transform = `scale(${(0.5 + 0.5 * e).toFixed(3)})`;
      if (Math.abs(eyeSpring.x - eyeSpring.t) > 0.01 || Math.abs(eyeSpring.v) > 0.01) {
        eyeRaf = requestAnimationFrame(stepEye);
      } else {
        eyeSpring.x = eyeSpring.t;
        eyeRaf = 0;
      }
    };

    eyeBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      isVisible = !isVisible;
      input.type = isVisible ? 'text' : 'password';
      eyeBtn.setAttribute('aria-pressed', String(isVisible));
      eyeBtn.setAttribute('aria-label', isVisible ? 'Скрыть пароль' : 'Показать пароль');
      eyeSpring.t = isVisible ? 1 : 0;
      if (!eyeRaf) eyeRaf = requestAnimationFrame(stepEye);
      updateGlyphs();
      input.focus();
    });
    stepEye();
  }

  input.addEventListener('input', sync);
  input.addEventListener('focus', () => {
    focused = true;
    input.classList.add('spring-focus');
    shell.classList.add('spring-focus');
    moveCaret();
  });
  input.addEventListener('blur', () => {
    focused = false;
    clearTimeout(blinkTimer);
    caret.classList.remove('blink');
    input.classList.remove('spring-focus');
    shell.classList.remove('spring-focus');
    moveCaret();
  });

  ['keydown', 'keyup', 'click', 'select'].forEach((event) => input.addEventListener(event, moveCaret));

  input.form?.addEventListener('submit', () => {
    if (input.validity.valid) return;
    rejectSpringyInput(input);
  }, true);

  sync();
}

export function rejectSpringyInput(input) {
  if (!(input instanceof HTMLInputElement)) return;
  input.classList.add('spring-invalid');
  const shell = input.closest('.springy-email');
  shell?.classList.add('spring-invalid');
  shell?.querySelectorAll('.springy-char:not(.leaving)').forEach((span, index) => {
    span.style.setProperty('--delay', `${index * 14}ms`);
    span.classList.remove('reject');
    void span.offsetWidth;
    span.classList.add('reject');
  });
  input.focus();
}

/* ─────────────────────────── Password Strength ─────────────────────────── */
// Designbookmark Password Strength:
// 4 bars fill with springs (260/24), staggered by 60ms.
// Walking colors: red -> orange -> yellow -> green.
// Word rises with spring (300/24).
// Subtext rules in Russian.

export function passwordMeter(input) {
  const anchor = input.closest('.springy-email') || input;
  if (anchor.nextElementSibling?.classList.contains('password-meter')) return;

  const meter = document.createElement('div');
  meter.className = 'password-meter';
  meter.innerHTML = `
    <div class="password-bars">
      <i><b></b></i><i><b></b></i><i><b></b></i><i><b></b></i>
    </div>
    <div class="password-foot">
      <span class="password-word" aria-live="polite">Слишком короткий</span>
      <span class="password-rules">8+ символов, заглавная буква и цифра</span>
    </div>
  `;
  anchor.insertAdjacentElement('afterend', meter);

  const bars = [...meter.querySelectorAll('.password-bars i b')].map((b) => ({
    el: b,
    s: S(0, 260, 24),
  }));
  const wordEl = meter.querySelector('.password-word');
  const WD = S(1, 300, 24);

  const WORDS = ['Слишком короткий', 'Слабый пароль', 'Можно надёжнее', 'Хороший пароль', 'Надёжный пароль'];
  const COLORS = ['var(--muted)', 'var(--g2, #d33)', '#ff9f0a', '#ffd23f', 'var(--g5, #24984b)'];

  let score = 0;
  let meterRaf = 0;
  let timers = [];

  function rate(val) {
    if (!val || val.length < 6) return 0;
    let s = 1;
    if (val.length >= 8 && /[a-zа-я]/i.test(val) && /[A-ZА-Я]/.test(val)) s++;
    if (/\d/.test(val)) s++;
    if (/[^A-Za-z0-9А-Яа-я\s]/.test(val) && val.length >= 10) s++;
    return s;
  }

  function renderMeter() {
    step(WD, 1 / 60, reduced());
    bars.forEach((b) => step(b.s, 1 / 60, reduced()));

    const col = COLORS[score];
    bars.forEach((b) => {
      b.el.style.width = `${(clamp(b.s.x, 0, 1.05) * 100).toFixed(1)}%`;
      b.el.style.background = col;
    });

    if (wordEl) {
      wordEl.style.color = score > 0 && input.value ? col : 'var(--muted)';
      wordEl.style.opacity = clamp(WD.x, 0, 1).toFixed(3);
      wordEl.style.transform = `translateY(${((1 - clamp(WD.x, 0, 1)) * 6).toFixed(2)}px)`;
    }

    const isMoving = Math.abs(WD.x - WD.t) > 0.01 || bars.some((b) => Math.abs(b.s.x - b.s.t) > 0.01);
    if (isMoving) {
      meterRaf = requestAnimationFrame(renderMeter);
    } else {
      meterRaf = 0;
    }
  }

  function update() {
    timers.forEach(clearTimeout);
    timers = [];

    const newScore = rate(input.value);
    score = newScore;
    meter.dataset.level = String(score);

    bars.forEach((b, i) => {
      const t = setTimeout(() => {
        b.s.t = i < score ? 1 : 0;
        if (!meterRaf) meterRaf = requestAnimationFrame(renderMeter);
      }, i * 60);
      timers.push(t);
    });

    WD.x = 0;
    WD.t = 1;
    if (wordEl) {
      wordEl.textContent = input.value ? WORDS[score] : WORDS[0];
    }
    if (!meterRaf) meterRaf = requestAnimationFrame(renderMeter);
  }

  input.addEventListener('input', update);
  update();
}

/* ─────────────────────────── OTP Code Component ─────────────────────────── */
// Designbookmark's OTP Code:
// 6 boxes with drop bounce spring (520/16, opacity 400/28).
// Active cell highlight and vertical caret (300/26).
// On full code/acceptance: gaps close (260/24), inner corners square off (14px -> 0px),
// morphs into single solid green pill (220/28) with checkmark & "Подтверждено".
// On rejection: red shake and clears.

export function otpBoxes(input) {
  if (!input || input.parentElement?.classList.contains('motion-otp')) return;

  const count = Number(input.maxLength) || 6;
  const wrap = document.createElement('div');
  wrap.className = 'motion-otp';
  wrap.style.setProperty('--otp-count', count);

  input.before(wrap);
  wrap.append(input);
  input.classList.add('motion-otp-input');

  const bx = Array.from({ length: count }, () => {
    const el = document.createElement('div');
    el.className = 'motion-otp-box';
    el.setAttribute('aria-hidden', 'true');

    const d = document.createElement('i');
    const car = document.createElement('span');
    car.className = 'car';
    el.append(d, car);
    wrap.insertBefore(el, input);

    return {
      el,
      d,
      car,
      y: S(0, 520, 16),
      o: S(0, 400, 28),
      a: S(0, 300, 26),
    };
  });

  const ok = document.createElement('div');
  ok.className = 'motion-otp-ok';
  ok.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">
      <path d="M5 12.5l4.5 4.5L19 7.5"/>
    </svg>
    <span>Подтверждено</span>
  `;
  wrap.appendChild(ok);

  const M = S(0, 260, 24);
  const G = S(0, 220, 28);

  let val = '';
  let clock = 0;
  let raf = 0;
  let last = performance.now();

  function wake() {
    if (!raf) {
      last = performance.now();
      raf = requestAnimationFrame(loop);
    }
  }

  function loop(now) {
    const dt = Math.min((now - last) / 1000, 1 / 30);
    last = now;
    clock += dt;

    const isRed = reduced();
    bx.forEach((b, i) => {
      b.a.t = G.t < 0.5 && i === Math.min(val.length, count - 1) && val.length < count ? 1 : 0;
      step(b.y, dt, isRed);
      step(b.o, dt, isRed);
      step(b.a, dt, isRed);
    });
    step(M, dt, isRed);
    step(G, dt, isRed);

    // Render
    const m = clamp(M.x, 0, 1.05);
    const g = clamp(G.x, 0, 1.05);
    const gap0 = 8;
    const gap = gap0 * (1 - clamp(m, 0, 1));
    wrap.style.gap = `${gap.toFixed(2)}px`;

    bx.forEach((b, i) => {
      const r = 14 * (1 - clamp(m, 0, 1));
      b.el.style.borderRadius =
        i === 0
          ? `14px ${r.toFixed(1)}px ${r.toFixed(1)}px 14px`
          : i === count - 1
          ? `${r.toFixed(1)}px 14px 14px ${r.toFixed(1)}px`
          : `${r.toFixed(1)}px`;

      const a = clamp(b.a.x, 0, 1);
      if (g > 0.01) {
        b.el.style.background = `color-mix(in srgb, var(--g5, #24984b) ${(clamp(g, 0, 1) * 100).toFixed(1)}%, var(--card-2))`;
        b.el.style.borderColor = 'transparent';
      } else if (a > 0.01) {
        b.el.style.background = `color-mix(in srgb, var(--primary) ${(a * 18).toFixed(1)}%, var(--card-2))`;
        b.el.style.borderColor = `color-mix(in srgb, var(--primary) ${(a * 60).toFixed(1)}%, var(--border))`;
      } else {
        b.el.style.background = '';
        b.el.style.borderColor = '';
      }

      b.d.style.opacity = (clamp(b.o.x, 0, 1) * (1 - clamp(g, 0, 1))).toFixed(3);
      b.d.style.transform = `translateY(${b.y.x.toFixed(2)}px)`;
      b.car.style.opacity = a > 0.5 && !val[i] && (clock * 1.8) % 2 < 1.2 ? '1' : '0';
    });

    ok.style.opacity = clamp(g * 1.6 - 0.4, 0, 1).toFixed(3);
    ok.style.transform = `translateY(${((1 - clamp(g, 0, 1)) * 8).toFixed(2)}px)`;

    const isMoving =
      Math.abs(M.x - M.t) > 0.01 ||
      Math.abs(G.x - G.t) > 0.01 ||
      bx.some((b) => Math.abs(b.y.x - b.y.t) > 0.05 || Math.abs(b.o.x - b.o.t) > 0.01 || Math.abs(b.a.x - b.a.t) > 0.01);

    if (isMoving || (val.length < count && G.t === 0)) {
      raf = requestAnimationFrame(loop);
    } else {
      raf = 0;
    }
  }

  const sync = () => {
    const next = input.value.replace(/\D/g, '').slice(0, count);
    for (let i = 0; i < count; i++) {
      const c = next[i] || '';
      if (c && bx[i].d.textContent !== c) {
        bx[i].d.textContent = c;
        bx[i].y.x = -14;
        bx[i].y.v = 0;
        bx[i].o.x = 0;
        bx[i].o.t = 1;
      }
      if (!c) {
        bx[i].d.textContent = '';
        bx[i].o.t = 0;
      }
    }
    val = next;
    if (input.value !== next) {
      input.value = next;
    }
    wrap.classList.toggle('complete', next.length === count);
    wake();
  };

  input.addEventListener('input', sync);
  input.addEventListener('focus', () => {
    wrap.classList.add('focused');
    sync();
  });
  input.addEventListener('blur', () => {
    wrap.classList.remove('focused');
    wake();
  });
  wrap.addEventListener('click', () => input.focus());

  wrap._motion = {
    setResult(accepted) {
      if (accepted) {
        wrap.classList.remove('rejected');
        wrap.classList.add('verified');
        M.t = 1;
        G.t = 1;
        wake();
      } else {
        wrap.classList.remove('verified');
        wrap.classList.add('rejected');
        M.t = 0;
        G.t = 0;
        wake();
        setTimeout(() => {
          input.value = '';
          input.dispatchEvent(new Event('input', { bubbles: true }));
          wrap.classList.remove('rejected');
          input.focus();
        }, 430);
      }
    },
  };

  sync();
}

export function setOtpResult(input, accepted) {
  const wrap = input?.closest?.('.motion-otp');
  if (wrap?._motion) {
    wrap._motion.setResult(accepted);
    return;
  }
  if (!wrap) return;
  if (accepted) {
    wrap.classList.remove('rejected');
    wrap.classList.add('verified');
    return;
  }
  wrap.classList.remove('verified');
  wrap.classList.add('rejected');
  setTimeout(() => {
    input.value = '';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    wrap.classList.remove('rejected');
    input.focus();
  }, 430);
}

/* ─────────────────────────── Streaming Reply ─────────────────────────── */
// Designbookmark's Streaming Reply:
// Initial shimmer "Думаю…" pill.
// As tokens arrive, words stream in: each word rises (translateY) and sharpens
// from blur on spring 340/26, while the bubble smoothly expands with spring 260/26.

export function createStreamingReply(container) {
  if (!container) return { update() {}, finish() {}, destroy() {} };

  container.classList.add('ai-streaming-wrap');
  container.innerHTML = `
    <div class="ai-stream-bubble">
      <span class="think"><span class="think-shimmer">Думаю…</span></span>
      <div class="stream-text"></div>
    </div>
  `;

  const bubble = container.querySelector('.ai-stream-bubble');
  const think = container.querySelector('.think');
  const textEl = container.querySelector('.stream-text');

  const T = S(1, 300, 30);
  const W = S(88, 260, 26);
  const Hh = S(40, 260, 26);

  let words = [];
  let renderedCount = 0;
  let raf = 0;
  let last = performance.now();
  let done = false;

  function wake() {
    if (!raf) {
      last = performance.now();
      raf = requestAnimationFrame(loop);
    }
  }

  function loop(now) {
    const dt = Math.min((now - last) / 1000, 1 / 30);
    last = now;

    const isRed = reduced();
    step(T, dt, isRed);
    step(W, dt, isRed);
    step(Hh, dt, isRed);
    words.forEach((w) => step(w.s, dt, isRed));

    if (think) {
      think.style.opacity = clamp(T.x, 0, 1).toFixed(3);
    }
    if (bubble) {
      if (W.x > 88) bubble.style.width = `${W.x.toFixed(1)}px`;
      if (Hh.x > 40) bubble.style.minHeight = `${Hh.x.toFixed(1)}px`;
    }

    words.forEach((w) => {
      const v = clamp(w.s.x, 0, 1.2);
      w.el.style.opacity = clamp(v, 0, 1).toFixed(3);
      w.el.style.transform = `translateY(${((1 - clamp(v, 0, 1)) * 6).toFixed(2)}px)`;
      w.el.style.filter = v < 0.98 && !isRed ? `blur(${((1 - clamp(v, 0, 1)) * 4).toFixed(1)}px)` : 'none';
    });

    const isMoving =
      Math.abs(T.x - T.t) > 0.01 ||
      Math.abs(W.x - W.t) > 0.5 ||
      Math.abs(Hh.x - Hh.t) > 0.5 ||
      words.some((w) => Math.abs(w.s.x - w.s.t) > 0.01);

    if (isMoving && !done) {
      raf = requestAnimationFrame(loop);
    } else {
      raf = 0;
    }
  }

  wake();

  return {
    update(fullText) {
      if (!fullText) return;
      T.t = 0; // fade out "Thinking"

      // Split into words
      const parts = fullText.split(/(\s+)/);
      for (let i = renderedCount; i < parts.length; i++) {
        const part = parts[i];
        if (!part) continue;
        if (/^\s+$/.test(part)) {
          textEl.appendChild(document.createTextNode(part));
        } else {
          const span = document.createElement('span');
          span.className = 'wd';
          span.textContent = part;
          span.style.opacity = '0';
          textEl.appendChild(span);

          const wObj = { el: span, s: S(0, 340, 26) };
          wObj.s.t = 1;
          words.push(wObj);
        }
      }
      renderedCount = parts.length;

      // Adjust height target
      if (textEl) {
        Hh.t = Math.max(40, textEl.offsetHeight + 18);
        W.t = Math.min(container.parentElement?.clientWidth || 360, textEl.offsetWidth + 28);
      }
      wake();
    },
    finish() {
      done = true;
      T.x = 0;
      T.t = 0;
      words.forEach((w) => {
        w.s.x = 1;
        w.s.t = 1;
        w.el.style.opacity = '1';
        w.el.style.transform = 'none';
        w.el.style.filter = 'none';
      });
      if (bubble) {
        bubble.style.width = '';
        bubble.style.minHeight = '';
      }
    },
    destroy() {
      cancelAnimationFrame(raf);
      raf = 0;
    },
  };
}

/* ─────────────────────────── Typewriter ─────────────────────────── */
// Kokonut UI Typewriter implementation:
// Natural typing delay variance (hesitations & bursts),
// realistic keyboard typos (Russian/English) with correction,
// select-to-delete animation (Shift+Home highlight tint right-to-left),
// solid typing caret blinking once idle (500ms),
// and full screen reader accessibility.

const KEY_ROWS = [
  'йцукенгшщзхъ',
  'фывапролджэ',
  'ячсмитьбю',
  'qwertyuiop',
  'asdfghjkl',
  'zxcvbnm',
];

function getNeighbour(char) {
  const lower = char.toLowerCase();
  for (const row of KEY_ROWS) {
    const idx = row.indexOf(lower);
    if (idx !== -1) {
      const candidates = [];
      if (idx > 0) candidates.push(row[idx - 1]);
      if (idx < row.length - 1) candidates.push(row[idx + 1]);
      if (candidates.length) {
        const picked = candidates[Math.floor(Math.random() * candidates.length)];
        return char === lower ? picked : picked.toUpperCase();
      }
    }
  }
  return '';
}

export function typewrite(element, text) {
  if (!element || element.dataset.typed || !text) return;
  element.dataset.typed = '1';
  if (reduced()) {
    element.textContent = text;
    return;
  }
  element.textContent = '';
  element.classList.add('typewriter');
  let index = 0;
  const tick = () => {
    element.textContent = text.slice(0, ++index);
    if (index < text.length) setTimeout(tick, /[,.—]/.test(text[index - 1]) ? 220 : 34);
    else setTimeout(() => element.classList.remove('typewriter'), 700);
  };
  setTimeout(tick, 180);
}

export function cycleTypewriter(element, phrases, options = {}) {
  if (!element || element.dataset.cycling || !phrases?.length) return { stop() {} };
  element.dataset.cycling = '1';

  if (reduced()) {
    element.textContent = phrases[0];
    return { stop() {} };
  }

  const {
    typingSpeed = 46,
    deleteSpeed = 26,
    pauseBeforeDelete = 1800,
    loopDelay = 1200,
    naturalVariance = false,
    typos = false,
    // A normal backspace is clearer than the selection sweep on a large
    // headline and always reads left-to-right while typing.
    deleteMode = 'backspace', // 'select' | 'backspace'
  } = options;

  element.setAttribute('aria-label', phrases.join(', '));
  element.innerHTML = `
    <span class="typewriter-screen" aria-hidden="true">
      <span class="typewriter-kept"></span>
      <span class="typewriter-caret" data-idle="false"></span>
      <span class="typewriter-selected"></span>
    </span>
  `;

  const keptEl = element.querySelector('.typewriter-kept');
  const caretEl = element.querySelector('.typewriter-caret');
  const selEl = element.querySelector('.typewriter-selected');

  let phraseIndex = 0;
  let idleTimer = 0;
  let running = true;

  const markIdle = () => {
    clearTimeout(idleTimer);
    if (caretEl) caretEl.dataset.idle = 'false';
    idleTimer = setTimeout(() => {
      if (caretEl) caretEl.dataset.idle = 'true';
    }, 500);
  };

  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function getDelay() {
    if (!naturalVariance) return typingSpeed;
    const r = Math.random();
    if (r < 0.1) return typingSpeed * 2;
    if (r > 0.9) return typingSpeed * 0.5;
    return typingSpeed * (0.6 + Math.random() * 0.8);
  }

  async function runCycle() {
    while (running) {
      const phrase = phrases[phraseIndex];
      let typed = '';
      keptEl.textContent = '';
      selEl.textContent = '';
      markIdle();

      // Type the phrase
      for (let i = 0; i < phrase.length && running; i++) {
        const targetChar = phrase[i];

        // 4% chance of realistic typo on alphabetic characters
        if (typos && /[a-zа-я]/i.test(targetChar) && Math.random() < 0.04) {
          const wrong = getNeighbour(targetChar) || targetChar;
          keptEl.textContent = typed + wrong;
          markIdle();
          await wait(240); // pause upon noticing typo
          if (!running) break;
          keptEl.textContent = typed; // backspace
          markIdle();
          await wait(140);
        }

        typed += targetChar;
        keptEl.textContent = typed;
        markIdle();
        await wait(/[,.—]/.test(targetChar) ? 190 : getDelay());
      }

      markIdle();
      await wait(pauseBeforeDelete);
      if (!running) break;

      // Erase
      if (deleteMode === 'select') {
        // Kokonut UI select sweep: sweeps right-to-left
        for (let s = 1; s <= phrase.length && running; s++) {
          keptEl.textContent = phrase.slice(0, phrase.length - s);
          selEl.textContent = phrase.slice(phrase.length - s);
          markIdle();
          await wait(18);
        }
        await wait(220); // selection hold pause
        keptEl.textContent = '';
        selEl.textContent = '';
      } else {
        // Backspace mode
        for (let s = phrase.length - 1; s >= 0 && running; s--) {
          keptEl.textContent = phrase.slice(0, s);
          markIdle();
          await wait(deleteSpeed);
        }
      }

      await wait(loopDelay);
      phraseIndex = (phraseIndex + 1) % phrases.length;
    }
  }

  runCycle().catch(() => {});

  return {
    stop() {
      running = false;
      clearTimeout(idleTimer);
    },
  };
}

/* ─────────────────────────── Form Auth Enhancer ─────────────────────────── */

export function enhanceAuthMotion(root) {
  if (!root || root.dataset.motionReady) return;
  root.dataset.motionReady = '1';

  root.querySelectorAll('input.field').forEach((input) => {
    input.classList.add('spring-field');
    if (input.type === 'email' || input.type === 'password' || input.autocomplete === 'username' || input.name === 'login') {
      springyInput(input);
    } else {
      input.addEventListener('focus', () => input.classList.add('spring-focus'));
      input.addEventListener('blur', () => input.classList.remove('spring-focus'));
    }
  });

  root.querySelectorAll('input[type="password"][autocomplete="new-password"]').forEach(passwordMeter);
  root.querySelectorAll('input[autocomplete="one-time-code"]').forEach(otpBoxes);

  root.querySelectorAll('form').forEach((form) =>
    form.addEventListener('invalid', (event) => {
      const field = event.target;
      if (field instanceof HTMLInputElement) {
        field.classList.add('spring-invalid');
        field.animate?.(
          [
            { transform: 'translateX(0)' },
            { transform: 'translateX(-7px)' },
            { transform: 'translateX(6px)' },
            { transform: 'translateX(0)' },
          ],
          { duration: 360, easing: 'cubic-bezier(.34,1.56,.64,1)' }
        );
      }
    }, true)
  );
}

/* ─────────────────────────── Desktop Sidebar Collapse ─────────────────────────── */

let updateSidebarGliderTarget = null;

export function syncSidebarGlider() {
  updateSidebarGliderTarget?.();
}

export function initSidebarCollapse() {
  const nav = document.getElementById('bottomNav');
  if (!nav) return;

  const desktopQuery = matchMedia('(min-width: 1024px)');

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
