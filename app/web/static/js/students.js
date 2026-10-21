// Переключение между детьми и дневниками на просмотр — стрелка у имени в шапке.
'use strict';

import { $, api, banner, esc } from './core.js';
import { notify, select, tap } from './haptics.js';
import { closeSheet, openSheet } from './sheet.js';

/** Стрелка у имени в шапке — если есть между кем переключаться: дети
 *  аккаунта и дневники учеников, пригласивших на просмотр. Шторка со
 *  списком, нажатие переключает (как чипы в меню ☰). */
export function initStudentSwitch(profile) {
  const readonly = Boolean(profile?.readonly);
  const views = profile?.views || [];
  const options = [
    ...(readonly ? [] : (profile?.students || []).map((s) => ({ ...s, kind: 'student' }))),
    // Свой дневник при просмотре — пункт «Мой дневник»; без просмотра он уже в списке детей.
    ...views.filter((v) => readonly || v.id !== null).map((v) => ({ ...v, kind: 'view' })),
  ];
  if (options.length < 2) return;
  const title = $('topTitle');
  // Не disabled для одного ребёнка: выключенную кнопку iOS рисует бледной,
  // и имя в шапке выглядело размытым. Вместо этого — класс.
  title.classList.add('switchable');
  $('titleSwitch').hidden = false;
  title.addEventListener('click', () => {
    tap();
    const body = openSheet('Чей дневник', `<div class="student-list">${options.map((s, index) =>
      `<button class="student-option ${s.current ? 'on' : ''}" data-option="${index}">
        <span class="avatar small" style="background:${studentColor(s.name)}">${esc(initial(s.name))}</span>
        <span class="student-name">${esc(s.name)}${s.kind === 'view' && s.id !== null ? '<br><span class="muted small">просмотр</span>' : ''}</span>
        ${s.current ? '<span class="check">✓</span>' : ''}
      </button>`).join('')}</div>`);
    body.querySelectorAll('[data-option]').forEach((button) => button.addEventListener('click', async () => {
      const option = options[Number(button.dataset.option)];
      if (button.classList.contains('on')) {
        closeSheet();
        return;
      }
      select();
      if (option.kind === 'student') {
        button.dataset.student = option.id;
        switchStudent(button);
        return;
      }
      button.disabled = true;
      try {
        await api('/api/view', { method: 'POST', body: JSON.stringify({ id: option.id }) });
        location.reload();
      } catch (error) {
        notify('error');
        banner(error.message, true);
        button.disabled = false;
      }
    }));
  });
}

const STUDENT_COLORS = ['#4a6fae', '#2e8b6e', '#6b4fa8', '#a8434f', '#c9702f', '#2a8fa3'];

function studentColor(name) {
  let hash = 0;
  for (const char of String(name)) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return STUDENT_COLORS[hash % STUDENT_COLORS.length];
}

function initial(name) {
  // «Иванов Пётр» — по имени, а не по фамилии.
  const parts = String(name).trim().split(/\s+/);
  return (parts[1] || parts[0] || '?')[0].toUpperCase();
}

export async function switchStudent(button) {
  button.disabled = true;
  try {
    await api('/api/student', {
      method: 'POST',
      body: JSON.stringify({ id: Number(button.dataset.student) }),
    });
    notify('success');
    // Дневник другого ребёнка — совсем другие данные, сбрасываем всё.
    location.reload();
  } catch (error) {
    notify('error');
    banner(error.message, true);
    button.disabled = false;
  }
}
