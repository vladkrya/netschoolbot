'use strict';
import { api, esc, banner, base } from './core.js';
import { pushScreen } from './screens.js';

export async function openIntegrations() {
  const screen = pushScreen('API и интеграции');
  let secret = '';
  let permissionNames = {};
  const date = (value) => value ? new Date(value * 1000).toLocaleString('ru-RU') : 'Ещё не использовался';
  const render = (items) => {
    screen.innerHTML = `<div class="card"><h3>Ваши данные для интеграций</h3>
      <p>Выберите доступ для интеграции: дневник, почта и статус или только статус школьных серверов. Передавайте токен только доверенным приложениям.</p>
      <p><a href="${esc(base)}/api/v1/docs" target="_blank" rel="noopener">Документация API и примеры →</a></p>
      ${secret ? `<p>Сохраните токен: он показывается только сейчас.</p><input class="field" readonly value="${esc(secret)}" aria-label="Новый токен"><button class="btn outline" data-copy>Скопировать токен</button>` : ''}
      <form><fieldset><legend>Права доступа</legend>${Object.entries(permissionNames).map(([key, label]) => `<label class="field-label"><input type="checkbox" name="permissions" value="${esc(key)}" checked> ${esc(label)}</label>`).join('')}</fieldset><p class="muted small">Выберите нужные разделы. После создания права можно только посмотреть; для изменения создайте новый токен.</p><label class="field-label">Название интеграции<input class="field" name="name" required maxlength="80" placeholder="Например, виджет на рабочем столе"></label>
      <label class="field-label">Срок действия<select class="field" name="days"><option value="30">30 дней</option><option value="90" selected>90 дней</option><option value="365">1 год</option><option value="0">Бессрочно</option><option value="custom">Своя дата</option></select></label>
      <label class="field-label" data-custom-date hidden>Действует по дату включительно<input class="field" name="expires_on" type="date" disabled></label>
      <p class="muted small" data-date-hint hidden>До конца выбранного дня в часовом поясе аккаунта.</p>
      <button class="btn" type="submit">Создать токен</button></form></div>
      <div class="card"><h3>Токены</h3>${items.length ? items.map(item => `<div><strong>${esc(item.name)}</strong><p class="muted small">Права: ${(item.permissions || []).map(key => esc(permissionNames[key] || key)).join(', ')}</p><p class="muted small">${item.expires_at === null ? 'Бессрочно' : 'До ' + date(item.expires_at)}<br>Последний запрос: ${date(item.last_used_at)}</p><button class="btn outline" data-revoke="${item.id}">Отозвать</button></div>`).join('') : '<p class="muted">Пока нет токенов</p>'}</div>`;
    screen.querySelector('[data-copy]')?.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(secret); banner('Токен скопирован'); }
      catch { banner('Скопируйте токен из поля', true); }
    });
    const duration = screen.querySelector('[name="days"]');
    duration.addEventListener('change', () => {
      const custom = duration.value === 'custom';
      const dateField = screen.querySelector('[name="expires_on"]');
      screen.querySelector('[data-custom-date]').hidden = !custom;
      screen.querySelector('[data-date-hint]').hidden = !custom;
      dateField.disabled = !custom;
      dateField.required = custom;
    });
    screen.querySelector('form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      form.querySelector('button').disabled = true;
      try {
        const body = { name: form.elements.namedItem('name').value, permissions: [...form.querySelectorAll('[name="permissions"]:checked')].map(input => input.value) };
        if (!body.permissions.length) throw new Error('Выберите хотя бы одно право доступа');
        if (duration.value === 'custom') body.expires_on = form.elements.namedItem('expires_on').value;
        else body.days = Number(duration.value);
        const { data } = await api('/api/integration-tokens', { method: 'POST', body: JSON.stringify(body) });
        secret = data.token;
        render(data.items);
      } catch (error) { banner(error.message, true); form.querySelector('button').disabled = false; }
    });
    screen.querySelectorAll('[data-revoke]').forEach(button => button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await api(`/api/integration-tokens/${button.dataset.revoke}`, { method: 'DELETE' });
        secret = '';
        await load();
      } catch (error) { banner(error.message, true); button.disabled = false; }
    }));
  };
  const load = async () => {
    const { data } = await api('/api/integration-tokens');
    permissionNames = data.available_permissions;
    render(data.items);
  };
  screen.innerHTML = '<p class="muted">Загрузка…</p>';
  try { await load(); } catch (error) { screen.textContent = error.message; }
}
