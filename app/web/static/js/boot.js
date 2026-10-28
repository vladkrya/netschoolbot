// Быстрый старт: PIN и проверка доступа не ждут загрузки всех экранов приложения.
import { api, demo, setToken, state, tg, token } from './core.js';
import { unlockApp } from './lock.js';
import { initIosHaptics } from './haptics.js';

const INVITE_KEY = 'ns-invite';

/** Код из ссылки-приглашения (/?invite=…) — до входа и после него: после
 * регистрации по почте страница перезагружается уже без кода в адресе. */
function rememberInvite() {
  const params = new URLSearchParams(location.search);
  const code = params.get('invite');
  if (!code) return;
  try { localStorage.setItem(INVITE_KEY, code); } catch { /* приватный режим */ }
  window.netschoolInvite = code;
  params.delete('invite');
  const query = params.toString() ? `?${params}` : '';
  history.replaceState(null, '', location.pathname + query + location.hash);
}

function pendingInvite() {
  try { return localStorage.getItem(INVITE_KEY) || window.netschoolInvite || ''; } catch { return window.netschoolInvite || ''; }
}

const VIEWING_KEY = 'ns-viewing';
// Сколько ждать /api/account на старте. Service worker его не кэширует, а
// мобильный интернет с белыми списками не обрывает запрос, а держит его —
// без предела заставка «Открываем дневник…» стояла больше минуты, хотя
// сохранённый дневник показать можно.
const ACCOUNT_WAIT_MS = 4000;

/** Аккаунт для старта; сервер медлит — прошлый признак просмотра, без входа в СГО. */
async function accountInfo() {
  const request = api('/api/account').then(({ data }) => {
    try { localStorage.setItem(VIEWING_KEY, data?.viewing ? '1' : '0'); } catch { /* приватный режим */ }
    return data;
  }, () => null);
  const late = new Promise(resolve => setTimeout(() => resolve(undefined), ACCOUNT_WAIT_MS));
  const info = await Promise.race([request, late]);
  if (info !== undefined) return info;
  // Сервер ответил позже и просит войти в «Сетевой город» — заново, со входом.
  request.then(late => { if (late?.needs_sgo) location.reload(); });
  let viewing = false;
  try { viewing = localStorage.getItem(VIEWING_KEY) === '1'; } catch { /* приватный режим */ }
  return { viewing };
}

function signedOut() {
  try { return localStorage.getItem('ns-signed-out') === token; } catch { return false; }
}

/** Начать качать модули, не дожидаясь их import: сеть простаивала, пока
 *  шли проверки PIN, аккаунта и настроек, и дневник ждал ещё и их. */
function preload(urls) {
  for (const url of urls || []) {
    if (document.querySelector(`link[rel="modulepreload"][href="${url}"]`)) continue;
    const link = document.createElement('link'); link.rel = 'modulepreload'; link.href = url;
    document.head.append(link);
  }
}

// В прошлый раз дошли до дневника этим входом: тогда его запрос идёт сразу
// после PIN, рядом с проверкой аккаунта, а не после неё. Первый запуск,
// приглашение и вход в «Сетевой город» — по-старому, по очереди.
const READY_KEY = 'ns-boot-ready';
const readyMark = () => token || 'telegram';
function wasReady() {
  try { return localStorage.getItem(READY_KEY) === readyMark(); } catch { return false; }
}

function loadInitialDiary() {
  state.initialDiary = api('/api/diary?week=0&window=current', {
    onFresh: fresh => {
      state.initialDiaryFresh = fresh;
      window.dispatchEvent(new CustomEvent('netschool:diaryfresh', { detail: fresh }));
    },
  })
    .then(payload => ({ payload }), error => ({ error }));
}

const OWN_KEY = 'ns-own-token';

/** iPhone при установке копирует в иконку на экране «Домой» cookie Safari —
 *  токен у них общий, и выход в Safari выкидывал иконку. Иконка один раз
 *  заводит себе свой (app/web/devices.py, own_token). #own — первый запуск
 *  по коду из манифеста: токен там уже свой. */
async function ownToken() {
  if (demo || tg?.initData || !token || navigator.standalone !== true) return;
  try {
    if (location.hash === '#own') {
      localStorage.setItem(OWN_KEY, token);
      history.replaceState(null, '', location.pathname + location.search);
      return;
    }
    if (localStorage.getItem(OWN_KEY) === token || sessionStorage.getItem(OWN_KEY)) return;
    sessionStorage.setItem(OWN_KEY, '1');
    const { data } = await api('/api/devices/own', { method: 'POST', body: '{}' });
    // Общий токен старых ссылок не трогаем — запоминаем, чтобы не спрашивать снова.
    localStorage.setItem(OWN_KEY, data.token || token);
    if (data.token) setToken(data.token);
  } catch { /* хранилище недоступно или нет сети — попробуем при следующем запуске */ }
}

async function boot() {
  // В браузере жест вниз — наше обновление дневника, а не перезагрузка страницы.
  if (!tg?.initData) document.documentElement.classList.add('in-browser');
  // Safari на Mac не крутит колесом мыши блок с overscroll-behavior, пока на
  // странице есть wheel-обработчик с passive: false (трекпад, trackpad.js) —
  // WebKit bug 281300. Трекпад крутит, мышь — нет; на Mac этот запрет не нужен.
  if (navigator.vendor === 'Apple Computer, Inc.' && navigator.maxTouchPoints === 0) {
    document.documentElement.classList.add('mac-webkit');
  }
  tg?.ready();
  tg?.expand();
  initIosHaptics();
  rememberInvite();
  // Ссылка из письма «Сброс пароля» — открывается и без входа, и поверх входа.
  const reset = !demo && !tg?.initData && new URLSearchParams(location.hash.slice(1)).get('reset');
  if (reset) {
    const { showResetLink } = await import('./account.js');
    await showResetLink(reset);
    return;
  }
  // Ни Telegram, ни ссылки с токеном — главный экран со входом. Сюда же —
  // после «Выйти из аккаунта», если адрес иконки на домашнем экране всё ещё
  // несёт прежний общий токен (signOut в profile.js). QR-код с экрана входа
  // другого устройства (account.js, startQrLogin) здесь не подтвердить.
  const qrLogin = demo ? '' : new URLSearchParams(location.search).get('qr') || '';
  if (!demo && !tg?.initData && (!token || signedOut())) {
    const { showAuth, showInvite, showQrWithoutSignIn } = await import('./account.js');
    if (qrLogin) await showQrWithoutSignIn(qrLogin);
    else if (pendingInvite()) await showInvite(pendingInvite(), false);
    else await showAuth();
    return;
  }
  preload(window.NETSCHOOL.appModules);
  await unlockApp();
  await ownToken();
  const tglink = /[?&]tglink=/.test(location.search) || location.hash.startsWith('#tglink=');
  if (!demo && !tglink && !pendingInvite() && wasReady()) loadInitialDiary();
  // Вернулись из Telegram после «Привязать через Telegram» (account.js).
  if (!demo && tglink) {
    const { finishTelegramLink } = await import('./account.js');
    if (await finishTelegramLink()) return;
  }
  // Вернулись с Яндекса после «Привязать Яндекс ID»: аккаунт тот же, дневник уже грузится.
  if (!demo && location.hash.startsWith('#yalink=')) {
    const { finishYandexLink } = await import('./account.js');
    finishYandexLink();
  }
  // Сначала своё: оформление и настройки с сервера бота — раньше аккаунта и
  // дневника, который ждёт «Сетевой город» (app.js берёт этот же запрос).
  if (!demo) state.prefsSync = import('./prefs.js').then((module) => module.syncPrefs()).catch(() => false);
  // Пришли по ссылке-приглашению от ученика — сначала её.
  if (!demo && pendingInvite()) {
    const { showInvite } = await import('./account.js');
    await showInvite(pendingInvite(), true);
    return;
  }
  // Ещё не вошли в «Сетевой город» — сначала вход, потом дневник.
  if (!demo) {
    const info = await accountInfo();
    // Дневник ученика, пригласившего на просмотр: только чтение, без почты.
    document.documentElement.classList.toggle('view-mode', Boolean(info?.viewing));
    // Зарегистрировались по почте — сначала код из письма, потом всё остальное.
    if (info?.needs_verify) {
      const { showVerify } = await import('./account.js');
      await showVerify(info);
      return;
    }
    if (info?.needs_sgo) {
      const { showSgoLogin } = await import('./account.js');
      await showSgoLogin(info);
      return;
    }
  }
  // Ретро-сайт включён (retro.js) — туда, когда ясно, что выбрано: настройка
  // общая с другими устройствами. Сервер бота медлит — решаем по сохранённой.
  if (!demo) await Promise.race([state.prefsSync, new Promise(resolve => setTimeout(resolve, 1500))]);
  {
    const { openRetro, wantsRetro } = await import('./retro.js');
    // QR-код с экрана входа подтверждается в обычном интерфейсе: в ретро
    // его некому показать, а другое устройство ждёт.
    if (wantsRetro() && !qrLogin) {
      openRetro();
      return;
    }
  }
  // Первый запрос идёт параллельно загрузке модулей интерфейса, после разблокировки.
  if (!demo) {
    if (!state.initialDiary) loadInitialDiary();
    try { localStorage.setItem(READY_KEY, readyMark()); } catch { /* приватный режим */ }
  }
  await import('./app.js');
  document.getElementById('bootSplash')?.remove();
  if (qrLogin) {
    history.replaceState(null, '', location.pathname + location.hash);
    import('./devices.js').then(({ openQrApproval }) => openQrApproval(qrLogin));
  }
  // Экраны по требованию — заранее, но после дневника: без сети они тоже откроются.
  setTimeout(() => preload(window.NETSCHOOL.modules), 1000);
  try { sessionStorage.removeItem('bootRetries'); } catch { /* приватный режим */ }
}
boot().catch(() => window.netschoolBootFailed());
