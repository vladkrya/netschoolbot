// Вход на сайт без Telegram (почта и пароль) и вход в «Сетевой город» с сайта.
//
// Сервер — app/web/account.py. После входа по почте сервер ставит cookie с
// постоянным токеном, и страница просто перезагружается — дальше всё как при
// входе по ссылке из бота.
'use strict';

import { api, banner, base, esc, initData, tg } from './core.js';
import { notify } from './haptics.js';
import { ICONS } from './icons.js';
import { pushScreen } from './screens.js';
import { setHalloweenAccess } from './appearance.js';

/** Подпись под кнопкой входа: согласие с политикой. Ссылка — в новой
 * вкладке, чтобы не потерять заполненную форму. */
function consent(action = 'Нажимая «Войти»') {
  return `<p class="account-consent">${action}, вы соглашаетесь с
    <a href="${base}/privacy" target="_blank" rel="noopener">политикой конфиденциальности</a>.
    Это неофициальное приложение, оно не связано с ИРТех.</p>`;
}

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

/* ───────────────────────────── Главный экран ───────────────────────────── */

const FEATURES = [
  ['bell', 'Оценки сразу', 'Новая оценка, домашка или замена урока — уведомление в Telegram или push.'],
  ['calc', 'Сколько до пятёрки', 'Средний балл с весами и расчёт: какие оценки нужны до нужной итоговой.'],
  ['sparkles', 'ИИ-помощник', 'Подскажет, что повторить, и поможет написать письмо учителю.'],
  ['users', 'Для родных', 'Пригласите родителей: уведомления и просмотр дневника без пароля.'],
  ['calendar', 'Календарь', 'Расписание и задания — в календаре телефона или компьютера.'],
  ['shield', 'PIN и Face ID', 'СГОшка под замком и без интернета: последнее загруженное всегда под рукой.'],
];

// Макет дня в превью: предмет, время и оценка, которая «прилетает».
const DEMO_LESSONS = [
  ['Алгебра', '8:30', '5', 'g5'],
  ['Русский язык', '9:25', '4', 'g4'],
  ['Биология', '10:20', '5', 'g5'],
  ['История', '11:25', '', ''],
];

let landingConfig = null;

function heroHtml() {
  const lessons = DEMO_LESSONS.map(([subject, time, mark, tone], index) => `
    <div class="ld-lesson" style="--i:${index}">
      <span class="ld-no">${index + 1}</span>
      <span class="ld-subject">${subject}<small>${time}</small></span>
      ${mark ? `<span class="ld-mark ${tone}">${mark}</span>` : '<span class="ld-dot"></span>'}
    </div>`).join('');
  const features = FEATURES.map(([icon, title, text], index) => `
    <li class="ld-feature" style="--i:${index}">
      <span class="ld-icon" aria-hidden="true">${ICONS[icon]}</span>
      <b>${title}</b><span>${text}</span>
    </li>`).join('');
  return `
    <section class="ld-hero">
      <div class="ld-brand"><img src="${base}/static/icon-192.png" alt="" width="36" height="36"> СГОшка</div>
      <h1>«Сетевой город» —<br><span class="ld-grad">удобно и быстро</span></h1>
      <p class="ld-lead">Оценки, домашние задания и расписание из вашего электронного дневника
        в одном красивом приложении. Работает в Telegram, в браузере и на домашнем экране телефона.</p>
      <p class="ld-note">Неофициальное приложение — не сайт «Сетевого города» и не ИРТех.</p>
      <div class="ld-actions">
        <button class="btn ld-demo" type="button" data-landing-route="demo">▶ Демо-режим</button>
        <button class="btn outline ld-status" type="button" data-landing-route="status"><i class="ld-status-dot" aria-hidden="true"></i>Статус серверов</button>
      </div>
    </section>
    <div class="ld-preview" aria-hidden="true">
      <div class="ld-stage">
        <div class="ld-toast"><span class="ld-toast-icon">${ICONS.bell}</span><div><b>Новая оценка</b><small>Биология · 5</small></div></div>
        <div class="ld-phone">
          <div class="ld-phone-head"><b>Сегодня</b><span>понедельник</span></div>
          ${lessons}
          <div class="ld-avg"><span>Средний по алгебре</span><b class="ld-count">4,83</b></div>
        </div>
      </div>
    </div>
    <ul class="ld-features">${features}</ul>`;
}

/** Вход, регистрация и сброс — на главном экране: слева о проекте, справа
 * карточка. При переходе между ними меняется только карточка, а описание с
 * анимациями остаётся на месте и не проигрывается заново. */
function landing(html) {
  setHalloweenAccess(false);
  document.getElementById('bootSplash')?.remove();
  let root = document.getElementById('accountScreen');
  if (!root?.classList.contains('landing')) {
    root?.remove();
    root = document.createElement('div');
    root.className = 'app-lock account-screen landing';
    root.id = 'accountScreen';
    root.innerHTML = `
      <div class="ld-bg" aria-hidden="true"><i></i><i></i><i></i></div>
      <div class="ld-grid">
        ${heroHtml()}
        <aside class="ld-auth"><div class="app-lock-card account-card"></div></aside>
      </div>
      <footer class="ld-foot">Неофициальный проект, не связан с ИРТех — разработчиком «Сетевого города».
        Данные берутся из вашего дневника, пароль хранится зашифрованным.
        <a class="ld-privacy" href="${base}/privacy" data-landing-route="privacy">Политика конфиденциальности</a></footer>`;
    document.body.appendChild(root);
    document.documentElement.classList.add('app-locked');
    bindLanding(root);
  }
  const card = root.querySelector('.account-card');
  card.innerHTML = html;
  showBotLinks(root);
  card.classList.remove('ld-swap');
  void card.offsetWidth; // перезапуск анимации смены карточки
  card.classList.add('ld-swap');
  return card;
}

async function bindLanding(root) {
  // В iOS WebView обычные ссылки внутри полноэкранного экрана входа иногда
  // остаются без click после касания. Кнопка и явный переход работают и в
  // браузере, и в Telegram, а base сохраняет путь установки приложения.
  root.addEventListener('click', (event) => {
    const button = event.target.closest('[data-landing-route]');
    const route = button?.dataset.landingRoute;
    if (!button || !root.contains(button) || !['demo', 'status', 'privacy'].includes(route)) return;
    event.preventDefault();
    location.assign(`${base}/${route}`);
  });
  try {
    landingConfig ??= (await api('/api/account/config')).data;
  } catch {
    landingConfig = {};
  }
  showBotLinks(root);
}

/** Вход через бота — когда бот доступен; через виджет — на домене из BotFather. */
function showBotLinks(root) {
  if (landingConfig?.oidc || landingConfig?.widget) {
    root.querySelectorAll('[data-landing-widget]').forEach((element) => { element.hidden = false; });
    root.querySelectorAll('.ld-tg-login[data-landing-bot]').forEach((element) => element.classList.add('ld-tg-alt'));
  }
  if (landingConfig?.yandex) {
    root.querySelectorAll('[data-landing-yandex]').forEach((element) => { element.hidden = false; });
  }
  if (landingConfig?.passkey) {
    import('./passkey.js').then(({ passkeysSupported }) => {
      if (passkeysSupported()) root.querySelectorAll('[data-passkey-login]').forEach((element) => { element.hidden = false; });
    });
  }
  if (!landingConfig?.bot) return;
  root.querySelectorAll('[data-landing-bot]').forEach((element) => { element.hidden = false; });
}

function reload() {
  location.replace(`${base}/`);
}

/** Вошли (по почте или через бота): прежний выход больше не действует. */
function signedIn() {
  try { localStorage.removeItem('ns-signed-out'); } catch { /* приватный режим */ }
  location.replace(`${base}/?refresh=1`);
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

export function showAuth(view = 'login', email = '') {
  if (location.hash.includes('tgAuthResult=')) {
    finishWidgetLogin();
    return new Promise(() => {});
  }
  const views = { login: renderLogin, register: renderRegister, reset: renderReset };
  views[view](email);
  const hash = new URLSearchParams(location.hash.slice(1));
  const oidcError = hash.get('tgLoginError');
  const yandexError = hash.get('yaLoginError');
  if (oidcError || yandexError) {
    history.replaceState(null, '', location.pathname + location.search);
    const errors = yandexError ? YANDEX_ERRORS : OIDC_ERRORS;
    showError(document.querySelector('#accountScreen .account-card'), errors[yandexError || oidcError] || errors.failed);
  }
  return new Promise(() => {}); // страница перезагрузится после входа
}

function renderLogin(email = '') {
  const card = landing(`
    <h2>Вход в дневник</h2>
    <button class="btn ld-tg-login ld-tg-widget" type="button" data-landing-widget hidden>${TG_ICON}
      Войти через Telegram</button>
    <button class="btn ld-tg-login" type="button" data-landing-bot hidden>${TG_ICON}
      Войти через Telegram-бота</button>
    <div data-landing-yandex hidden>
      <a class="btn ld-ya-login" href="${base}/api/account/yandex/start">${YA_ICON} Войти через Яндекс ID</a>
      ${consent('Нажимая «Войти через Яндекс ID»')}
    </div>
    <div class="ld-alt-logins">
      <button class="btn outline ld-alt-login" type="button" data-qr-login>${ICONS.qr} По QR-коду</button>
      <button class="btn outline ld-alt-login" type="button" data-passkey-login hidden>${ICONS.key} Ключом входа</button>
    </div>
    <p class="ld-or"><span>или по почте</span></p>
    <form class="account-form">
      <input class="field" type="email" name="email" autocomplete="username" placeholder="Почта" required>
      <input class="field" type="password" name="password" autocomplete="current-password" placeholder="Пароль" required>
      <p class="account-error" hidden></p>
      <button class="btn" type="submit">Войти</button>
      ${consent()}
    </form>
    <div class="account-links">
      <button class="link" data-go="register">Регистрация</button>
      <button class="link" data-go="reset">Забыли пароль?</button>
    </div>
    <a class="ld-demo-link" href="${base}/demo">Без входа — <b>попробовать демо-режим</b> на выдуманном ученике</a>`);
  bindLinks(card);
  fillEmail(card, email);
  card.querySelector('[data-landing-bot]').addEventListener('click', (event) => startBotLogin(event.currentTarget));
  card.querySelector('[data-landing-widget]').addEventListener('click', startWidgetLogin);
  card.querySelector('[data-qr-login]').addEventListener('click', (event) => busy(event.currentTarget, startQrLogin));
  card.querySelector('[data-passkey-login]').addEventListener('click', (event) =>
    busy(event.currentTarget, () => passkeyLogin(card)));
  const form = card.querySelector('form');
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    busy(form.querySelector('button'), async () => {
      try {
        await post('/api/account/login', { email: form.email.value, password: form.password.value });
        signedIn();
      } catch (error) {
        showError(card, error.message);
      }
    });
  });
}

function renderRegister(email = '') {
  const card = landing(`
    <h2>Регистрация</h2>
    <p class="muted">${hasInvite() ? 'Аккаунт, чтобы принять приглашение. Пароль от «Сетевого города» не понадобится.'
      : 'Аккаунт для входа на сайт без Telegram. Потом войдёте в «Сетевой город» — как в боте.'}</p>
    <form class="account-form">
      <input class="field" type="email" name="email" autocomplete="username" placeholder="Почта" required>
      <input class="field" type="password" name="password" autocomplete="new-password" placeholder="Пароль (от 8 символов)" required>
      <input class="field" type="password" name="again" autocomplete="new-password" placeholder="Пароль ещё раз" required>
      <p class="account-error" hidden></p>
      <button class="btn" type="submit">Создать аккаунт</button>
      ${consent('Создавая аккаунт')}
    </form>
    <div class="account-links"><button class="link" data-go="login">Уже есть аккаунт</button></div>`);
  bindLinks(card);
  fillEmail(card, email);
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
        signedIn();
      } catch (error) {
        showError(card, error.message);
      }
    });
  });
}

function renderReset(email = '') {
  const card = landing(`
    <h2>Сброс пароля</h2>
    <p class="muted">Пришлём ссылку на почту — или в Telegram, если он привязан. По ней можно задать новый пароль.</p>
    <form class="account-form">
      <input class="field" type="email" name="email" autocomplete="username" placeholder="Почта" required>
      <p class="account-error" hidden></p>
      <button class="btn" type="submit">Прислать ссылку</button>
    </form>
    <div class="account-links"><button class="link" data-go="login">Назад ко входу</button></div>`);
  bindLinks(card);
  fillEmail(card, email);
  const form = card.querySelector('form');
  const button = form.querySelector('button');
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    busy(button, async () => {
      try {
        const { data } = await post('/api/account/reset/request', { email: form.email.value });
        showError(card, '');
        button.textContent = 'Прислать ещё раз';
        card.querySelector('.muted').textContent = data.channel === 'mail'
          ? 'Если такая почта зарегистрирована, ссылка уже в письме. Проверьте и «Спам». Она действует 30 минут.'
          : 'Если к аккаунту привязан Telegram, ссылка пришла от бота. Без Telegram сброс пока недоступен — напишите администратору.';
      } catch (error) {
        showError(card, error.message);
      }
    });
  });
}

/** Открыли ссылку из письма: #reset=<секрет>. Новый пароль дважды. */
export async function showResetLink(secret) {
  // Секрет из адреса убираем сразу: не останется в истории и в закладке.
  history.replaceState(null, '', location.pathname + location.search);
  let email = '';
  try {
    ({ data: { email } } = await api(`/api/account/reset/check?token=${encodeURIComponent(secret)}`));
  } catch (error) {
    const card = landing(`
      <h2>Сброс пароля</h2>
      <p class="account-error"></p>
      <button class="btn" type="button" data-go="reset">Прислать новую ссылку</button>
      <div class="account-links"><button class="link" data-go="login">Ко входу</button></div>`);
    card.querySelector('.account-error').textContent = error.message;
    bindLinks(card);
    return new Promise(() => {});
  }
  const card = landing(`
    <h2>Новый пароль</h2>
    <p class="muted">Для <b>${esc(email)}</b>. После смены остальные устройства выйдут из аккаунта.</p>
    <form class="account-form">
      <input type="email" name="email" autocomplete="username" value="${esc(email)}" hidden>
      <input class="field" type="password" name="password" autocomplete="new-password" placeholder="Новый пароль (от 8 символов)" required>
      <input class="field" type="password" name="repeat" autocomplete="new-password" placeholder="Повторите пароль" required>
      <p class="account-error" hidden></p>
      <button class="btn" type="submit">Сохранить пароль</button>
    </form>
    <div class="account-links"><button class="link" data-go="login">Ко входу</button></div>`);
  bindLinks(card);
  const form = card.querySelector('form');
  form.password.focus();
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (form.password.value !== form.repeat.value) { showError(card, 'Пароли не совпадают'); return; }
    busy(form.querySelector('button'), async () => {
      try {
        await post('/api/account/reset/confirm', { token: secret, password: form.password.value });
        signedIn();
      } catch (error) {
        showError(card, error.message);
      }
    });
  });
  return new Promise(() => {}); // страница перезагрузится после смены
}

const YA_ICON = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><circle cx="12" cy="12" r="12" fill="#fc3f1d"/><path fill="#fff" d="M13.3 6.2h-1.1c-2 0-3.1 1-3.1 2.6 0 1.8.8 2.6 2.3 3.6l1.3.9-3.6 5.4H6.4l3.3-4.9c-1.9-1.4-3-2.6-3-4.9 0-2.8 2-4.7 5.6-4.7h3.6v14.5h-2.6z"/></svg>';

const TG_ICON = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M9.8 15.2 9.6 19c.4 0 .6-.2.8-.4l1.9-1.8 4 2.9c.7.4 1.3.2 1.5-.7l2.7-12.6c.3-1.1-.4-1.6-1.1-1.3L3.6 10.9c-1 .4-1 1-.2 1.3l4 1.2 9.3-5.9c.4-.3.8-.1.5.2z"/></svg>';

/* ─────────────────────── Вход через Telegram Login ─────────────────────── */

/** Telegram Login Widget без его скрипта: telegram.org в России бывает
 * недоступен, и виджет на странице просто не появился бы. Переходим на
 * страницу входа Telegram сами, а она возвращает на сайт с подписанными
 * данными в адресе: #tgAuthResult=<base64 JSON>. Подпись проверяет сервер
 * (verify_login_widget в app/web/telegram_auth.py). */
function startWidgetLogin() {
  // Новый вход по OpenID Connect: сервер сам ведёт на Telegram и обратно
  // (app/web/telegram_oidc.py), на странице ничего не делаем.
  if (landingConfig.oidc) {
    location.assign(`${base}/api/account/tg-oidc/start`);
    return;
  }
  const back = `${location.origin}${base}/`;
  location.assign('https://oauth.telegram.org/auth?' + new URLSearchParams({
    bot_id: String(landingConfig.widget), origin: location.origin, return_to: back,
  }));
}

const OIDC_ERRORS = {
  cancel: 'Вход отменён в Telegram.',
  expired: 'Время на вход вышло — нажмите «Войти через Telegram» ещё раз.',
  limit: 'Слишком много попыток входа — подождите 15 минут.',
  off: 'Вход через Telegram сейчас выключен — войдите через бота или по почте.',
  failed: 'Telegram не подтвердил вход — попробуйте ещё раз.',
};

const YANDEX_ERRORS = {
  cancel: 'Вход отменён на странице Яндекса.',
  expired: 'Время на вход вышло — нажмите «Войти через Яндекс ID» ещё раз.',
  limit: 'Слишком много попыток входа — подождите 15 минут.',
  off: 'Вход через Яндекс ID сейчас выключен — войдите другим способом.',
  failed: 'Яндекс не подтвердил вход — попробуйте ещё раз.',
};

function widgetResult() {
  const raw = new URLSearchParams(location.hash.slice(1)).get('tgAuthResult') || '';
  // Адрес с подписью не оставляем в истории и в строке браузера.
  history.replaceState(null, '', location.pathname + location.search);
  try {
    const json = atob(raw.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(raw.length / 4) * 4, '='));
    const data = JSON.parse(new TextDecoder().decode(Uint8Array.from(json, (c) => c.charCodeAt(0))));
    return data && typeof data === 'object' && data.hash ? data : null;
  } catch {
    return null; // tgAuthResult=false — вход отменили на странице Telegram
  }
}

async function finishWidgetLogin() {
  const data = widgetResult();
  if (!data) {
    renderLogin();
    return;
  }
  const card = landing(`
    <h2>Входим…</h2>
    <div class="account-wait"><span class="boot-spinner" aria-hidden="true"></span></div>`);
  try {
    await post('/api/account/tg-widget', data);
    notify('success');
    signedIn();
  } catch (error) {
    renderLogin();
    showError(document.querySelector('#accountScreen .account-card') || card, error.message);
  }
}

/* ─────────────────────── Привязка Telegram ─────────────────────── */

/** Привязка тем же входом через Telegram, что и на главной: OpenID Connect
 * ведёт сервер, виджет возвращает на /?tglink=1 с #tgAuthResult. */
function startTelegramLink(methods) {
  if (methods.oidc) {
    location.assign(`${base}/api/account/tg-oidc/link`);
    return;
  }
  location.assign('https://oauth.telegram.org/auth?' + new URLSearchParams({
    bot_id: String(methods.widget), origin: location.origin, return_to: `${location.origin}${base}/?tglink=1`,
  }));
}

const LINK_ERRORS = {
  ...OIDC_ERRORS,
  cancel: 'Привязка отменена в Telegram.',
  expired: 'Время на привязку вышло — нажмите «Привязать через Telegram» ещё раз.',
  off: 'Привязка через Telegram сейчас выключена — привяжите кодом в боте.',
};

/** Вернулись из Telegram после привязки: показать итог и открыть настройки.
 * true — страница перезагружается. */
export async function finishTelegramLink() {
  const params = new URLSearchParams(location.search);
  const viaWidget = params.has('tglink');
  const code = new URLSearchParams(location.hash.slice(1)).get('tglink') || '';
  const data = viaWidget ? widgetResult() : null;
  params.delete('tglink');
  const query = params.toString();
  history.replaceState(null, '', `${location.pathname}${query ? `?${query}` : ''}#settings`);
  let message = '';
  if (viaWidget) {
    if (!data) message = LINK_ERRORS.cancel;
    else {
      try {
        await post('/api/account/telegram-link/widget', data);
        // Аккаунт переехал на id Telegram — дневник и настройки заново.
        location.replace(`${base}/?refresh=1#tglink=ok`);
        return true;
      } catch (error) {
        message = error.message;
      }
    }
  } else if (code === 'problem') {
    try {
      message = (await post('/api/account/telegram-link/result', {})).data.problem || LINK_ERRORS.failed;
    } catch {
      message = LINK_ERRORS.failed;
    }
  } else if (code !== 'ok') {
    message = LINK_ERRORS[code] || LINK_ERRORS.failed;
  }
  setTimeout(() => {
    notify(message ? 'error' : 'success');
    banner(message || 'Telegram привязан: уведомления теперь приходят и туда', Boolean(message), message ? 0 : 6000);
  }, 800);
  return false;
}

const YANDEX_LINK_ERRORS = {
  ...YANDEX_ERRORS,
  cancel: 'Привязка отменена на странице Яндекса.',
  expired: 'Время на привязку вышло — нажмите «Привязать Яндекс ID» ещё раз.',
  off: 'Привязка Яндекс ID сейчас выключена.',
  taken: 'Этот Яндекс ID уже привязан к другому аккаунту — войдите через него или отвяжите там.',
};

/** Вернулись с Яндекса после «Привязать Яндекс ID» (#yalink=…): итог и настройки. */
export function finishYandexLink() {
  const code = new URLSearchParams(location.hash.slice(1)).get('yalink') || '';
  history.replaceState(null, '', `${location.pathname}${location.search}#settings`);
  const message = code === 'ok' ? '' : YANDEX_LINK_ERRORS[code] || YANDEX_LINK_ERRORS.failed;
  setTimeout(() => {
    notify(message ? 'error' : 'success');
    banner(message || 'Яндекс ID привязан: через него можно входить на сайт', Boolean(message), message ? 0 : 6000);
  }, 800);
}

/* ─────────────────────── Вход через Telegram-бота ─────────────────────── */

const BOT_POLL_MS = 2000;
let botPoll = 0;

/** Открыть бота с одноразовым кодом и ждать, пока вход подтвердят там
 * (app/accounts/site_login.py). Окно открываем сразу, в обработчике нажатия:
 * открытое после запроса браузеры блокируют как всплывающее. */
async function startBotLogin(button) {
  const popup = window.open('', '_blank');
  if (popup) popup.opener = null;
  let data;
  try {
    button.disabled = true;
    ({ data } = await post('/api/account/tg-login', {}));
  } catch (error) {
    popup?.close();
    button.disabled = false;
    showError(button.closest('.account-card'), error.message);
    return;
  }
  if (popup) popup.location.href = data.url;
  const botUrl = new URL(data.url);
  const nativeUrl = 'tg://resolve?' + new URLSearchParams({
    domain: botUrl.pathname.slice(1), start: botUrl.searchParams.get('start') || '',
  });
  const card = landing(`
    <h2>Подтвердите вход</h2>
    <p class="muted">В Telegram открылся бот — нажмите там <b>«✅ Войти»</b>. Эта страница откроет дневник сама.</p>
    <div class="account-wait"><span class="boot-spinner" aria-hidden="true"></span></div>
    <p class="account-error" hidden></p>
    <a class="btn ld-tg-login" href="${esc(nativeUrl)}">Открыть Telegram</a>
    <a class="link" href="${esc(data.url)}" target="_blank" rel="noopener">Если не открылось — открыть ссылку в браузере</a>
    <div class="account-links"><button class="link" data-go="login">Назад</button></div>`);
  bindLinks(card);
  // Ссылка остаётся на экране, даже если браузер запретил новое окно.
  waitForBot(card, data.code);
  if (!popup && tg?.openTelegramLink) tg.openTelegramLink(data.url);
}

function waitForBot(card, code, { declined = 'Вход отменён в боте.', expired = 'Время на подтверждение вышло — попробуйте ещё раз.', onEnd } = {}) {
  clearTimeout(botPoll);
  const marker = card.querySelector('.account-wait');
  let running = false;
  const stop = () => {
    clearTimeout(botPoll);
    document.removeEventListener('visibilitychange', resume);
    window.removeEventListener('pageshow', resume);
    window.removeEventListener('focus', resume);
  };
  const resume = () => { if (!document.hidden) { clearTimeout(botPoll); tick(); } };
  const tick = async () => {
    if (!marker?.isConnected) { stop(); return; }
    if (running) return;
    running = true;
    try {
      const { data } = await api(`/api/account/tg-login?code=${encodeURIComponent(code)}`);
      if (data.status === 'pending') {
        botPoll = setTimeout(tick, BOT_POLL_MS);
      } else if (data.token) {
        stop();
        notify('success');
        signedIn();
      } else {
        stop();
        marker.remove();
        showError(card, data.status === 'declined' ? declined : expired);
        onEnd?.();
      }
    } catch {
      botPoll = setTimeout(tick, BOT_POLL_MS * 2); // сеть моргнула — пробуем дальше
    } finally { running = false; }
  };
  document.addEventListener('visibilitychange', resume);
  window.addEventListener('pageshow', resume);
  window.addEventListener('focus', resume);
  tick();
}

/* ─────────────────── По QR-коду и ключом входа ─────────────────── */

/** Вход по QR-коду: его сканируют там, где дневник уже открыт, и
 * подтверждают там же (app/web/devices.py, qr_login_approve). Ждём, как
 * вход через бота. */
async function startQrLogin() {
  let data;
  try {
    ({ data } = await post('/api/account/qr-login', {}));
  } catch (error) {
    showError(document.querySelector('#accountScreen .account-card'), error.message);
    return;
  }
  const card = landing(`
    <h2>Вход по QR-коду</h2>
    <p class="muted">Отсканируйте код там, где дневник уже открыт, и подтвердите вход:</p>
    <ul class="ld-qr-steps">
      <li><b>в Telegram</b> — дневник → меню → «Устройства» → «Сканировать QR-код»;</li>
      <li><b>в браузере на телефоне</b> — обычной камерой.</li>
    </ul>
    ${data.qr ? `<img class="device-qr" src="${data.qr}" alt="QR-код для входа" width="220" height="220">` : ''}
    <div class="account-wait"><span class="boot-spinner" aria-hidden="true"></span></div>
    <p class="account-error" hidden></p>
    <button class="btn" type="button" data-qr-again hidden>Новый QR-код</button>
    <div class="account-links"><button class="link" data-go="login">Назад</button></div>`);
  bindLinks(card);
  const again = card.querySelector('[data-qr-again]');
  again.addEventListener('click', () => busy(again, startQrLogin));
  waitForBot(card, data.code, {
    declined: 'Вход отклонён на другом устройстве.',
    expired: 'QR-код устарел — он действует 10 минут.',
    onEnd: () => { card.querySelector('.device-qr')?.remove(); again.hidden = false; },
  });
}

async function passkeyLogin(card) {
  const { usePasskey, cancelled } = await import('./passkey.js');
  showError(card, '');
  try {
    const { data } = await post('/api/account/passkey/options', {});
    const credential = await usePasskey(data.options);
    await post('/api/account/passkey/login', { id: data.id, credential });
    notify('success');
    signedIn();
  } catch (error) {
    if (!cancelled(error)) showError(card, error.message);
  }
}

/** Открыли QR-код с экрана входа (/?qr=…) там, где дневник не открыт —
 * например, камерой iPhone, а дневник только в Telegram. Подтвердить можно
 * в боте: код тот же, что у входа через бота (app/accounts/site_login.py). */
export async function showQrWithoutSignIn(code) {
  history.replaceState(null, '', location.pathname);
  const card = landing(`
    <h2>Вход на другом устройстве</h2>
    <p class="muted">Этот QR-код подтверждает вход в дневник на другом устройстве, но здесь вы ещё не вошли.
      Отсканируйте его там, где дневник уже открыт: в Telegram — меню → «Устройства» → «Сканировать QR-код».</p>
    <a class="btn ld-tg-login" data-qr-bot hidden>${TG_ICON} Подтвердить в Telegram</a>
    <div class="account-links"><button class="link" data-go="login">Войти здесь</button></div>`);
  bindLinks(card);
  try {
    landingConfig ??= (await api('/api/account/config')).data;
  } catch { /* без бота — только сканировать */ }
  const bot = card.querySelector('[data-qr-bot]');
  if (landingConfig?.bot && bot.isConnected) {
    bot.href = `${landingConfig.bot}?start=web_${encodeURIComponent(code)}`;
    bot.hidden = false;
  }
  return new Promise(() => {});
}

/** Переходы между входом, регистрацией и сбросом: введённая почта едет дальше. */
function bindLinks(card) {
  card.querySelectorAll('[data-go]').forEach((link) => link.addEventListener('click', () =>
    showAuth(link.dataset.go, card.querySelector('input[name="email"]')?.value.trim() || '')));
}

function fillEmail(card, email) {
  const field = card.querySelector('input[name="email"]');
  if (field && email) field.value = email;
}

/* ─────────────────────── Приглашение от ученика ─────────────────────── */

const INVITE_KEY = 'ns-invite';

function hasInvite() {
  try { return Boolean(localStorage.getItem(INVITE_KEY) || window.netschoolInvite); } catch { return Boolean(window.netschoolInvite); }
}

function forgetInvite() {
  try { localStorage.removeItem(INVITE_KEY); } catch { /* приватный режим */ }
  window.netschoolInvite = '';
}

function rightsText(invite) {
  if (invite.notify && invite.view) return 'уведомления об оценках и заданиях и просмотр дневника';
  return invite.notify ? 'уведомления об оценках и заданиях' : 'просмотр дневника: оценки, расписание, задания';
}

/** Ссылка-приглашение (/?invite=…): кто приглашает и что откроется. Без
 * входа — войти через Telegram или по почте (код ждёт в localStorage до
 * перезагрузки после входа), со входом — подтвердить. */
export async function showInvite(code, signedIn) {
  const card = screen('<h2>Приглашение</h2><p class="muted">Загрузка…</p>');
  let invite;
  try {
    invite = (await api(`/api/family/invite-info?code=${encodeURIComponent(code)}`)).data;
  } catch (error) {
    forgetInvite();
    card.innerHTML = `<h2>Приглашение</h2><p class="account-error">${esc(error.message)}</p>
      <button class="btn" data-act="go">${signedIn ? 'Открыть дневник' : 'Войти'}</button>`;
    card.querySelector('[data-act="go"]').addEventListener('click', () => (signedIn ? reload() : showAuth()));
    return new Promise(() => {});
  }
  const who = `<b>${esc(invite.name)}</b>${invite.school ? ` <span class="muted">(${esc(invite.school)})</span>` : ''}`;
  card.innerHTML = `
    <h2>Приглашение</h2>
    <p>${who} приглашает вас. Будет доступно: ${rightsText(invite)}.</p>
    <p class="muted small">Пароль от «Сетевого города» не нужен, менять что-либо в дневнике нельзя. Отказаться можно в любой момент.</p>
    <p class="account-error" hidden></p>
    <div class="invite-actions">
      ${signedIn ? '<button class="btn" data-act="accept">Принять</button>' : `
        ${invite.telegram ? '<button class="btn" data-act="telegram">Через Telegram</button>' : ''}
        <button class="btn ${invite.telegram ? 'outline' : ''}" data-act="register">Зарегистрироваться по почте</button>
        <button class="link" data-act="login">Уже есть аккаунт — войти</button>`}
      <button class="link" data-act="decline">Отказаться</button>
    </div>`;
  // Ссылку открываем в этой же вкладке: во встроенном браузере Telegram (а
  // приглашение обычно открывают именно из чата) target=_blank на t.me
  // ничего не делал. Переход в том же окне Telegram перехватывает и открывает
  // бота, Safari и Chrome — передают приложению Telegram.
  card.querySelector('[data-act="telegram"]')?.addEventListener('click', () => {
    // Приглашение принимает бот; забываем его, только когда Telegram и правда
    // открылся — иначе после «Зарегистрироваться по почте» оно бы потерялось.
    document.addEventListener('visibilitychange', forgetInvite, { once: true });
    if (initData && tg?.openTelegramLink) tg.openTelegramLink(invite.telegram);
    else location.href = invite.telegram;
    // Telegram не открылся (не установлен) — даём ссылку, чтобы открыть её вручную.
    setTimeout(() => {
      if (document.visibilityState !== 'visible' || card.querySelector('.invite-fallback')) return;
      card.querySelector('.invite-actions').insertAdjacentHTML('afterbegin', `<p class="muted small invite-fallback">
        Не открылось? Установите Telegram или откройте <a href="${esc(invite.telegram)}">${esc(invite.telegram)}</a>.</p>`);
    }, 2500);
  });
  card.querySelector('[data-act="register"]')?.addEventListener('click', () => showAuth('register'));
  card.querySelector('[data-act="login"]')?.addEventListener('click', () => showAuth('login'));
  card.querySelector('[data-act="decline"]').addEventListener('click', () => {
    forgetInvite();
    if (signedIn) reload(); else showAuth();
  });
  card.querySelector('[data-act="accept"]')?.addEventListener('click', (event) => busy(event.currentTarget, async () => {
    try {
      await post('/api/family/accept', { code });
      forgetInvite();
      notify('success');
      reload();
    } catch (error) {
      notify('error');
      showError(card, error.message);
    }
  }));
  return new Promise(() => {});
}

async function eraseAccount(password) {
  await post('/api/account/delete', { password });
  const { clearOffline } = await import('./lock.js');
  await clearOffline().catch(() => {});
  try { localStorage.setItem('ns-signed-out', window.NETSCHOOL.token || '1'); } catch { /* приватный режим */ }
  reload();
}

function showDeleteAccount(back) {
  const card = screen(`<h2>Удалить аккаунт</h2>
    <p>Удалятся профиль, почта, записи, история, сессии и серверный календарь. Отменить удаление нельзя.</p>
    <p class="muted">Данные в школе, сообщения Telegram и резервные копии останутся.</p>
    <form class="account-form"><input class="field" type="password" name="password" autocomplete="current-password" placeholder="Пароль аккаунта" required>
    <p class="account-error" hidden></p><button class="btn danger" type="submit">Удалить все мои данные</button></form>
    <button class="link" data-back>Отмена</button>`);
  card.querySelector('[data-back]').onclick = back;
  card.querySelector('form').onsubmit = (event) => {
    event.preventDefault();
    if (!confirm('Удалить аккаунт и все данные без возможности отмены?')) return;
    busy(event.currentTarget.querySelector('button'), async () => {
      try { await eraseAccount(card.querySelector('[name="password"]').value); }
      catch (error) { showError(card, error.message); }
    });
  };
}

/** Открыть бота сразу в приложении Telegram. Ссылка https://t.me в новой
 * вкладке Safari (особенно в приватном режиме и на экране «Домой») часто
 * остаётся страницей t.me и в приложение не переходит; tg:// открывает его
 * напрямую, а если Telegram не установлен — через секунду идём на t.me. */
export function openTelegram(url) {
  const parsed = new URL(url);
  const domain = parsed.pathname.replace(/^\//, '');
  const start = parsed.searchParams.get('start');
  const fallback = setTimeout(() => { if (!document.hidden) location.href = url; }, 1200);
  document.addEventListener('visibilitychange', () => clearTimeout(fallback), { once: true });
  location.href = `tg://resolve?domain=${encodeURIComponent(domain)}${start ? `&start=${encodeURIComponent(start)}` : ''}`;
}

/* ─────────────────────── Подтверждение почты ─────────────────────── */

/** Сразу после регистрации: без кода из письма дальше не пускаем. */
export function showVerify(info = {}) {
  const card = screen(`<h2>Подтвердите почту</h2>
    <p>Мы отправили код на <b>${esc(info.email || '')}</b>. Введите его, чтобы продолжить.</p>
    <p class="muted">Письма нет — проверьте «Спам» или пришлите код ещё раз.</p>
    <form class="account-form">
      <input class="field" name="code" inputmode="numeric" autocomplete="one-time-code" placeholder="Код из письма" required>
      <p class="account-error" hidden></p>
      <button class="btn" type="submit">Подтвердить</button>
    </form>
    <div class="account-links">
      <button class="link" data-act="resend">Прислать код ещё раз</button>
      <button class="link" data-act="logout">Выйти из аккаунта</button>
    </div>`);
  const form = card.querySelector('form');
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    busy(form.querySelector('button'), async () => {
      try {
        await post('/api/account/verify', { code: form.code.value.trim() });
        notify('success');
        reload();
      } catch (error) {
        notify('error');
        showError(card, error.message);
      }
    });
  });
  card.querySelector('[data-act="resend"]').addEventListener('click', (event) => {
    busy(event.currentTarget, async () => {
      try {
        await post('/api/account/verify/send', {});
        showError(card, '');
        banner('Письмо с кодом отправлено');
        setTimeout(() => banner(''), 2500);
      } catch (error) {
        showError(card, error.message);
      }
    });
  });
  card.querySelector('[data-act="logout"]').addEventListener('click', async () => {
    await post('/api/account/logout', {}).catch(() => {});
    reload();
  });
  return new Promise(() => {}); // страница перезагрузится после подтверждения
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
      ${!info.telegram && info.needs_sgo ? '<button class="link" data-act="logout">Выйти из аккаунта</button><button class="link danger" data-act="delete">Удалить аккаунт</button>' : ''}
    </div>`;
  const bindFooter = (card) => {
    card.querySelector('[data-act="delete"]')?.addEventListener('click', () => showDeleteAccount(regionStep));
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
      <p class="muted">Шаг 2 из 3. ${esc(choice.region || choice.url)}: найдите свою школу.</p>
      <form class="account-form account-row">
        <input class="field" name="q" placeholder="Название или номер школы" required>
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
      ${consent('Выбирая способ входа')}
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
        ${esia ? '<p class="account-note">Логин и пароль Госуслуг нужны только на этот вход и не сохраняются.</p>'
          : `<label class="account-check"><input type="checkbox" name="remember" checked>
          <span>Запомнить для повторного входа — дневник сам войдёт, когда сессия истечёт</span></label>`}
        <p class="account-error" hidden></p>
        <button class="btn" type="submit">Войти</button>
        ${consent()}
      </form>
      <button class="link" data-act="back">Другой способ</button>`);
    card.querySelector('[data-act="back"]').addEventListener('click', methodStep);
    const form = card.querySelector('form');
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      start({ login: form.login.value, password: form.password.value, remember: !esia && form.remember.checked }, card);
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
        <input class="field" type="email" name="email" autocomplete="username" placeholder="Почта" value="${esc(info.email || info.suggested_email || '')}" required>
        ${withCurrent ? '<input class="field" type="password" name="current" autocomplete="current-password" placeholder="Текущий пароль" required>' : ''}
        <input class="field" type="password" name="password" autocomplete="new-password" placeholder="${withCurrent ? 'Новый пароль (от 8 символов)' : 'Пароль (от 8 символов)'}" required>
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
          : `<p class="muted">Задайте почту и пароль — и на сайт можно будет входить без Telegram. Если почта уже зарегистрирована, введите её текущий пароль: аккаунты объединятся с сохранением дневника Telegram и записей.</p>${emailForm('Почта и пароль', false)}`}
        ${info.number ? `<p class="muted small">ID аккаунта: <b>${esc(String(info.number))}</b></p>` : ''}
      </div>
      ${info.email ? `<div class="card">${emailForm('Сменить почту или пароль', true)}</div>` : ''}
      ${info.telegram ? '' : `
        <div class="card">
          <div class="card-title">Telegram</div>
          <p class="muted">Привяжите Telegram — уведомления будут приходить и туда, а в дневник можно будет заходить из бота. Если там уже есть этот дневник, аккаунты объединятся: настройки Telegram останутся, записи и входы сохранятся.</p>
          <div class="account-link-code"></div>
          ${info.tg_link ? `
            <button class="btn account-tg-link" data-act="link-telegram">${TG_ICON} Привязать через Telegram</button>
            <button class="link" data-act="link">Не открывается? Привязать кодом в боте</button>`
            : '<button class="btn outline" data-act="link">Привязать Telegram</button>'}
        </div>`}
      ${info.yandex?.enabled || info.yandex?.linked ? `
        <div class="card">
          <div class="card-title">Яндекс ID</div>
          ${info.yandex.linked ? `
            <p>Привязан — через него можно входить на сайт.</p>
            ${info.email || info.telegram ? '<button class="btn outline" data-act="unlink-yandex">Отвязать Яндекс ID</button>' : ''}`
            : `<p class="muted">Привяжите Яндекс ID — и на сайт можно будет входить через него. От Яндекса придут только id и имя.</p>
            <button class="btn ld-ya-login" data-act="link-yandex">${YA_ICON} Привязать Яндекс ID</button>`}
        </div>` : ''}
      ${!info.email && !info.telegram ? `<div class="card"><div class="card-title">Удалить аккаунт</div>
        <p class="muted">Удалятся профиль, записи, сессии, история и серверный календарь. Данные в школе и резервные копии останутся.</p>
        <button class="btn outline danger" data-act="delete-nopass">Удалить все мои данные</button></div>` : ''}
      ${info.email ? `<div class="card"><div class="card-title">Удалить аккаунт</div>
        <p class="muted">Удалятся профиль, почта, записи, сессии, история и серверный календарь. Данные в школе, сообщения Telegram и резервные копии останутся.</p>
        <form class="account-form" data-form="delete"><input class="field" type="password" name="password" autocomplete="current-password" placeholder="Пароль аккаунта" required>
        <p class="account-error" hidden></p><button class="btn outline danger" type="submit">Удалить все мои данные</button></form></div>` : ''}
      ${!tg?.initData ? '<button class="btn outline danger account-logout" data-act="logout">Выйти из аккаунта</button>' : ''}`;
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
    form('delete')?.addEventListener('submit', (event) => {
      event.preventDefault();
      const element = event.currentTarget;
      if (!confirm('Удалить аккаунт и все данные без возможности отмены?')) return;
      busy(element.querySelector('button'), async () => {
        try {
          await eraseAccount(element.password.value);
        } catch (failure) { error(element, failure.message); }
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
    body.querySelector('[data-act="link-telegram"]')?.addEventListener('click', () => startTelegramLink(info.tg_link));
    body.querySelector('[data-act="link-yandex"]')?.addEventListener('click', () => {
      location.assign(`${base}/api/account/yandex/link`);
    });
    body.querySelector('[data-act="unlink-yandex"]')?.addEventListener('click', (event) => {
      if (!confirm('Отвязать Яндекс ID? Входить через него больше не получится.')) return;
      busy(event.currentTarget, async () => {
        try {
          await post('/api/account/yandex/unlink', {});
          draw();
        } catch (failure) { banner(failure.message, true); }
      });
    });
    body.querySelector('[data-act="delete-nopass"]')?.addEventListener('click', (event) => {
      if (!confirm('Удалить аккаунт и все данные без возможности отмены?')) return;
      busy(event.currentTarget, async () => {
        try { await eraseAccount(''); } catch (failure) { banner(failure.message, true); }
      });
    });
    body.querySelector('[data-act="link"]')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      busy(button, async () => {
        try {
          const { data } = await post('/api/account/telegram-link', {});
          const command = `/link ${data.code}`;
          const box = body.querySelector('.account-link-code');
          box.innerHTML = `
            <p>${data.url ? 'Нажмите кнопку — бот откроется и сам привяжет аккаунт. Если не вышло, отправьте ему' : 'Отправьте боту'}
              команду <code>${esc(command)}</code> — код действует 15 минут.</p>
            ${data.url ? `<a class="btn" href="${esc(data.url)}" target="_blank" rel="noopener" data-act="open-bot">Скопировать и открыть Telegram</a>` : ''}
            <button class="btn outline" type="button" data-act="copy-link">Скопировать команду</button>`;
          // Команда в буфере на случай, если бот откроется без старта.
          const copyCommand = () => navigator.clipboard?.writeText(command).catch(() => {});
          box.querySelector('[data-act="open-bot"]')?.addEventListener('click', (event) => {
            event.preventDefault();
            copyCommand();
            openTelegram(data.url);
          });
          box.querySelector('[data-act="copy-link"]').addEventListener('click', async () => {
            await copyCommand();
            notify('success');
            banner('Команда скопирована');
            setTimeout(() => banner(''), 2000);
          });
          button.hidden = true;
        } catch (failure) {
          banner(failure.message, true);
        }
      });
    });
    body.querySelector('[data-act="logout"]')?.addEventListener('click', async () => {
      const { signOut } = await import('./profile.js');
      signOut();
    });
  };

  draw();
}
