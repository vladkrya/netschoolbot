// Выдвижная панель. Одна на всё приложение: в прежней версии их было
// восемь штук в разметке, каждая со своим идентификатором и обработчиком.
'use strict';

import { $ } from './core.js';

let onClose = null;

export function openSheet(title, body, options = {}) {
  $('sheetTitle').textContent = title;
  $('sheetBody').innerHTML = body;
  $('sheet').hidden = false;
  document.body.style.overflow = 'hidden';
  onClose = options.onClose || null;
  return $('sheetBody');
}

export function closeSheet() {
  $('sheet').hidden = true;
  document.body.style.overflow = '';
  if (onClose) {
    const handler = onClose;
    onClose = null;
    handler();
  }
  window.dispatchEvent(new Event('netschool:sheetclosed'));
}

export function initSheet() {
  $('sheetClose').addEventListener('click', closeSheet);
  // Клик по затемнению — тоже закрытие, но только по нему самому,
  // иначе панель закрывалась бы при любом касании внутри неё.
  $('sheet').addEventListener('click', (event) => {
    if (event.target === $('sheet')) closeSheet();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('sheet').hidden) closeSheet();
  });
}
