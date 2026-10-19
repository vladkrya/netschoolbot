// Ретро-сайт по желанию: сайт netschool-retro целиком (страница /retro,
// app/web/retro.py). Дизайн — faustyu1, github.com/faustyu1/netschool-retro.
// Включён — приложение при запуске уходит туда; «Выйти» там возвращает сюда.
'use strict';

import { base, demo, token, unlock } from './core.js';

export const RETRO_AUTHOR_URL = 'https://github.com/faustyu1/netschool-retro';

/** Включён ли ретро-сайт. Ретро-страница вернула сюда (вход, PIN) — в эту
 *  сессию обратно не уходим, иначе ошибка гоняла бы по кругу. */
export function wantsRetro() {
  try {
    return localStorage.getItem('ns-retro') === 'on' && sessionStorage.getItem('ns-retro-skip') !== '1';
  } catch {
    return false;
  }
}

/** На ретро-страницу. Разблокировку PIN она берёт из sessionStorage этой вкладки. */
export function openRetro() {
  try {
    sessionStorage.removeItem('ns-retro-skip');
    if (unlock()) sessionStorage.setItem('ns-retro-unlock', unlock());
  } catch { /* приватный режим — PIN спросят ещё раз */ }
  const query = demo ? '?demo=1' : token ? `?token=${encodeURIComponent(token)}` : '';
  location.replace(`${base}/retro${query}`);
}
