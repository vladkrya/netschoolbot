// Тактильный отклик: как в нативном приложении, касания ощущаются.
//
// Внутри Telegram — HapticFeedback из Mini App API (работает и на iOS).
// В установленном PWA — vibrate, где он есть (Android). На iOS Safari
// vibrate нет, но с iOS 18 переключатель <input switch> щёлкает тактильно —
// им и пользуемся (работает только внутри обработчика касания).
'use strict';

import { initData, tg } from './core.js';

// Скрипт Telegram создаёт WebApp и вне Telegram — с немыми заглушками.
// Поэтому HapticFeedback берём, только если приложение открыто в Telegram.
const haptic = initData ? tg?.HapticFeedback : null;

function iosTick() {
  const label = document.createElement('label');
  label.ariaHidden = 'true';
  label.style.display = 'none';
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.setAttribute('switch', '');
  label.append(input);
  document.head.append(label);
  label.click();
  label.remove();
}

function vibrate(pattern) {
  // До первого касания браузер вибрацию запрещает и пишет ошибку в консоль.
  if (navigator.userActivation && !navigator.userActivation.hasBeenActive) return;
  try {
    if (navigator.vibrate) navigator.vibrate(pattern);
    else iosTick();
  } catch {
    /* вибрация недоступна — не страшно */
  }
}

/** Лёгкое касание: открыть экран, нажать кнопку. */
export function tap(style = 'light') {
  if (haptic) haptic.impactOccurred(style);
  else vibrate(style === 'light' || style === 'soft' ? 8 : 15);
}

/** Смена выбора: вкладка, день, дата в календаре, переключатель. */
export function select() {
  if (haptic) haptic.selectionChanged();
  else vibrate(5);
}

/** Итог действия: обновление, смена ребёнка, подписка на push. */
export function notify(type = 'success') {
  if (haptic) haptic.notificationOccurred(type);
  else vibrate(type === 'success' ? [10, 40, 10] : [30, 40, 30]);
}
