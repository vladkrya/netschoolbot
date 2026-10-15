// Выдвижная панель. Одна на всё приложение: в прежней версии их было
// восемь штук в разметке, каждая со своим идентификатором и обработчиком.
'use strict';

import { $, stillMotion } from './core.js';

let onClose = null;
// Шторка уезжает вниз, а не пропадает разом; прячется по окончании.
const CLOSE_MS = 220;
let closing = 0;

export function openSheet(title, body, options = {}) {
  clearTimeout(closing);
  $('sheet').classList.remove('closing');
  $('sheetTitle').textContent = title;
  $('sheetBody').innerHTML = body;
  $('sheet').hidden = false;
  document.body.style.overflow = 'hidden';
  onClose = options.onClose || null;
  return $('sheetBody');
}

/** Шторка открыта и не уезжает — для «назад» и жестов. */
export function sheetOpen() {
  const sheet = $('sheet');
  return !sheet.hidden && !sheet.classList.contains('closing');
}

export function closeSheet() {
  const sheet = $('sheet');
  if (!sheetOpen()) return;
  document.body.style.overflow = '';
  if (onClose) {
    const handler = onClose;
    onClose = null;
    handler();
  }
  // «Закрыта» — когда правда спрятана: кто ждёт этого события, тут же
  // проверяет hidden и иначе ждал бы вечно.
  const done = () => {
    sheet.hidden = true;
    sheet.classList.remove('closing');
    window.dispatchEvent(new Event('netschool:sheetclosed'));
  };
  if (stillMotion()) {
    done();
    return;
  }
  sheet.classList.add('closing');
  clearTimeout(closing);
  closing = setTimeout(done, CLOSE_MS);
}

export function initSheet() {
  $('sheetClose').addEventListener('click', closeSheet);
  // Клик по затемнению — тоже закрытие, но только по нему самому,
  // иначе панель закрывалась бы при любом касании внутри неё.
  $('sheet').addEventListener('click', (event) => {
    if (event.target === $('sheet')) closeSheet();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && sheetOpen()) closeSheet();
  });
}
