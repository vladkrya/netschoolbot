// Spring presets copied from Motion Studies: caret 700/32, morph 300/20,
// pop 520/16 and panel 260/24. They are expressed in CSS below.
'use strict';

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches || document.documentElement.classList.contains('no-motion');

export function enhanceAuthMotion(root) {
  if (!root || root.dataset.motionReady) return;
  root.dataset.motionReady = '1';
  root.querySelectorAll('input.field').forEach((input) => {
    input.classList.add('spring-field');
    input.addEventListener('focus', () => input.classList.add('spring-focus'));
    input.addEventListener('blur', () => input.classList.remove('spring-focus'));
    if (input.type === 'email') {
      const validate = () => input.classList.toggle('spring-invalid', input.value.length > 0 && !input.validity.valid);
      input.addEventListener('input', validate); input.addEventListener('blur', validate); validate();
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

// Exact OTP interaction model: one real input remains the source of truth;
// boxes are visual only, so paste, autofill and backspace can never lose code.
function otpBoxes(input) {
  if (input.parentElement?.classList.contains('motion-otp')) return;
  const count = Number(input.maxLength) || 6;
  const wrap = document.createElement('div'); wrap.className = 'motion-otp'; wrap.style.setProperty('--otp-count', count);
  input.before(wrap); wrap.append(input); input.classList.add('motion-otp-input');
  const boxes = Array.from({ length: count }, () => { const box = document.createElement('span'); box.className = 'motion-otp-box'; box.setAttribute('aria-hidden', 'true'); wrap.append(box); return box; });
  const sync = () => {
    const value = input.value.replace(/\D/g, '').slice(0, count);
    if (input.value !== value) input.value = value;
    boxes.forEach((box, index) => {
      const next = value[index] || '';
      if (box.textContent !== next && next) { box.textContent = next; box.animate?.([{ transform: 'translateY(-14px)', opacity: 0 }, { transform: 'translateY(0)', opacity: 1 }], { duration: 380, easing: 'cubic-bezier(.34,1.56,.64,1)' }); }
      else if (!next) box.textContent = '';
      box.classList.toggle('active', index === Math.min(value.length, count - 1) && value.length < count);
    });
    wrap.classList.toggle('complete', value.length === count);
  };
  input.addEventListener('input', sync); input.addEventListener('focus', sync); wrap.addEventListener('click', () => input.focus()); sync();
}

function passwordMeter(input) {
  if (input.nextElementSibling?.classList.contains('password-meter')) return;
  const meter = document.createElement('div');
  meter.className = 'password-meter';
  meter.innerHTML = '<span class="password-bars"><i></i><i></i><i></i><i></i></span><span class="password-word" aria-live="polite">Введите не менее 8 символов</span>';
  input.insertAdjacentElement('afterend', meter);
  const update = () => {
    const value = input.value;
    const score = [value.length >= 8, /[a-zа-я]/i.test(value) && /[A-ZА-Я]/.test(value), /\\d/.test(value), /[^\\w\\s]/.test(value)].filter(Boolean).length;
    meter.dataset.level = String(value ? Math.max(1, score) : 0);
    meter.querySelector('.password-word').textContent = !value ? 'Введите не менее 8 символов' : score < 2 ? 'Слабый пароль' : score < 3 ? 'Можно надёжнее' : score < 4 ? 'Хороший пароль' : 'Надёжный пароль';
  };
  input.addEventListener('input', update); update();
}

export function initSidebarCollapse() {
  const nav = document.getElementById('bottomNav');
  if (!nav || nav.querySelector('.nav-collapse')) return;
  const button = document.createElement('button');
  button.className = 'nav-collapse'; button.type = 'button'; button.innerHTML = '<span aria-hidden="true">‹</span>';
  const sync = (closed) => { document.documentElement.classList.toggle('sidebar-collapsed', closed); button.setAttribute('aria-expanded', String(!closed)); button.title = closed ? 'Развернуть панель' : 'Свернуть панель'; button.setAttribute('aria-label', button.title); };
  sync(localStorage.getItem('ns-sidebar-collapsed') === '1');
  button.addEventListener('click', () => { const closed = !document.documentElement.classList.contains('sidebar-collapsed'); try { localStorage.setItem('ns-sidebar-collapsed', closed ? '1' : '0'); } catch {} sync(closed); });
  nav.append(button);
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
