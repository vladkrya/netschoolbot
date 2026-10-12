// Вход на сайт без Telegram (почта и пароль) и вход в «Сетевой город» с сайта.
//
// Сервер — app/web/account.py. После входа по почте сервер ставит cookie с
// постоянным токеном, и страница просто перезагружается — дальше всё как при
// входе по ссылке из бота.
'use strict';

import { api, banner, base, esc, tg } from './core.js';
import { notify } from './haptics.js';
import { pushScreen } from './screens.js';

function screen(html) {
  document.getElementById('bootSplash')?.remove();
  document.getElementById('accountScreen')?.remove();
  const root = document.createElement('div');
  root.className = 'app-lock account-screen';
  root.id = 'accountScreen';
  root.innerHTML = `<div class="app-lock-card account-card">${html}</div>`;
  document.body.appendChild(root);
  document.documentElement.classList.add('app-locked');
  return root.querySelector('.account-card');
}

function reload() {
  location.replace(`${base}/`);
}

/** Кнопка с ожиданием: пока идёт запрос, нажать второй раз нельзя. */
async function busy(button, work) {
  if (button.disabled) return;
  button.disabled = true;
  button.classList.add('loading');
  try {
    await work();
  } finally {
    button.disabled = false;
    button.classList.remove('loading');
  }
}

function showError(card, message) {
  const box = card.querySelector('.account-error');
  box.textContent = message || '';
  box.hidden = !message;
}

const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });

/* ─────────────────────────── Вход по почте ─────────────────────────── */

export function showAuth(view = 'login') {
  const views = { login: renderLogin, register: renderRegister, reset: renderReset };
  views[view]();
  return new Promise(() => {}); // страница перезагрузится после входа
}

function renderLogin() {
  const card = screen(`
    <h2>Дневник</h2>
    <p class="muted">Войдите по почте и паролю. Есть Telegram — откройте дневник кнопкой в боте.</p>
    <form class="account-form">
      <input class="field" type="email" name="email" autocomplete="username" placeholder="Почта" required>
      <input class="field" type="password" name="password" autocomplete="current-password" placeholder="Пароль" required>
      <p class="account-error" hidden></p>
      <button class="btn" type="submit">Войти</button>
    </form>
    <div class="account-links">
      <button class="link" data-go="register">Регистрация</button>
      <button class="link" data-go="reset">Забыли пароль?</button>
    </div>`);
  bindLinks(card);
  const form = card.querySelector('form');
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    busy(form.querySelector('button'), async () => {
      try {
        await post('/api/account/login', { email: form.email.value, password: form.password.value });
        reload();
      } catch (error) {
        showError(card, error.message);
      }
    });
  });
}

function renderRegister() {
  const card = screen(`
    <h2>Регистрация</h2>
    <p class="muted">Аккаунт для входа на сайт без Telegram. Потом войдёте в «Сетевой город» — как в боте.</p>
    <form class="account-form">
      <input class="field" type="email" name="email" autocomplete="username" placeholder="Почта" required>
      <input class="field" type="password" name="password" autocomplete="new-password" placeholder="Пароль (от 8 символов)" required>
      <input class="field" type="password" name="again" autocomplete="new-password" placeholder="Пароль ещё раз" required>
      <p class="account-error" hidden></p>
      <button class="btn" type="submit">Создать аккаунт</button>
    </form>
    <div class="account-links"><button class="link" data-go="login">Уже есть аккаунт</button></div>`);
  bindLinks(card);
  const form = card.querySelector('form');
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (form.password.value !== form.again.value) {
      showError(card, 'Пароли не совпадают');
      return;
    }
    busy(form.querySelector('button'), async () => {
      try {
        await post('/api/account/register', { email: form.email.value, password: form.password.value });
        reload();
      } catch (error) {
        showError(card, error.message);
      }
    });
  });
}

function renderReset() {
  const card = screen(`
    <h2>Сброс пароля</h2>
    <p class="muted">Пришлём код на почту — или в Telegram, если он привязан.</p>
    <form class="account-form" data-step="email">
      <input class="field" type="email" name="email" autocomplete="username" placeholder="Почта" required>
      <div class="account-code" hidden>
        <input class="field" name="code" inputmode="numeric" autocomplete="one-time-code" placeholder="Код из 6 цифр">
        <input class="field" type="password" name="password" autocomplete="new-password" placeholder="Новый пароль">
      </div>
      <p class="account-error" hidden></p>
      <button class="btn" type="submit">Получить код</button>
    </form>
    <div class="account-links"><button class="link" data-go="login">Назад ко входу</button></div>`);
  bindLinks(card);
  const form = card.querySelector('form');
  const button = form.querySelector('button');
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    busy(button, async () => {
      try {
        if (form.dataset.step === 'email') {
          const { data } = await post('/api/account/reset/request', { email: form.email.value });
          form.dataset.step = 'code';
          form.querySelector('.account-code').hidden = false;
          form.email.readOnly = true;
          button.textContent = 'Сменить пароль';
          showError(card, '');
          card.querySelector('.muted').textContent = data.channel === 'mail'
            ? 'Если такая почта зарегистрирована, код уже в письме. Проверьте и «Спам».'
            : 'Если к аккаунту привязан Telegram, код пришёл от бота. Без Telegram сброс пока недоступен — напишите администратору.';
          return;
        }
        await post('/api/account/reset/confirm', {
          email: form.email.value, code: form.code.value, password: form.password.value,
        });
        reload();
      } catch (error) {
        showError(card, error.message);
      }
    });
  });
}

function bindLinks(card) {
  card.querySelectorAll('[data-go]').forEach((link) => link.addEventListener('click', () => showAuth(link.dataset.go)));
}

/* ─────────────────── Вход в «Сетевой город» с сайта ─────────────────── */

const METHODS = [
  ['password', 'Логин и пароль «Сетевого города»'],
  ['esia', 'Госуслуги: логин и пароль'],
  ['qr', 'Госуслуги: QR-код'],
];

/** Мастер входа в «Сетевой город»: регион → школа → способ → вход. */
export function showSgoLogin(info = {}) {
  const choice = {};
  // Уже в дневнике и входит заново — можно передумать и вернуться.
  const footer = () => `
    <div class="account-links">
      ${info.needs_sgo ? '' : '<button class="link" data-act="close">Вернуться в дневник</button>'}
      ${!info.telegram && info.needs_sgo ? '<button class="link" data-act="logout">Выйти из аккаунта</button>' : ''}
    </div>`;
  const bindFooter = (card) => {
    card.querySelector('[data-act="logout"]')?.addEventListener('click', async () => {
      await post('/api/account/logout', {}).catch(() => {});
      reload();
    });
    card.querySelector('[data-act="close"]')?.addEventListener('click', () => {
      clearTimeout(timer);
      document.getElementById('accountScreen')?.remove();
      document.documentElement.classList.remove('app-locked');
    });
  };

  const regionStep = async () => {
    const card = screen(`
      <h2>Вход в «Сетевой город»</h2>
      <p class="muted">Шаг 1 из 3. Выберите регион.</p>
      <input class="field" name="filter" placeholder="Поиск региона">
      <div class="account-list"><div class="muted">Загрузка…</div></div>
      <button class="link" data-act="manual">Указать адрес сервера вручную</button>
      <p class="account-error" hidden></p>${footer()}`);
    bindFooter(card);
    let regions = [];
    const list = card.querySelector('.account-list');
    const draw = () => {
      const filter = card.querySelector('[name="filter"]').value.trim().toLowerCase();
      list.innerHTML = regions.filter((name) => name.toLowerCase().includes(filter)).map((name) =>
        `<button class="btn outline" data-region="${esc(name)}">${esc(name)}</button>`).join('')
        || '<div class="muted">Ничего не нашлось</div>';
    };
    card.querySelector('[name="filter"]').addEventListener('input', draw);
    list.addEventListener('click', (event) => {
      const button = event.target.closest('[data-region]');
      if (!button) return;
      Object.assign(choice, { region: button.dataset.region, url: '' });
      schoolStep();
    });
    card.querySelector('[data-act="manual"]').addEventListener('click', () => {
      const url = window.prompt('Адрес сервера «Сетевого города», например https://sgo.example.ru');
      if (!url) return;
      Object.assign(choice, { region: '', url: url.trim() });
      schoolStep();
    });
    try {
      regions = (await api('/api/sgo/regions')).data.items;
      draw();
    } catch (error) {
      showError(card, error.message);
    }
  };

  const schoolStep = () => {
    const card = screen(`
      <h2>Вход в «Сетевой город»</h2>
      <p class="muted">Шаг 2 из 3. ${esc(choice.region || choice.url)}: найдите школу по названию или номеру.</p>
      <form class="account-form account-row">
        <input class="field" name="q" placeholder="Например, 93 или лицей" required>
        <button class="btn" type="submit">Найти</button>
      </form>
      <div class="account-list"></div>
      <p class="account-error" hidden></p>
      <button class="link" data-act="back">Другой регион</button>${footer()}`);
    bindFooter(card);
    card.querySelector('[data-act="back"]').addEventListener('click', regionStep);
    const form = card.querySelector('form');
    const list = card.querySelector('.account-list');
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      busy(form.querySelector('button'), async () => {
        showError(card, '');
        list.innerHTML = '<div class="muted">Ищем…</div>';
        try {
          const params = new URLSearchParams({ q: form.q.value, region: choice.region || '', url: choice.url || '' });
          const { data } = await api(`/api/sgo/schools?${params}`);
          list.innerHTML = data.items.map((name) => `<button class="btn outline" data-school="${esc(name)}">${esc(name)}</button>`).join('')
            || '<div class="muted">Ничего не нашлось — попробуйте номер школы</div>';
        } catch (error) {
          list.innerHTML = '';
          showError(card, error.message);
        }
      });
    });
    list.addEventListener('click', (event) => {
      const button = event.target.closest('[data-school]');
      if (!button) return;
      choice.school = button.dataset.school;
      methodStep();
    });
  };

  const methodStep = () => {
    const card = screen(`
      <h2>Вход в «Сетевой город»</h2>
      <p class="muted">Шаг 3 из 3. ${esc(choice.school)}. Как будете входить?</p>
      <div class="account-list">${METHODS.map(([method, label]) =>
        `<button class="btn outline" data-method="${method}">${label}</button>`).join('')}</div>
      <button class="link" data-act="back">Другая школа</button>${footer()}`);
    bindFooter(card);
    card.querySelector('[data-act="back"]').addEventListener('click', schoolStep);
    card.querySelectorAll('[data-method]').forEach((button) => button.addEventListener('click', () => {
      choice.method = button.dataset.method;
      if (choice.method === 'qr') start({});
      else credentialsStep();
    }));
  };

  const credentialsStep = () => {
    const esia = choice.method === 'esia';
    const card = screen(`
      <h2>${esia ? 'Госуслуги' : '«Сетевой город»'}</h2>
      <p class="muted">${esc(choice.school)}</p>
      <form class="account-form">
        <input class="field" name="login" autocomplete="username" placeholder="${esia ? 'Телефон, СНИЛС или почта' : 'Логин «Сетевого города»'}" required>
        <input class="field" type="password" name="password" autocomplete="current-password" placeholder="Пароль" required>
        <label class="account-check"><input type="checkbox" name="remember" checked>
          <span>Запомнить для повторного входа${esia ? '' : ' — дневник сам войдёт, когда сессия истечёт'}</span></label>
        <p class="account-error" hidden></p>
        <button class="btn" type="submit">Войти</button>
      </form>
      <button class="link" data-act="back">Другой способ</button>`);
    card.querySelector('[data-act="back"]').addEventListener('click', methodStep);
    const form = card.querySelector('form');
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      start({ login: form.login.value, password: form.password.value, remember: form.remember.checked }, card);
    });
  };

  let timer = 0;
  const start = async (credentials, card = null) => {
    clearTimeout(timer);
    try {
      await post('/api/sgo/login', { ...choice, ...credentials });
    } catch (error) {
      if (card) showError(card, error.message);
      else progress({ status: 'error', message: error.message });
      return;
    }
    progress({ status: 'running' });
    poll();
  };

  const poll = async () => {
    let data = null;
    try {
      data = (await api('/api/sgo/login')).data;
    } catch {
      /* связь моргнула — спросим ещё раз */
    }
    if (data) progress(data);
    if (!data || !['done', 'error'].includes(data.status)) timer = setTimeout(poll, 1000);
  };

  let shown = '';
  const progress = (data) => {
    const key = `${data.status}:${data.qr ? data.qr.length : ''}`;
    if (data.status === 'qr' && shown === key) {
      const left = document.querySelector('#accountScreen .account-qr-left');
      if (left) left.textContent = `Код действует ещё ${data.qr_left} с`;
      return;
    }
    shown = key;
    if (data.status === 'running') {
      screen('<h2>Входим…</h2><div class="account-wait"><span class="boot-spinner"></span></div><p class="muted">Это может занять до минуты.</p>');
    } else if (data.status === 'qr') {
      const image = data.qr.startsWith('data:image') ? `<img class="account-qr" src="${data.qr}" alt="QR-код Госуслуг">`
        : `<p><a href="${esc(data.qr)}">Открыть ссылку для входа</a></p>`;
      const card = screen(`<h2>QR-код Госуслуг</h2>
        <p class="muted">Отсканируйте его в приложении «Госуслуги» на телефоне: Сканер QR в правом верхнем углу.</p>
        ${image}<p class="muted account-qr-left">Код действует ещё ${data.qr_left} с</p>
        <button class="link" data-act="cancel">Отмена</button>`);
      card.querySelector('[data-act="cancel"]').addEventListener('click', cancel);
    } else if (data.status === 'otp') {
      const card = screen(`<h2>${esc(data.otp.label)}</h2>
        <p class="muted">${data.otp.phone ? `Отправлен на ${esc(data.otp.phone)}. ` : ''}${data.otp.length} цифр.</p>
        <form class="account-form">
          <input class="field" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="8" required>
          <p class="account-error" hidden></p>
          <button class="btn" type="submit">Подтвердить</button>
        </form>
        <button class="link" data-act="cancel">Отмена</button>`);
      card.querySelector('[data-act="cancel"]').addEventListener('click', cancel);
      const form = card.querySelector('form');
      form.code.focus();
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        busy(form.querySelector('button'), async () => {
          try {
            await post('/api/sgo/login/otp', { code: form.code.value });
            shown = '';
          } catch (error) {
            showError(card, error.message);
          }
        });
      });
    } else if (data.status === 'done') {
      screen(`<h2>✅ ${esc(data.message || 'Вход выполнен')}</h2><p class="muted">Открываем дневник…</p>`);
      setTimeout(reload, 900);
    } else if (data.status === 'error') {
      const card = screen(`<h2>Не получилось войти</h2>
        <p class="account-error">${esc(data.message || 'Попробуйте ещё раз')}</p>
        <div class="account-list">
          ${data.retry ? '<button class="btn" data-act="retry">Получить новый QR-код</button>' : ''}
          <button class="btn outline" data-act="method">Другой способ входа</button>
          <button class="btn outline" data-act="region">Начать сначала</button>
        </div>`);
      card.querySelector('[data-act="retry"]')?.addEventListener('click', () => start({}));
      card.querySelector('[data-act="method"]').addEventListener('click', choice.school ? methodStep : regionStep);
      card.querySelector('[data-act="region"]').addEventListener('click', regionStep);
    }
  };

  const cancel = async () => {
    clearTimeout(timer);
    await post('/api/sgo/login/cancel', {}).catch(() => {});
    if (choice.school) methodStep();
    else regionStep();
  };

  // Вход уже идёт (страницу перезагрузили на шаге QR или кода) — продолжаем его.
  if (info.login && !['done', 'error'].includes(info.login.status)) {
    progress(info.login);
    poll();
  } else {
    regionStep();
  }
  return new Promise(() => {});
}

/* ─────────────────────────── Настройки входа ─────────────────────────── */

/** Экран «Вход по почте»: почта и пароль для сайта, Telegram, выход. */
export async function openAccountSettings() {
  const body = pushScreen('Вход по почте');
  const draw = async () => {
    body.innerHTML = '<div class="card"><div class="muted">Загрузка…</div></div>';
    let info;
    try {
      info = (await api('/api/account')).data;
    } catch (error) {
      body.innerHTML = `<div class="card error-card"><p>${esc(error.message)}</p></div>`;
      return;
    }
    const emailForm = (title, withCurrent) => `
      <form class="account-form" data-form="attach">
        <div class="card-title">${title}</div>
        <input class="field" type="email" name="email" autocomplete="username" placeholder="Почта" value="${esc(info.email || '')}" required>
        <input class="field" type="password" name="password" autocomplete="new-password" placeholder="${withCurrent ? 'Новый пароль' : 'Пароль (от 8 символов)'}" required>
        ${withCurrent ? '<input class="field" type="password" name="current" autocomplete="current-password" placeholder="Текущий пароль" required>' : ''}
        <p class="account-error" hidden></p>
        <button class="btn" type="submit">Сохранить</button>
      </form>`;
    body.innerHTML = `
      <div class="card account-settings">
        ${info.email ? `
          <div class="card-title">Почта</div>
          <p><b>${esc(info.email)}</b> ${info.verified ? '<span class="muted">· подтверждена</span>' : '<span class="muted">· не подтверждена</span>'}</p>
          <p class="muted">По почте и паролю можно войти на сайт без Telegram — например, когда Telegram не открывается.</p>
          ${info.mail && !info.verified ? `
            <form class="account-form account-row" data-form="verify">
              <input class="field" name="code" inputmode="numeric" placeholder="Код из письма">
              <button class="btn" type="submit">Подтвердить</button>
            </form>
            <button class="link" data-act="resend">Прислать код ещё раз</button>` : ''}`
          : `<p class="muted">Задайте почту и пароль — и на сайт можно будет входить без Telegram.</p>${emailForm('Почта и пароль', false)}`}
      </div>
      ${info.email ? `<div class="card">${emailForm('Сменить почту или пароль', true)}</div>` : ''}
      ${info.telegram ? '' : `
        <div class="card">
          <div class="card-title">Telegram</div>
          <p class="muted">Привяжите Telegram — уведомления будут приходить и туда, а в дневник можно будет заходить из бота.</p>
          <div class="account-link-code"></div>
          <button class="btn outline" data-act="link">Привязать Telegram</button>
        </div>`}
      <div class="card">
        <div class="card-title">«Сетевой город»</div>
        <p class="muted">${info.school ? esc(info.school) : 'Вход не выполнен'}</p>
        <button class="btn outline" data-act="sgo">Войти в «Сетевой город» заново</button>
      </div>
      ${info.email && !tg?.initData ? '<button class="btn outline danger account-logout" data-act="logout">Выйти на этом устройстве</button>' : ''}`;
    bind(info);
  };

  const bind = (info) => {
    const form = (name) => body.querySelector(`[data-form="${name}"]`);
    const error = (element, message) => {
      const box = element.querySelector('.account-error');
      if (box) { box.textContent = message; box.hidden = !message; } else banner(message, true);
    };
    form('attach')?.addEventListener('submit', (event) => {
      event.preventDefault();
      const element = event.currentTarget;
      busy(element.querySelector('button'), async () => {
        try {
          await post('/api/account/attach', {
            email: element.email.value, password: element.password.value, current: element.current?.value || '',
          });
          notify('success');
          banner('Сохранено');
          setTimeout(() => banner(''), 1800);
          draw();
        } catch (failure) {
          notify('error');
          error(element, failure.message);
        }
      });
    });
    form('verify')?.addEventListener('submit', (event) => {
      event.preventDefault();
      const element = event.currentTarget;
      busy(element.querySelector('button'), async () => {
        try {
          await post('/api/account/verify', { code: element.code.value });
          notify('success');
          draw();
        } catch (failure) {
          notify('error');
          banner(failure.message, true);
        }
      });
    });
    body.querySelector('[data-act="resend"]')?.addEventListener('click', async () => {
      try {
        await post('/api/account/verify/send', {});
        banner('Письмо с кодом отправлено');
        setTimeout(() => banner(''), 2500);
      } catch (failure) {
        banner(failure.message, true);
      }
    });
    body.querySelector('[data-act="link"]')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      busy(button, async () => {
        try {
          const { data } = await post('/api/account/telegram-link', {});
          body.querySelector('.account-link-code').innerHTML = `
            <p>Отправьте боту команду <code>/link ${esc(data.code)}</code> — код действует 15 минут.</p>
            ${data.url ? `<a class="btn" href="${esc(data.url)}" target="_blank" rel="noopener">Открыть бота</a>` : ''}`;
          button.hidden = true;
        } catch (failure) {
          banner(failure.message, true);
        }
      });
    });
    body.querySelector('[data-act="sgo"]')?.addEventListener('click', () => showSgoLogin(info));
    body.querySelector('[data-act="logout"]')?.addEventListener('click', async () => {
      if (!window.confirm('Выйти на этом устройстве? Войти снова можно по почте и паролю.')) return;
      await post('/api/account/logout', {}).catch(() => {});
      reload();
    });
  };

  draw();
}
