// Быстрый старт: PIN и проверка доступа не ждут загрузки всех экранов приложения.
import { api, demo, state, tg, token } from './core.js';
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

async function boot() {
  tg?.ready();
  tg?.expand();
  initIosHaptics();
  rememberInvite();
  // Ни Telegram, ни ссылки с токеном — главный экран со входом. Сюда же —
  // после «Выйти из аккаунта», если адрес иконки на домашнем экране всё ещё
  // несёт прежний общий токен (signOut в profile.js).
  if (!demo && !tg?.initData && (!token || signedOut())) {
    const { showAuth, showInvite } = await import('./account.js');
    if (pendingInvite()) await showInvite(pendingInvite(), false);
    else await showAuth();
    return;
  }
  await unlockApp();
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
    // Дневник ученика, пригласившего на просмотр: только чтение, без почты и чатов.
    document.documentElement.classList.toggle('view-mode', Boolean(info?.viewing));
    if (info?.needs_sgo) {
      const { showSgoLogin } = await import('./account.js');
      await showSgoLogin(info);
      return;
    }
  }
  // Первый запрос идёт параллельно загрузке модулей интерфейса, после разблокировки.
  if (!demo) state.initialDiary = api('/api/diary?week=0&window=current', {
    onFresh: fresh => {
      state.initialDiaryFresh = fresh;
      window.dispatchEvent(new CustomEvent('netschool:diaryfresh', { detail: fresh }));
    },
  })
    .then(payload => ({ payload }), error => ({ error }));
  for (const url of window.NETSCHOOL.modules || []) {
    if (document.querySelector(`link[rel="modulepreload"][href="${url}"]`)) continue;
    const link = document.createElement('link'); link.rel = 'modulepreload'; link.href = url;
    document.head.append(link);
  }
  await import('./app.js');
  document.getElementById('bootSplash')?.remove();
  try { sessionStorage.removeItem('bootRetries'); } catch { /* приватный режим */ }
}
boot().catch(() => window.netschoolBootFailed());
