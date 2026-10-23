// Расписание в календаре телефона: доступ CalDAV и подписка .ics.
'use strict';

import { $, api, banner, esc, showFailure } from './core.js';
import { onScreenRefresh, pushScreen } from './screens.js';
import { notify } from './haptics.js';
import { closeDrawer } from './profile.js';

export async function openCalendar() {
  closeDrawer();
  const screen = pushScreen('Расписание в календаре');
  screen.closest('.screen').classList.add('calendar-screen');
  screen.innerHTML = '<div class="muted small">Загрузка…</div>';
  let password = '';
  const copy = async (value) => {
    try {
      await navigator.clipboard.writeText(value);
      notify('success');
      banner('Скопировано', false, 2500);
    } catch { prompt('Скопируйте значение', value); }
  };
  const render = (data) => {
    const scroll = screen.closest('.screen').scrollTop;
    const field = (label, value, id) => `<label class="field-label">${label}
      <input class="field" readonly value="${esc(value)}" autocomplete="off" spellcheck="false"></label>
      <button class="btn outline" id="${id}">Скопировать ${label.toLowerCase()}</button>`;
    screen.innerHTML = `<div class="card">
      ${data.available === false ? '<p>CalDAV сейчас отключён на сервере. Можно использовать подписку по ссылке ниже.</p>' : `<p>Подключите календарный аккаунт один раз. Уроки, кабинеты и задания будут
      появляться в штатном календаре; замены и отмены будут обновляться автоматически.</p>
      <p class="muted small">Сервер проверяет расписание в «Сетевом городе» примерно каждые ${data.interval_minutes} минут.
      iPhone получает изменения при следующей синхронизации календаря, поэтому они могут появиться с задержкой.
      Держать бот или это приложение открытыми не нужно. Календарь доступен только для чтения.</p>
      <details><summary>Как часто обновляется календарь на iPhone?</summary>
        <p>Частоту загрузки задаёт iOS: Настройки → Приложения → Календарь →
        Учётные записи календаря → Загрузка данных. Можно выбрать, например, каждые 15 или 30 минут.
        В старых версиях iOS раздел «Календарь» находится прямо в настройках.</p>
        <p>Кнопка «Проверить расписание сейчас» запускает проверку школы на сервере сразу.
        После неё iPhone получит изменения при следующей синхронизации календаря.</p>
      </details>`}
      ${data.connected ? `<p>${data.last_sync_at ? 'Расписание опубликовано.' : 'Готовим расписание — оно появится после первой успешной проверки школы.'}</p>
        ${data.last_error ? `<p class="muted small">${esc(data.last_error)}</p>` : ''}
        ${field('Сервер', data.server, 'calServer')}
        ${field('Account URL', data.account_url, 'calAccountUrl')}
        ${field('Логин', data.login, 'calLogin')}
        ${password ? `${field('Пароль', password, 'calPassword')}<p class="muted small">Сохраните пароль: повторно он не показывается.</p>` : '<p class="muted small">Если пароль потерян, выпустите новый и обновите его в настройках календарного аккаунта.</p>'}
        <p><b>На iPhone:</b> Настройки → Приложения → Календарь → Учётные записи календаря →
        Добавить учётную запись → Другое → Учётная запись CalDAV.</p>
        <p>Введите сервер, логин и пароль выше. Используйте SSL. Путь аккаунта определяется автоматически. Если Calendar просит Account URL, скопируйте его выше. Описание можно выбрать любое.</p>
        <button class="btn outline" id="calStatus">Обновить статус</button>
        <button class="btn outline" id="calRefresh">Проверить расписание сейчас</button>` : ''}
      ${data.available === false ? '' : `<button class="btn" id="calCreate">${data.connected ? 'Выпустить новый пароль' : 'Создать доступ CalDAV'}</button>`}
      ${data.connected ? '<button class="btn outline" id="calRevoke">Отключить доступ</button>' : ''}
      <p class="muted small">Выход через /logout отзывает доступ. Отключение доступа останавливает
      синхронизацию; уже сохранённые события удаляются вместе с аккаунтом на телефоне.</p>
      </div>
      <details class="card"><summary>Подписка по ссылке .ics</summary>
        <p>Альтернативный способ: добавьте ссылку как подписной календарь. Простое открытие файла не создаёт подписку.</p>
        <button class="btn outline" id="calCopyIcs">Скопировать ссылку .ics</button>
      </details>`;
    screen.closest('.screen').scrollTop = scroll;
    if (data.connected) {
      $('calServer').onclick = () => copy(data.server);
      $('calAccountUrl').onclick = () => copy(data.account_url);
      $('calLogin').onclick = () => copy(data.login);
      if (password) $('calPassword').onclick = () => copy(password);
      $('calStatus').onclick = load;
      $('calRefresh').onclick = async () => {
        try {
          await api('/api/caldav-sync', { method: 'POST' });
          banner('Проверяем расписание. Через некоторое время обновите статус.');
        } catch (error) { banner(error.message, true); }
      };
      $('calRevoke').onclick = async () => {
        if (!confirm('Отключить доступ календарного аккаунта к расписанию?')) return;
        try {
          await api('/api/caldav-account', { method: 'DELETE' });
          password = '';
          await load();
        } catch (error) { banner(error.message, true); }
      };
    }
    if ($('calCreate')) $('calCreate').onclick = async () => {
      if (data.connected && !confirm('Старый пароль перестанет работать. Выпустить новый?')) return;
      const button = $('calCreate');
      button.disabled = true;
      try {
        const result = await api('/api/caldav-account', { method: 'POST' });
        password = result.data.password;
        render(result.data);
      } catch (error) { banner(error.message, true); button.disabled = false; }
    };
    $('calCopyIcs').onclick = async () => {
      try { const result = await api('/api/calendar-link'); await copy(result.data.url); }
      catch (error) { banner(error.message, true); }
    };
  };
  const load = async () => {
    try { const { data } = await api('/api/caldav-account'); render(data); }
    catch (error) { screen.innerHTML = `<div class="muted small">${esc(error.message)}</div>`; }
  };
  // Свайп вниз: на месте, без «Загрузка…»; не вышло — прежнее остаётся.
  onScreenRefresh(screen, async () => {
    try { render((await api('/api/caldav-account')).data); }
    catch (error) { showFailure(screen, error, `<div class="muted small">${esc(error.message)}</div>`); }
  });
  await load();
}
