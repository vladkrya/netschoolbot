// Тактильный отклик: как в нативном приложении, касания ощущаются.
//
// Внутри Telegram — HapticFeedback из Mini App API (работает и на iOS).
// В установленном PWA — vibrate, где он есть (Android); на iOS Safari
// вибрации из веба нет, там просто тихо.
'use strict';

import { initData, tg } from './core.js';

// Скрипт Telegram создаёт WebApp и вне Telegram — с немыми заглушками.
// Поэтому HapticFeedback берём, только если приложение открыто в Telegram.
const haptic = initData ? tg?.HapticFeedback : null;

function vibrate(pattern) {
  try {
    navigator.vibrate?.(pattern);
  } catch {
    /* вибрация недоступна — не страшно */
  }
}

/** Лёгкое касание: открыть экран, нажать кнопку. */
export function tap(style = 'light') {
  if (haptic) haptic.impactOccurred(style);
  else vibrate(style === 'light' ? 8 : 15);
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
