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
  });
  root.querySelectorAll('input[type="password"][autocomplete="new-password"]').forEach(passwordMeter);
  root.querySelectorAll('input[autocomplete="one-time-code"]').forEach((input) => {
    input.classList.add('otp-field');
    input.setAttribute('aria-label', input.getAttribute('aria-label') || 'Код подтверждения');
    input.addEventListener('input', () => input.classList.toggle('otp-complete', Boolean(input.maxLength > 0 && input.value.length >= input.maxLength)));
  });
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
