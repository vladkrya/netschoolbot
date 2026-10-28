// Нижняя панель. В новом дизайне какие вкладки в ней и в каком порядке —
// выбирает человек, а настройки — отдельная вкладка вместо меню ☰. В
// классическом всё как раньше: пять вкладок и меню.
//
// Набор хранится в общих настройках (prefs `tabs`), поэтому одинаков в
// Telegram и на сайте. «Настройки» убрать нельзя: иначе панель уже не
// вернуть.
'use strict';

import { $, isReadOnly } from './core.js';
import { getPref, setPref } from './prefs.js';
import { isNewDesign, navIcon } from './appearance.js';

// Подписи: классический дизайн и новый (lbl-old / lbl-new в app.css).
export const TAB_DEFS = {
  day: { icon: 'today', old: 'День', fresh: 'День' },
  schedule: { icon: 'clock', old: 'Звонки', fresh: 'Звонки' },
  assistant: { icon: 'sparkles', old: 'Помощник', fresh: 'Помощник' },
  homework: { icon: 'checklist', old: 'Д/З', fresh: 'Задания' },
  totals: { icon: 'book', old: 'Итоги', fresh: 'Оценки' },
  mail: { icon: 'chat', old: 'Сообщения', fresh: 'Сообщения' },
  games: { icon: 'game', old: 'Игры', fresh: 'Игры' },
  settings: { icon: 'gear', old: 'Настройки', fresh: 'Настройки' },
};

/** Названия для списка в настройках — полные, без сокращений. */
export const TAB_NAMES = {
  day: 'День', schedule: 'Звонки / неделя', assistant: 'ИИ-помощник', homework: 'Задания',
  totals: 'Оценки', mail: 'Сообщения (почта и объявления)', games: 'Игры', settings: 'Настройки',
};

// Вкладок в новом дизайне пять (звонки и помощник переехали на «День»), и
// все помещаются с подписями.
export const MAX_TABS = 5;

// В новом дизайне звонки и неделя — шторкой из «Дня» (кнопка с часами),
// помощник — кнопкой ✨ на «Дне»: отдельные вкладки не помещались в панель.
const MERGED_IN_NEW = new Set(['schedule', 'assistant']);

// Просмотр дневника родным: почта — переписка самого ученика.
const HIDDEN_READONLY = new Set(['mail', 'assistant']);

export function availableTabs() {
  return Object.keys(TAB_DEFS).filter((tab) => !MERGED_IN_NEW.has(tab) && !(isReadOnly() && HIDDEN_READONLY.has(tab)));
}

const CLASSIC_TABS = ['day', 'schedule', 'homework', 'totals', 'mail'];

export function defaultTabs() {
  return ['day', 'homework', 'totals', 'mail', 'settings'];
}

/** Вкладки панели: сохранённый выбор без неизвестных и недоступных. */
export function navTabs() {
  const allowed = (tabs) => (isReadOnly() ? tabs.filter((tab) => !HIDDEN_READONLY.has(tab)) : tabs);
  if (!isNewDesign()) return allowed(CLASSIC_TABS);
  const saved = getPref('tabs');
  if (!Array.isArray(saved)) return allowed(defaultTabs());
  const available = availableTabs();
  const tabs = [...new Set(saved)].filter((tab) => available.includes(tab)).slice(0, MAX_TABS);
  if (!tabs.includes('settings')) tabs.push('settings');
  return tabs.length >= 2 ? tabs : allowed(defaultTabs());
}

export function saveTabs(tabs) {
  setPref('tabs', tabs);
  renderNav();
}

export function resetTabs() {
  saveTabs(defaultTabs());
}

/** Число у значка вкладки — например, непрочитанные письма у «Почты».
 *  Хранится здесь: панель пересобирается при смене набора вкладок. */
const badges = {};
export function setMailBadge(count) {
  const unread = Math.max(0, Number(count) || 0);
  setNavBadge('mail', unread);
  // Тот же счётчик — на самом разделе «Письма» вкладки.
  const seg = document.getElementById('segMailBadge');
  if (seg) {
    seg.textContent = unread > 99 ? '99+' : String(unread);
    seg.hidden = !unread;
  }
}

function badgeHtml(tab) {
  const count = badges[tab] || 0;
  return count ? `<span class="nav-badge" aria-label="${count} новых">${count > 99 ? '99+' : count}</span>` : '';
}

export function setNavBadge(tab, count) {
  badges[tab] = count;
  const icon = $('bottomNav').querySelector(`.nav-item[data-tab="${tab}"] .ico`);
  if (!icon) return;
  // Значок не пересоздаём: иначе его анимация появления проигрывалась при
  // каждом фоновом обновлении почты, и счётчик «мигал».
  const current = icon.querySelector('.nav-badge');
  if (!count) {
    current?.remove();
  } else if (current) {
    current.textContent = count > 99 ? '99+' : String(count);
    current.setAttribute('aria-label', `${count} новых`);
  } else {
    icon.insertAdjacentHTML('beforeend', badgeHtml(tab));
  }
}

/** Пункты панели заново; линзу на активную вкладку ставит app.js. */
export function renderNav(activeTab = '') {
  const nav = $('bottomNav');
  const tabs = navTabs();
  nav.querySelectorAll('.nav-item').forEach((item) => item.remove());
  nav.insertAdjacentHTML('beforeend', tabs.map((tab) => {
    const def = TAB_DEFS[tab];
    return `<button class="nav-item${tab === activeTab ? ' active' : ''}" data-tab="${tab}" aria-label="${def.fresh}">
        <span class="ico" data-icon="${def.icon}">${navIcon(def.icon)}${badgeHtml(tab)}</span>
        <span class="lbl-old">${def.old}</span><span class="lbl-new">${def.fresh}</span></button>`;
  }).join(''));
  nav.style.setProperty('--n', tabs.length);
  nav.dataset.count = tabs.length;
  window.dispatchEvent(new CustomEvent('netschool:nav'));
}
