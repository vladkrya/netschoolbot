// Тактильный отклик: как в нативном приложении, касания ощущаются.
//
// Внутри Telegram — HapticFeedback из Mini App API (работает и на iOS).
// В установленном PWA — vibrate, где он есть (Android). В Safari на iPhone
// vibrate нет; тактильно щёлкает только системный переключатель
// <input switch>, и с iOS 26.5 — лишь от настоящего касания пальцем, не от
// скрипта. Поэтому поверх нажимаемых элементов кладём невидимую подпись
// к такому переключателю: палец попадает в неё, и iOS щёлкает сама.
'use strict';

import { initData, tg } from './core.js';

// Скрипт Telegram создаёт WebApp и вне Telegram — с немыми заглушками.
// Поэтому HapticFeedback берём, только если приложение открыто в Telegram.
const haptic = initData ? tg?.HapticFeedback : null;

// Что щёлкает на iPhone: всё, что в приложении «проседает» от касания.
const IOS_TAPPABLE = [
  'button', '[role="button"]',
  '.nav-item', '.btn', '.chip', '.topbar-btn', '.date-arrow', '.date-title', '.screen-back',
  '.lesson-card', '.hw-card', '.mail-card', '.ann-card', '.task-block', '.tile',
  'button.tt-cell', '.cal-day', '.cal-arrow', '.calc-btn', '.fab', '.sheet-close',
  '.swatch', '.recipient', '.drawer-link', '.sched-toggle',
  // Калькулятор «что если» в итогах по предмету.
  '.whatif-add .mark-box', '.whatif-item', '.whatif-clear', '.wchip-x', '.thr-edit',
  // Значок «Д/З» в карточке урока открывает домашку — своя подпись поверх.
  '[data-hw-badge]',
].join(',');
// Внутри них не должно быть своих ссылок и полей — подпись перехватила бы касание.
const OWN_CONTROLS = 'a, input, select, textarea, button, label';

const iosSwitches = !haptic && !('vibrate' in navigator)
  && window.matchMedia?.('(pointer: coarse)').matches;

function armSwitch(host) {
  if (host.querySelector(':scope > .haptic-hit')) return;
  const controls = [...host.querySelectorAll(OWN_CONTROLS)].filter((el) => !el.closest('.haptic-hit'));
  if (controls.length) return;
  const label = document.createElement('label');
  label.className = 'haptic-hit';
  label.ariaHidden = 'true';
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.setAttribute('switch', '');
  input.tabIndex = -1;
  // Переключатель сам «кликается» вслед за подписью — второй клик не должен
  // дойти до кнопки, иначе её действие сработает дважды.
  input.addEventListener('click', (event) => event.stopPropagation());
  label.append(input);
  if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
  host.append(label);
}

function armAll(root) {
  if (root.matches?.(IOS_TAPPABLE)) armSwitch(root);
  root.querySelectorAll?.(IOS_TAPPABLE).forEach(armSwitch);
}

/** Включить щелчки Safari на iPhone. Новые карточки подхватываются сами. */
let initialized = false;
export function initIosHaptics() {
  if (initialized) return;
  initialized = true;
  // Делегирование охватывает также кнопки в новых модальных окнах и PIN.
  document.addEventListener('click', event => {
    const control = event.target.closest?.(`${IOS_TAPPABLE}, a, input.switch`);
    if (!event.isTrusted || !control || control.disabled || control.getAttribute('aria-disabled') === 'true') return;
    tap();
  }, { capture: true });
  if (!iosSwitches) return;
  armAll(document.body);
  new MutationObserver((records) => {
    for (const record of records) record.addedNodes.forEach((node) => node.nodeType === 1 && armAll(node));
  }).observe(document.body, { childList: true, subtree: true });
}

function vibrate(pattern) {
  // До первого касания браузер вибрацию запрещает и пишет ошибку в консоль.
  if (navigator.userActivation && !navigator.userActivation.hasBeenActive) return;
  try {
    // На iPhone щёлкает подпись под пальцем (см. initIosHaptics).
    navigator.vibrate?.(pattern);
  } catch {
    /* вибрация недоступна — не страшно */
  }
}

/** Лёгкое касание: открыть экран, нажать кнопку. */
let lastTap = -Infinity;
function allowTap() {
  const now = performance.now();
  // Старые обработчики уже вызывают tap/select: один отклик на одно нажатие.
  if (now - lastTap < 50) return false;
  lastTap = now;
  return true;
}
export function tap(style = 'light') {
  if (!allowTap()) return;
  if (haptic) haptic.impactOccurred(style);
  else vibrate(style === 'light' || style === 'soft' ? 8 : 15);
}

/** Смена выбора: вкладка, день, дата в календаре, переключатель. */
export function select() {
  if (!allowTap()) return;
  if (haptic) haptic.selectionChanged();
  else vibrate(5);
}

/** Итог действия: обновление, смена ребёнка, подписка на push. */
export function notify(type = 'success') {
  if (haptic) haptic.notificationOccurred(type);
  else vibrate(type === 'success' ? [10, 40, 10] : [30, 40, 30]);
}
