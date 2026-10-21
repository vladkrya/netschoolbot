// Отчёты в Excel: выбор отчёта и срока, открытие в браузере или файлом в чат.
'use strict';

import { api, demo, ensureAttachmentTicket, exportUrl, initData, openExternal, state } from './core.js';
import { notify, select, tap } from './haptics.js';
import { openSheet } from './sheet.js';

// Отчёты Excel: ключ, название, что внутри. Пропуски отмечаются только
// в мобильном API — в веб-режиме этот отчёт был бы пустым.
const REPORTS = [
  ['grades', 'Оценки', 'Средние и выходящие по предметам, все работы с датами и весом'],
  ['subjects', 'Сводка по предметам', 'Учитель, уроки, пропуски, задания, средний и что выходит — строкой'],
  ['dynamics', 'Динамика по месяцам', 'Средний по каждому предмету помесячно: где растёт, где проседает'],
  ['tests', 'Контрольные и проверочные', 'Все крупные работы с оценками и средний по ним'],
  ['absences', 'Пропуски', 'Сколько пропущено по каждому предмету и список по датам', 'mobile'],
  ['homework', 'Домашние задания', 'Все задания периода с текстом и файлами'],
  ['lessons', 'Журнал уроков', 'Каждый урок: тема, учитель, оценки и пропуск'],
  ['schedule', 'Расписание недели', 'Обычная неделя таблицей: уроки, время и кабинеты — для печати'],
];

/** Отчёты в Excel: открываются как вложение из ДЗ — страницей ожидания во
 * внешнем браузере, где файл можно посмотреть или сохранить. В Telegram
 * можно ещё прислать его файлом в чат с ботом. */
export function openExport() {
  tap();
  const mobile = state.profile?.api_mode === 'mobile';
  const reports = REPORTS.filter(([, , , only]) => !only || mobile);
  const body = openSheet('Отчёты', `
    <div class="report-list" role="radiogroup" aria-label="Отчёт">${reports.map(([key, title, note], i) => `
      <label class="report-item"><input type="radio" name="report" value="${key}" ${i ? '' : 'checked'}>
        <span><b>${title}</b><span class="muted small">${note}</span></span></label>`).join('')}
    </div>
    <div class="chips report-scope" role="radiogroup" aria-label="Срок">
      <button class="chip active" data-scope="period" aria-pressed="true">Текущий период</button>
      <button class="chip" data-scope="year" aria-pressed="false">Весь учебный год</button>
    </div>
    <label class="switch-row" id="exportChatRow" ${initData ? '' : 'hidden'}><span>Прислать файлом в чат с ботом</span>
      <input type="checkbox" switch class="switch" id="exportToChat"></label>
    <button class="btn" id="exportGo">Открыть в Excel</button>
    <div class="muted small export-status"></div>`);
  const status = body.querySelector('.export-status');
  const go = body.querySelector('#exportGo');
  // В приложении на телефоне и на сайте initData нет, но бот пришлёт файл
  // и туда, если к аккаунту привязан Telegram — раньше переключателя не было.
  if (!initData && !demo) {
    api('/api/account').then(({ data }) => {
      if (data?.telegram) body.querySelector('#exportChatRow').hidden = false;
    }, () => {});
  }
  let scope = 'period';
  body.querySelectorAll('[data-scope]').forEach((chip) => chip.addEventListener('click', () => {
    select();
    scope = chip.dataset.scope;
    body.querySelectorAll('[data-scope]').forEach((c) => {
      c.classList.toggle('active', c === chip);
      c.setAttribute('aria-pressed', String(c === chip));
    });
  }));
  body.querySelector('#exportToChat')?.addEventListener('change', (event) => {
    go.textContent = event.target.checked ? 'Прислать в чат' : 'Открыть в Excel';
  });
  go.addEventListener('click', async () => {
    select();
    const report = body.querySelector('[name="report"]:checked')?.value || 'grades';
    if (!body.querySelector('#exportToChat')?.checked) {
      if (!(await ensureAttachmentTicket()) && initData) {
        notify('error');
        status.textContent = 'Не удалось открыть файл — попробуйте ещё раз.';
        return;
      }
      openExternal(exportUrl(scope, report));
      status.textContent = 'Файл открывается в браузере — там его можно посмотреть или сохранить.';
      return;
    }
    go.disabled = true;
    status.textContent = 'Собираю отчёт — за год это может занять до минуты…';
    try {
      await api('/api/export/send', { method: 'POST', body: JSON.stringify({ scope, report }) });
      status.textContent = 'Готово: файл в чате с ботом.';
      notify('success');
    } catch (error) {
      notify('error');
      status.textContent = error.message;
    } finally {
      go.disabled = false;
    }
  });
}
