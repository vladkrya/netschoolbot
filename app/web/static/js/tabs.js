// Нижняя панель. В новом дизайне какие вкладки в ней и в каком порядке —
// выбирает человек, а настройки — отдельная вкладка вместо меню ☰. В
// классическом всё как раньше: пять вкладок и меню.
//
// Набор хранится в общих настройках (prefs `tabs`), поэтому одинаков в
// Telegram и на сайте. «Настройки» убрать нельзя: иначе панель уже не
// вернуть. Без ИИ-помощника (нет ключа на сервере или демо) его вкладка
// просто не показывается.
'use strict';

import { $, assistantEnabled } from './core.js';
import { ICONS } from './icons.js';
import { getPref, setPref } from './prefs.js';
import { isNewDesign } from './appearance.js';

// Подписи: классический дизайн и новый (lbl-old / lbl-new в app.css).
export const TAB_DEFS = {
  day: { icon: 'today', old: 'День', fresh: 'День' },
  schedule: { icon: 'clock', old: 'Звонки', fresh: 'Звонки' },
  assistant: { icon: 'sparkles', old: 'Помощник', fresh: 'Помощник' },
  homework: { icon: 'checklist', old: 'Д/З', fresh: 'Задания' },
  totals: { icon: 'book', old: 'Итоги', fresh: 'Оценки' },
  mail: { icon: 'mail', old: 'Сообщения', fresh: 'Почта' },
  settings: { icon: 'gear', old: 'Настройки', fresh: 'Настройки' },
};

/** Названия для списка в настройках — полные, без сокращений. */
export const TAB_NAMES = {
  day: 'День', schedule: 'Звонки / неделя', assistant: 'ИИ-помощник', homework: 'Задания',
  totals: 'Оценки', mail: 'Почта', settings: 'Настройки',
};

// Все семь: от шести вкладок подписи остаются только у активной (new.css),
// у остальных — иконки, иначе подписи не помещались на узком телефоне.
export const MAX_TABS = 7;

export function availableTabs() {
  return Object.keys(TAB_DEFS).filter((tab) => tab !== 'assistant' || assistantEnabled);
}

const CLASSIC_TABS = ['day', 'schedule', 'homework', 'totals', 'mail'];

export function defaultTabs() {
  return assistantEnabled
    ? ['day', 'assistant', 'homework', 'totals', 'mail', 'settings']
    : ['day', 'schedule', 'homework', 'totals', 'mail', 'settings'];
}

/** Вкладки панели: сохранённый выбор без неизвестных и недоступных. */
export function navTabs() {
  if (!isNewDesign()) return CLASSIC_TABS;
  const saved = getPref('tabs');
  if (!Array.isArray(saved)) return defaultTabs();
  const available = availableTabs();
  const tabs = [...new Set(saved)].filter((tab) => available.includes(tab)).slice(0, MAX_TABS);
  if (!tabs.includes('settings')) tabs.push('settings');
  return tabs.length >= 2 ? tabs : defaultTabs();
}

export function saveTabs(tabs) {
  setPref('tabs', tabs);
  renderNav();
}

export function resetTabs() {
  saveTabs(defaultTabs());
}

/** Пункты панели заново; линзу на активную вкладку ставит app.js. */
export function renderNav(activeTab = '') {
  const nav = $('bottomNav');
  const tabs = navTabs();
  nav.querySelectorAll('.nav-item').forEach((item) => item.remove());
  nav.insertAdjacentHTML('beforeend', tabs.map((tab) => {
    const def = TAB_DEFS[tab];
    return `<button class="nav-item${tab === activeTab ? ' active' : ''}" data-tab="${tab}" aria-label="${def.fresh}">
        <span class="ico" data-icon="${def.icon}">${ICONS[def.icon]}</span>
        <span class="lbl-old">${def.old}</span><span class="lbl-new">${def.fresh}</span></button>`;
  }).join(''));
  nav.style.setProperty('--n', tabs.length);
  nav.dataset.count = tabs.length;
  window.dispatchEvent(new CustomEvent('netschool:nav'));
}
