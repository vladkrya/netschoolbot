import { tap, notify } from './haptics.js';

const LOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="4"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/><circle cx="12" cy="15" r="1"/></svg>';
const BACK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m8 5-6 7 6 7h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2Z"/><path d="m11 9 6 6m0-6-6 6"/></svg>';

const OPEN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="4"/><path d="M8 10V7a4 4 0 0 1 7.6-1.8"/><circle cx="12" cy="15" r="1"/></svg>';
const FACE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M3 8V6a3 3 0 0 1 3-3h2M16 3h2a3 3 0 0 1 3 3v2M21 16v2a3 3 0 0 1-3 3h-2M8 21H6a3 3 0 0 1-3-3v-2"/><path d="M9 9v1.5M15 9v1.5M12 9v4h-1M9.5 16a4 4 0 0 0 5 0"/></svg>';
const FINGER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M6.5 5.5A8 8 0 0 1 20 11v1.5M4 9.5a8 8 0 0 0-.5 2.5v2M8 20.5c.6-1.5 1-3.4 1-6.5a3 3 0 0 1 6 0v1M12 14c0 3-.4 5.4-1.5 7.5M17.5 17c-.2 1.3-.6 2.6-1.1 3.6M7 15.5V12a5 5 0 0 1 8.5-3.6"/></svg>';

// Один пинпад для входа, задания и подтверждения PIN. Цифры только в памяти.
export function mountPinpad(root, complete, { hint = 'Введите 4 цифры', biometric = null, forgot = '' } = {}) {
  root.classList.add('pin-screen');
  const field = root.querySelector('.lock-fields');
  field.innerHTML = `<div class="pin-emblem">${LOCK}</div>
    <p class="pin-hint"></p><div class="pin-dots" aria-hidden="true">${'<i></i>'.repeat(4)}</div>
    <span class="pin-progress" role="status" aria-live="polite">Введено 0 из 4 цифр</span>
    <div class="pin-grid" role="group" aria-label="Цифровая клавиатура">${Array.from({length: 9}, (_, i) =>
      `<button type="button" class="pin-key" data-digit="${i + 1}" aria-label="${i + 1}">${i + 1}</button>`).join('')}
      <button type="button" class="pin-key pin-aux pin-biometric" data-biometric hidden></button>
      <button type="button" class="pin-key" data-digit="0" aria-label="0">0</button>
      <button type="button" class="pin-key pin-aux" data-delete aria-label="Удалить последнюю цифру" hidden>${BACK}</button>
    </div>${forgot ? '<button type="button" class="pin-forgot" data-forgot>Забыли PIN?</button>' : ''}`;
  root.querySelector('h2').before(field.querySelector('.pin-emblem'));
  // Ошибка — сразу под точками, где взгляд, а не под клавиатурой.
  field.querySelector('.pin-dots').after(root.querySelector('.lock-error'));
  const dots = field.querySelectorAll('.pin-dots i');
  let value = '', busy = false, disposed = false;
  const paint = () => {
    dots.forEach((dot, i) => dot.classList.toggle('filled', i < value.length));
    // Стирать нечего — клавиша не мешает (как на iPhone).
    field.querySelector('[data-delete]').hidden = !value.length;
    field.querySelector('.pin-progress').textContent = busy ? 'Проверяем код' : `Введено ${value.length} из 4 цифр`;
    field.querySelectorAll('button').forEach(button => { button.disabled = busy; });
  };
  const reset = (message = hint) => {
    value = ''; field.querySelector('.pin-hint').textContent = message; paint();
  };
  const failure = () => {
    notify('error');
    const row = field.querySelector('.pin-dots');
    row.classList.add('wrong'); setTimeout(() => row.classList.remove('wrong'), 650);
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
      field.querySelector('.pin-dots').animate([
        {transform: 'translateX(0)'}, {transform: 'translateX(-9px)'},
        {transform: 'translateX(9px)'}, {transform: 'translateX(-5px)'}, {transform: 'translateX(0)'},
      ], {duration: 320});
    }
  };
  const press = button => {
    if (!button) return;
    button.getAnimations().forEach(animation => animation.cancel());
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    button.animate(reduced ? [{opacity: .65}, {opacity: 1}] : [
      {transform: 'translateY(0) scale(1)', offset: 0},
      {transform: 'translateY(4px) scale(.91)', offset: .25},
      {transform: 'translateY(2px) scale(.95)', offset: .6},
      {transform: 'translateY(0) scale(1)', offset: 1},
    ], {duration: reduced ? 100 : 260, easing: 'cubic-bezier(.2,.8,.2,1)'});
  };
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  /** Верный код: точки по очереди вспыхивают зелёным, замок открывается. */
  const success = async () => {
    if (disposed) return;
    notify('success');
    const row = field.querySelector('.pin-dots');
    row.classList.remove('wrong'); row.classList.add('ok');
    dots.forEach(dot => dot.classList.add('filled'));
    field.querySelector('.pin-progress').textContent = 'Код верный';
    const emblem = root.querySelector('.pin-emblem');
    if (emblem) { emblem.innerHTML = OPEN; emblem.classList.add('open'); }
    if (reduced()) return;
    dots.forEach((dot, i) => dot.animate([
      { transform: 'scale(1)' }, { transform: 'scale(1.55)' }, { transform: 'scale(1)' },
    ], { duration: 300, delay: i * 55, easing: 'cubic-bezier(.3,.7,.3,1)' }));
    emblem?.animate([{ transform: 'scale(1) rotate(0)' }, { transform: 'scale(1.18) rotate(-8deg)' }, { transform: 'scale(1) rotate(0)' }],
      { duration: 380, easing: 'cubic-bezier(.3,.7,.3,1)' });
    await new Promise(resolve => setTimeout(resolve, 430));
  };
  async function enter(digit) {
    if (disposed || busy || value.length >= 4) return;
    press(field.querySelector(`[data-digit="${digit}"]`));
    root.querySelector('.lock-error').textContent = '';
    value += digit; tap('light'); paint();
    if (value.length !== 4) return;
    busy = true; paint();
    const submitted = value;
    try { await complete(submitted, {reset, failure, success}); }
    catch (error) {
      root.querySelector('.lock-error').textContent = error.message;
      failure(); value = '';
    } finally {
      value = ''; busy = false;
      if (!disposed) paint();
    }
  }
  function remove() { if (!busy && !disposed) { press(field.querySelector('[data-delete]')); value = value.slice(0, -1); tap('light'); paint(); } }
  field.querySelectorAll('[data-digit]').forEach(button => { button.onclick = () => enter(button.dataset.digit); });
  field.querySelector('[data-delete]').onclick = remove;
  function setBiometric(biometric) {
    if (disposed || !biometric) return;
    const button = field.querySelector('[data-biometric]');
    button.hidden = false;
    button.innerHTML = /face|лиц|hello/i.test(biometric.label) ? FACE : FINGER;
    button.setAttribute('aria-label', `Войти с ${biometric.label}`);
    button.onclick = async () => {
      if (disposed || busy) return;
      busy = true; paint();
      try { await biometric.run(); }
      catch (error) { root.querySelector('.lock-error').textContent = error.message; }
      finally { busy = false; if (!disposed) paint(); }
    };
  }
  if (biometric) setBiometric(biometric);
  field.querySelector('[data-forgot]')?.addEventListener('click', () => {
    root.querySelector('.lock-error').textContent = forgot;
  });
  const keydown = event => {
    if (/^[0-9]$/.test(event.key)) { event.preventDefault(); enter(event.key); }
    if (event.key === 'Backspace' || event.key === 'Delete') { event.preventDefault(); remove(); }
  };
  const paste = event => {
    const text = event.clipboardData?.getData('text') || '';
    if (!/^[0-9]{4}$/.test(text) || value || busy) return;
    event.preventDefault(); for (const digit of text) enter(digit);
  };
  root.addEventListener('keydown', keydown); root.addEventListener('paste', paste);
  root.tabIndex = -1; root.focus(); reset();
  // Появление: клавиши поднимаются волной.
  if (!reduced()) field.querySelectorAll('.pin-key').forEach((key, i) => key.animate([
    { opacity: 0, transform: 'translateY(14px) scale(.92)' }, { opacity: 1, transform: 'none' },
  ], { duration: 360, delay: 40 + Math.floor(i / 3) * 45 + (i % 3) * 15, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'backwards' }));
  return { reset, setBiometric, success, destroy() {
    value = ''; disposed = true;
    root.removeEventListener('keydown', keydown); root.removeEventListener('paste', paste);
  }};
}
