// Админка: поиск по служебным идентификаторам и персональные настройки тем.
'use strict';
import { api, banner, esc } from './core.js';
import { ICONS } from './icons.js';
import { select, notify } from './haptics.js';
import { openSheet, closeSheet } from './sheet.js';

const label = (user) => user.username ? `@${user.username}` : `Аккаунт #${user.account_no}`;
const permissionLabel = (theme) => theme.permission === 'default' ? 'По общим правилам' : theme.available ? 'Доступ выдан' : 'Доступ закрыт';

function solverCard(user) {
  const {rule, catalog} = user.solver_access;
  const subjects = [...new Set(catalog.flatMap((b) => b.subjects))];
  return `<section class="card"><h3>Доступ к решениям</h3>
    <label class="switch-row"><span>Выборочный доступ</span><input type="checkbox" class="switch" switch data-solver-enabled ${rule ? 'checked' : ''}></label>
    <label class="switch-row"><span>Для всего класса<span class="muted small switch-note">Выключено — только для этого человека</span></span><input type="checkbox" class="switch" switch data-solver-class ${rule?.classwide ? 'checked' : ''}></label>
    <p class="muted small">Выберите предметы и учебники для решений. Общий доступ регулируется в разделе «Дополнительно».</p>
    <h4>Предметы</h4>${subjects.map((name) => `<label class="switch-row"><span>${esc(name)}</span><input type="checkbox" data-solver-subject value="${esc(name)}" ${rule?.subjects.includes(name) ? 'checked' : ''}></label>`).join('')}
    <h4>Учебники</h4>${catalog.map((book) => `<label class="switch-row"><span>${esc(book.title)}<span class="muted small switch-note">${esc(book.subjects.join(', '))}</span></span><input type="checkbox" data-solver-book value="${esc(book.id)}" ${rule?.books.includes(book.id) ? 'checked' : ''}></label>`).join('')}
    ${!catalog.length ? '<p class="muted small">На сервере нет загруженных учебников.</p>' : ''}
    <button type="button" class="btn" data-solver-save>Сохранить доступ к решениям</button>
  </section>`;
}

export function userThemeCard(theme) {
  const options = [['free', 'Выбирает сам'], ['on', 'Включена'], ['off', 'Выключена']];
  return `<section class="au-theme card">
    <div class="au-theme-preview au-preview-${esc(theme.key)}" aria-hidden="true"><span></span><i></i><i></i><i></i></div>
    <div class="au-theme-heading"><h3>${esc(theme.name)}</h3><span class="au-status ${theme.active ? 'active' : ''}">${theme.active ? 'Включена сейчас' : 'Не включена'}</span></div>
    <p class="muted small">${esc(theme.description)}</p>
    <label class="switch-row"><span>Разрешить тему<span class="muted small switch-note">${theme.owner ? 'Доступ владельца' : permissionLabel(theme)}</span></span>
      <input type="checkbox" switch class="switch" aria-label="Доступ: ${esc(theme.name)}" data-theme-access="${esc(theme.key)}" ${theme.owner ? 'disabled' : ''} ${theme.available ? 'checked' : ''}></label>
    <div class="au-setting-label">Управление оформлением</div>
    <div class="au-options" role="group" aria-label="Состояние: ${esc(theme.name)}">
      ${options.map(([value, text]) => `<button type="button" class="${theme.forced === value ? 'on' : ''}" aria-pressed="${theme.forced === value}" data-theme-force="${esc(theme.key)}" data-value="${value}">${text}</button>`).join('')}
    </div>
    <p class="muted small au-explanation">${theme.forced === 'free' ? 'Пользователь сам выбирает, включать ли доступную тему.' : 'Вы задали состояние темы. Пользователь не сможет изменить его до снятия ограничения.'}</p>
    ${!theme.owner && theme.permission !== 'default' ? `<button type="button" class="link small" data-theme-default="${esc(theme.key)}">Вернуть доступ по общим правилам</button>` : ''}
  </section>`;
}

export function userAdvanced(user, open = false) {
  const option = (name, title, values) => `<label class="au-control"><span>${title}</span><select class="field" data-managed-pref="${name}">
    ${[['free', 'Выбирает сам'], ...values].map(([value, text]) => `<option value="${value}" ${(user.forced_prefs[name] || 'free') === value ? 'selected' : ''}>${text}</option>`).join('')}</select></label>`;
  return `<details class="au-advanced card" ${open ? 'open' : ''}><summary>Дополнительно <span class="muted small">проверка, функции и безопасность</span></summary>
    <div class="au-advanced-body">
      <h3>Проверка дневника</h3>
      <label class="switch-row"><span>Фоновая проверка</span><input class="switch" type="checkbox" switch data-check-enabled ${user.checking.enabled ? 'checked' : ''}></label>
      <label class="au-control"><span>Интервал</span><select class="field" data-check-interval>${[180,300,600,1800].map((n) => `<option value="${n}" ${n === user.checking.interval ? 'selected' : ''}>${n/60} мин</option>`).join('')}</select></label>
      <button class="btn outline" data-user-restart ${!user.checking.enabled || user.blocked || !user.checking.ready ? 'disabled' : ''}>Перезапустить проверку</button>
      <h3>Уведомления</h3>
      ${Object.entries({grades:'Оценки',homework:'Домашние задания',mail:'Почта',telegram:'В Telegram'}).map(([key,text]) => `<label class="switch-row"><span>${text}</span><input class="switch" type="checkbox" switch data-user-notify="${key}" ${user.notifications[key] ? 'checked' : ''}></label>`).join('')}
      <h3>Доступ к функциям</h3>
      ${user.features.map((feature) => `<label class="au-control"><span>${esc(feature.name)}</span><select class="field" data-user-feature="${feature.key}">${[['default','По общим правилам'],['allow','Разрешить'],['deny','Запретить']].map(([value,text]) => `<option value="${value}" ${value === feature.permission ? 'selected' : ''}>${text}</option>`).join('')}</select></label>`).join('')}
      <h3>Остальное оформление</h3>
      ${option('theme','Светлая / тёмная',[['auto','По системе'],['light','Светлая'],['dark','Тёмная']])}
      ${option('design','Дизайн',[['min','min'],['new','Новый'],['classic','Старый']])}
      ${option('motion','Анимации',[['on','Включены'],['off','Выключены']])}
      ${option('confetti','Конфетти',[['show','Включено'],['hide','Выключено']])}
      <h3>Безопасность</h3>
      <div class="au-security"><p class="muted small">${user.blocked ? 'Доступ пользователя заблокирован.' : 'Доступ пользователя разрешён.'}</p>
      <button class="btn outline" data-user-block ${user.owner ? 'disabled' : ''}>${user.blocked ? 'Разблокировать доступ' : 'Заблокировать доступ'}</button>
      <button class="btn outline" data-user-security="reset" ${user.owner ? 'disabled' : ''}>Сбросить входы…</button>
      <button class="btn outline" data-user-security="pin" ${user.owner ? 'disabled' : ''}>Сбросить PIN…</button>
      <button class="btn outline au-danger" data-user-security="delete" ${user.owner ? 'disabled' : ''}>Удалить аккаунт…</button></div>
    </div></details>`;
}

export function mountAdminUsers(container, model) {
  let searchSequence = 0;
  let selectionSequence = 0;
  let timer;
  container.innerHTML = `<div class="au-intro"><h2>Пользователи</h2><p class="muted small">Найдите аккаунт и настройте доступ к темам и решениям.</p></div>
    <form class="au-search" role="search">
      <input class="field" id="adminUserQuery" aria-label="Поиск пользователя" type="search" autocomplete="off" spellcheck="false" maxlength="64" placeholder="@username, Telegram ID или #аккаунта" value="${esc(model.query)}">
      <button class="btn" type="submit" aria-label="Найти пользователя">${ICONS.search}</button>
    </form>
    <div class="au-results" aria-live="polite"></div>
    <div class="au-user" aria-live="polite"></div>`;
  const input = container.querySelector('input[type="search"]');
  const results = container.querySelector('.au-results');
  const detail = container.querySelector('.au-user');
  const screen = container.closest('.screen');
  const setHeader = (user = null) => {
    const title = screen?.querySelector('.screen-title');
    const subtitle = screen?.querySelector('.screen-subtitle');
    if (title) title.textContent = user ? label(user) : 'Админ-настройки';
    if (subtitle) subtitle.textContent = user ? `ID ${user.id} · аккаунт #${user.account_no}` : 'видно только вам';
  };
  const current = () => container.isConnected;
  const empty = () => { results.innerHTML = `<div class="au-empty">${ICONS.users}<b>Найдите пользователя</b><span class="muted small">По нику Telegram, Telegram ID или номеру аккаунта.</span></div>`; };

  const drawDetails = (user) => {
    setHeader(user);
    detail.innerHTML = `<div class="au-identity card">
      <div class="au-avatar" aria-hidden="true">${user.username ? esc(user.username[0].toUpperCase()) : '#'}</div>
      <div class="au-identity-copy"><h2>${esc(label(user))}</h2><div class="au-identifiers"><span>Аккаунт #${esc(user.account_no)}</span><span>${user.has_telegram ? 'Telegram ID' : 'ID'}: ${esc(user.id)}</span></div></div>
      <button class="icon-btn au-close" type="button" aria-label="Закрыть пользователя">×</button>
    </div>
    <div class="au-section-title">Персональные темы</div>
    <p class="muted small au-section-note">Доступ разрешает выбрать тему. «Включена» применяет её принудительно.</p>
    <div class="au-theme-grid">${user.themes.map((theme) => userThemeCard({...theme, owner:user.owner})).join('')}</div>
    ${solverCard(user)}
    ${userAdvanced(user, model.advancedOpen)}`;
    detail.querySelector('.au-close').addEventListener('click', () => {
      model.selected = null;
      setHeader();
      selectionSequence++;
      detail.replaceChildren();
      if (model.query) search(); else empty();
    });
    detail.querySelector('.au-advanced').addEventListener('toggle', (event) => { model.advancedOpen = event.target.open; });
    const change = async (kind, name, value, endpoint = 'theme') => {
      const uid = user.id;
      const revision = selectionSequence;
      detail.querySelectorAll('button, input, select').forEach((control) => { control.disabled = true; });
      try {
        const { data } = await api(`/api/admin/users/${uid}/${endpoint}`, { method: 'POST', body: JSON.stringify({kind, name, value}) });
        if (current() && model.selected === uid && selectionSequence === revision) drawDetails(data);
        notify('success');
      } catch (error) {
        if (current() && model.selected === uid && selectionSequence === revision) drawDetails(user);
        banner(error.message, true);
        notify('error');
      }
    };
    const manage = (kind, name, value) => { select(); return change(kind, name, value, 'manage'); };
    detail.querySelector('[data-solver-save]').addEventListener('click', () => manage('solver', 'access', {
      enabled: detail.querySelector('[data-solver-enabled]').checked,
      classwide: detail.querySelector('[data-solver-class]').checked,
      subjects: [...detail.querySelectorAll('[data-solver-subject]:checked')].map((el) => el.value),
      books: [...detail.querySelectorAll('[data-solver-book]:checked')].map((el) => el.value),
    }));
    detail.querySelector('[data-check-enabled]').addEventListener('change', (event) => manage('checking', 'enabled', event.target.checked));
    detail.querySelector('[data-check-interval]').addEventListener('change', (event) => manage('checking', 'interval', Number(event.target.value)));
    detail.querySelector('[data-user-restart]').addEventListener('click', () => manage('checking', 'restart', true));
    detail.querySelectorAll('[data-user-notify]').forEach((input) => input.addEventListener('change', () => manage('notify', input.dataset.userNotify, input.checked)));
    detail.querySelectorAll('[data-user-feature]').forEach((input) => input.addEventListener('change', () => manage('grant', input.dataset.userFeature, input.value)));
    detail.querySelectorAll('[data-managed-pref]').forEach((input) => input.addEventListener('change', () => manage('force', input.dataset.managedPref, input.value)));
    detail.querySelector('[data-user-block]').addEventListener('click', () => manage('block', 'access', !user.blocked));
    detail.querySelectorAll('[data-user-security]').forEach((button) => button.addEventListener('click', async () => {
      const operation = button.dataset.userSecurity;
      const titles = { reset: 'Сбросить входы', pin: 'Сбросить PIN', delete: 'Удалить аккаунт' };
      button.disabled = true;
      try {
        const { data: confirmation } = await api(`/api/admin/users/${user.id}/manage`, { method:'POST', body:JSON.stringify({kind:'confirm', name:operation, value:''}) });
        if (!current() || model.selected !== user.id) return;
        const sheet = openSheet(titles[operation], `<p><b>${esc(label(user))}</b><br><span class="muted small">ID ${esc(user.id)} · аккаунт #${esc(user.account_no)}</span></p>
          <p>${operation === 'delete' ? 'Аккаунт и все его данные будут удалены. Это действие нельзя отменить.' : operation === 'pin' ? 'PIN и биометрическая защита приложения будут сброшены.' : 'Текущие сеансы и ссылки будут отозваны. Пользователь сможет войти заново.'}</p>
          <div class="app-icon-actions"><button class="btn ${operation === 'delete' ? 'au-danger' : ''}" data-user-confirm>${titles[operation]}</button><button class="btn outline" data-user-cancel>Отмена</button></div>`);
        sheet.querySelector('[data-user-cancel]').addEventListener('click', closeSheet);
        sheet.querySelector('[data-user-confirm]').addEventListener('click', async (event) => {
          event.currentTarget.disabled = true;
          try {
            const { data } = await api(`/api/admin/users/${user.id}/manage`, {method:'POST', body:JSON.stringify({kind:'execute', name:operation, value:confirmation.confirmation})});
            closeSheet(); notify('success');
            if (!current() || model.selected !== user.id) return;
            if (data.deleted) { model.selected = null; setHeader(); detail.innerHTML = '<div class="card">Аккаунт удалён.</div>'; search(); }
            else drawDetails(data);
          } catch (error) { event.currentTarget.disabled = false; banner(error.message, true); }
        });
      } catch (error) { banner(error.message, true); }
      finally { button.disabled = false; }
    }));
    detail.querySelectorAll('[data-theme-access]').forEach((control) => control.addEventListener('change', () => {
      select();
      change('grant', control.dataset.themeAccess, control.checked ? 'allow' : 'deny');
    }));
    detail.querySelectorAll('[data-theme-force]').forEach((button) => button.addEventListener('click', () => {
      if (button.getAttribute('aria-pressed') === 'true') return;
      select();
      change('force', button.dataset.themeForce, button.dataset.value);
    }));
    detail.querySelectorAll('[data-theme-default]').forEach((button) => button.addEventListener('click', () => {
      select();
      change('grant', button.dataset.themeDefault, 'default');
    }));
  };

  const open = async (uid) => {
    const sequence = ++selectionSequence;
    if (model.selected !== uid) model.advancedOpen = false;
    model.selected = uid;
    detail.innerHTML = '<div class="card muted small">Загрузка настроек…</div>';
    results.replaceChildren();
    try {
      const { data } = await api(`/api/admin/users/${uid}`);
      if (current() && sequence === selectionSequence) drawDetails(data);
    } catch (error) {
      if (current() && sequence === selectionSequence) detail.innerHTML = `<div class="card muted small">${esc(error.message)}</div>`;
    }
  };

  const search = async () => {
    clearTimeout(timer);
    const sequence = ++searchSequence;
    model.query = input.value.trim();
    if (!model.query) { empty(); return; }
    results.innerHTML = '<p class="muted small">Ищем…</p>';
    try {
      const { data } = await api(`/api/admin/users?q=${encodeURIComponent(model.query)}`);
      if (!current() || sequence !== searchSequence) return;
      results.innerHTML = data.items.length ? `<div class="au-result-list">${data.items.map((user) => `<button type="button" class="au-result" data-user-id="${user.id}" aria-pressed="${model.selected === user.id}">
        <span><b>${esc(label(user))}</b><small>Аккаунт #${esc(user.account_no)} · ID ${esc(user.id)}</small></span><span aria-hidden="true">›</span></button>`).join('')}</div>
        ${data.more ? '<p class="muted small">Показаны первые 12. Уточните запрос.</p>' : ''}` : '<p class="au-no-results muted small">Никого не нашли. Проверьте ID или username. Поиск идёт среди пользователей приложения.</p>';
      results.querySelectorAll('[data-user-id]').forEach((button) => button.addEventListener('click', () => { select(); open(Number(button.dataset.userId)); }));
    } catch (error) {
      if (current() && sequence === searchSequence) results.innerHTML = `<p class="muted small">${esc(error.message)}</p>`;
    }
  };
  container.querySelector('form').addEventListener('submit', (event) => { event.preventDefault(); search(); });
  input.addEventListener('input', () => {
    model.query = input.value;
    setHeader();
    model.selected = null;
    model.advancedOpen = false;
    selectionSequence++;
    detail.replaceChildren();
    searchSequence++;
    clearTimeout(timer);
    timer = setTimeout(search, 300);
  });
  if (model.query) search(); else empty();
  if (model.selected) open(model.selected);
}
