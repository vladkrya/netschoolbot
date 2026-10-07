import { tap, notify } from './haptics.js';

const LOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="4"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/><circle cx="12" cy="15" r="1"/></svg>';
const BACK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m8 5-6 7 6 7h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2Z"/><path d="m11 9 6 6m0-6-6 6"/></svg>';

// Один пинпад для входа, задания и подтверждения PIN. Цифры только в памяти.
export function mountPinpad(root, complete, { hint = 'Введите 4 цифры', biometric = null } = {}) {
  root.classList.add('pin-screen');
  const field = root.querySelector('.lock-fields');
  field.innerHTML = `<div class="pin-emblem">${LOCK}</div>
    <p class="pin-hint"></p><div class="pin-dots" aria-hidden="true">${'<i></i>'.repeat(4)}</div>
    <span class="pin-progress" role="status" aria-live="polite">Введено 0 из 4 цифр</span>
    <div class="pin-grid" role="group" aria-label="Цифровая клавиатура">${Array.from({length: 9}, (_, i) =>
      `<button type="button" class="pin-key" data-digit="${i + 1}" aria-label="${i + 1}">${i + 1}</button>`).join('')}
      <span class="pin-spacer" aria-hidden="true"></span>
      <button type="button" class="pin-key" data-digit="0" aria-label="0">0</button>
      <button type="button" class="pin-key pin-aux" data-delete aria-label="Удалить последнюю цифру">${BACK}</button>
    </div><button type="button" class="pin-biometric" data-biometric hidden></button>`;
  root.querySelector('h2').before(field.querySelector('.pin-emblem'));
  const dots = field.querySelectorAll('.pin-dots i');
  let value = '', busy = false, disposed = false;
  const paint = () => {
    dots.forEach((dot, i) => dot.classList.toggle('filled', i < value.length));
    field.querySelector('.pin-progress').textContent = busy ? 'Проверяем код' : `Введено ${value.length} из 4 цифр`;
    field.querySelectorAll('button').forEach(button => { button.disabled = busy; });
  };
  const reset = (message = hint) => {
    value = ''; field.querySelector('.pin-hint').textContent = message; paint();
  };
  const failure = () => {
    notify('error');
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
  async function enter(digit) {
    if (disposed || busy || value.length >= 4) return;
    press(field.querySelector(`[data-digit="${digit}"]`));
    root.querySelector('.lock-error').textContent = '';
    value += digit; tap('light'); paint();
    if (value.length !== 4) return;
    busy = true; paint();
    const submitted = value;
    try { await complete(submitted, {reset, failure}); }
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
    button.hidden = false; button.textContent = `Войти с ${biometric.label}`;
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
  return { reset, setBiometric, destroy() {
    value = ''; disposed = true;
    root.removeEventListener('keydown', keydown); root.removeEventListener('paste', paste);
  }};
}
