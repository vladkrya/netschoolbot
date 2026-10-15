// Блокировка запрашивается до любых данных дневника; секрет сессии только в памяти.
import { api, demo, onLocked, setUnlock, tg, stillMotion } from './core.js';
import { biometricLabel, nativeBiometricEnabled, webBiometricLabel } from './biometrics.js';
import { mountPinpad } from './pinpad.js';

let active = false;
const FORGOT_TELEGRAM = 'Отправьте /resetpin в личный чат с ботом — защита сбросится, и PIN можно будет задать заново.';
// Вне Telegram (вход по почте) PIN сбрасывается паролем от аккаунта.
// Вне Telegram и только при аккаунте с паролем — сброс паролем; иначе бот.
const FORGOT_PASSWORD = (root) => {
  const box = root.querySelector('.lock-error');
  box.textContent = 'Введите пароль от аккаунта (почта и пароль) — PIN сбросится. Или отправьте боту /resetpin.';
  // Форма — рядом с подсказкой, а не внутри: подсказка — абзац, и форму в нём браузер не создаёт.
  root.querySelector('.lock-reset')?.remove();
  const form = document.createElement('form');
  form.className = 'lock-reset';
  form.innerHTML = '<input type="password" autocomplete="current-password" placeholder="Пароль от аккаунта"><button type="submit">Сбросить</button>';
  box.after(form);
  form.querySelector('input').focus();
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await api('/api/lock/password-reset', { method: 'POST', body: JSON.stringify({ password: form.querySelector('input').value }) });
      location.reload();
    } catch (failure) {
      box.textContent = failure.message;
    }
  });
};
// Последний ответ сервера о защите: без сети по нему решаем, можно ли
// открыть сохранённые данные. Неизвестно — ждём сервер.
const REQUIRED_KEY = 'lockRequired';
function rememberedRequired() {
  try { const value = localStorage.getItem(REQUIRED_KEY); return value === null ? null : value === '1'; }
  catch { return null; }
}
function setActive(value) {
  active = value;
  try { localStorage.setItem(REQUIRED_KEY, value ? '1' : '0'); } catch { /* приватный режим */ }
}
let pending = null;
let biometricPrompt = false;

// PIN без сети. Сервер не спросить, поэтому после каждого входа по PIN
// на устройстве хранится его PBKDF2-хеш с солью: без интернета PIN
// сверяется с ним и открывает сохранённые данные. Сами данные и так лежат
// в кэше этого устройства — хеш их не раскрывает сверх того.
const VERIFIER_KEY = 'offlinePin';
const FAILS_KEY = 'offlinePinFails';
const RETRY_KEY = 'offlinePinRetry';
// Данные стёрты после ошибок без сети: до входа с сетью PIN проверять не с чем.
const WIPED_KEY = 'offlineWiped';
const OFFLINE_ATTEMPTS = 10;
// Как на сервере: первые ошибки без паузы, дальше пауза растёт.
const OFFLINE_FREE = 5;
const offlinePause = fails => fails < OFFLINE_FREE ? 0 : Math.min(300, 30 * 2 ** (fails - OFFLINE_FREE));
const seconds = n => n < 60 ? `${n} с` : `${Math.ceil(n / 60)} мин`;
const WIPED_MESSAGE = 'Сохранённые данные удалены после 10 неверных PIN. Чтобы войти, подключитесь к интернету.';
function offlineWiped() {
  try { return localStorage.getItem(WIPED_KEY) === '1'; } catch { return false; }
}
// Введённый без сети PIN в памяти: когда сеть вернётся, им же тихо
// входим на сервере, чтобы не спрашивать второй раз.
let offlinePin = '';

async function pinHash(pin, salt) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(pin), 'PBKDF2', false, ['deriveBits']);
  return encode(await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations: 200000 }, key, 256));
}
function savedVerifier() {
  try { return window.crypto?.subtle ? JSON.parse(localStorage.getItem(VERIFIER_KEY) || 'null') : null; }
  catch { return null; }
}
async function rememberPin(pin) {
  try {
    const salt = crypto.getRandomValues(new Uint8Array(16));
    localStorage.setItem(VERIFIER_KEY, JSON.stringify({ salt: encode(salt), hash: await pinHash(pin, salt) }));
    localStorage.removeItem(FAILS_KEY); localStorage.removeItem(RETRY_KEY); localStorage.removeItem(WIPED_KEY);
  } catch { /* без хеша вход без сети просто недоступен */ }
}
function forgetPin() {
  try {
    localStorage.removeItem(VERIFIER_KEY); localStorage.removeItem(FAILS_KEY); localStorage.removeItem(RETRY_KEY);
    localStorage.removeItem(PASSKEYS_KEY);
  } catch { /* приватный режим */ }
}

// Ключ доступа (Face ID / Touch ID) без сети. Сервер не выдаст вызов и не
// проверит подпись, поэтому без сети ключ подтверждает только то, что
// телефон узнал владельца, — как и PIN, сверенный с хешем на устройстве.
// Открываются лишь данные, уже сохранённые здесь; с сетью вход снова
// проверит сервер. Идентификаторы ключей запоминаются после входа по ним.
const PASSKEYS_KEY = 'offlinePasskeys';
let offlineBiometric = false;
function savedPasskeys() {
  try { const ids = JSON.parse(localStorage.getItem(PASSKEYS_KEY) || '[]'); return Array.isArray(ids) ? ids : []; }
  catch { return []; }
}
function rememberPasskey(id) {
  try { localStorage.setItem(PASSKEYS_KEY, JSON.stringify([id, ...savedPasskeys().filter(item => item !== id)].slice(0, 5))); }
  catch { /* без этого ключ без сети просто недоступен */ }
}
function savePasskeys(ids) {
  try {
    if (ids.length) localStorage.setItem(PASSKEYS_KEY, JSON.stringify(ids.slice(0, 5)));
    else localStorage.removeItem(PASSKEYS_KEY);
  } catch { /* без этого ключ без сети просто недоступен */ }
}
function forgetPasskeys() {
  try { localStorage.removeItem(PASSKEYS_KEY); } catch { /* приватный режим */ }
}
async function offlinePasskey() {
  if (offlineWiped()) throw new Error(WIPED_MESSAGE);
  let credential;
  biometricPrompt = true;
  try {
    credential = await navigator.credentials.get({ publicKey: {
      challenge: crypto.getRandomValues(new Uint8Array(32)),
      allowCredentials: savedPasskeys().map(id => ({ type: 'public-key', id: decode(id) })),
      userVerification: 'required',
      timeout: 60000,
    } });
  } catch (failure) {
    throw new Error(failure.name === 'NotAllowedError' ? 'Биометрия отменена. Можно войти по PIN.' : 'Не удалось подтвердить биометрию');
  } finally { biometricPrompt = false; }
  // Флаг UV (бит 2 байта флагов): телефон действительно узнал владельца,
  // а не просто нашёл ключ.
  const flags = new Uint8Array(credential.response.authenticatorData)[32];
  if (!(flags & 0x04)) throw new Error('Телефон не подтвердил владельца. Войдите по PIN.');
  offlineBiometric = true;
}
/** Сверить PIN с хешем на устройстве. Много ошибок — стираем и хеш, и данные. */
async function checkOffline(pin) {
  if (offlineWiped()) throw new Error(WIPED_MESSAGE);
  const saved = savedVerifier();
  if (!saved) throw new Error(savedPasskeys().length ? 'Без интернета PIN не проверить — войдите по биометрии.' : 'Без интернета PIN не проверить. Подключитесь к сети.');
  let fails = 0, retry = 0;
  try { fails = Number(localStorage.getItem(FAILS_KEY) || 0); retry = Number(localStorage.getItem(RETRY_KEY) || 0); }
  catch { /* приватный режим */ }
  const left = OFFLINE_ATTEMPTS - fails;
  const wait = Math.ceil((retry - Date.now()) / 1000);
  if (wait > 0) throw new Error(`Слишком много ошибок. Попробуйте через ${seconds(wait)}. До удаления сохранённых данных: ${left} ${plural(left)}.`);
  if (await pinHash(pin, decode(saved.salt)) === saved.hash) {
    try { localStorage.removeItem(FAILS_KEY); localStorage.removeItem(RETRY_KEY); } catch { /* приватный режим */ }
    return;
  }
  fails += 1;
  const pause = offlinePause(fails);
  try {
    localStorage.setItem(FAILS_KEY, String(fails));
    localStorage.setItem(RETRY_KEY, String(pause ? Date.now() + pause * 1000 : 0));
  } catch { /* приватный режим */ }
  if (fails >= OFFLINE_ATTEMPTS) {
    forgetPin(); await clearOffline();
    try { localStorage.setItem(WIPED_KEY, '1'); } catch { /* приватный режим */ }
    throw Object.assign(new Error('Неверный PIN, это была 10-я ошибка: сохранённые данные удалены. Чтобы войти, подключитесь к интернету.'), { wiped: true });
  }
  const rest = OFFLINE_ATTEMPTS - fails;
  throw new Error(`Неверный PIN. До удаления сохранённых данных: ${rest} ${plural(rest)}.` +
    (pause ? ` Следующая попытка через ${seconds(pause)}.` : ''));
}
const plural = n => n % 10 === 1 && n % 100 !== 11 ? 'попытка'
  : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'попытки' : 'попыток';

let resyncing = null;
/** Сеть вернулась после входа без сети — получаем вход на сервере тем же PIN. */
function resync() {
  if (!offlinePin) return Promise.resolve(false);
  if (!resyncing) resyncing = (async () => {
    const pin = offlinePin;
    try {
      const result = await post('unlock', { pin });
      setUnlock(result.data.unlock); offlinePin = ''; markUnlocked();
      window.dispatchEvent(new CustomEvent('netschool:unlocked'));
      return true;
    } catch (failure) {
      // PIN сменили на другом устройстве или сервер его не принял — спросим заново.
      if (failure.reason !== 'server') { forgetPin(); relock(); }
      return false;
    }
  })().finally(() => { resyncing = null; });
  return resyncing;
}
window.addEventListener('online', () => { resync(); });
// Сервер ответил «закрыто»: вошли без сети. По PIN — тихо входим им же;
// по ключу доступа тихо нельзя — спросим снова, уже с проверкой сервером.
onLocked(() => {
  if (offlinePin) return resync();
  if (offlineBiometric) { offlineBiometric = false; relock(); }
  return Promise.resolve(false);
});

const serverDown = () => Object.assign(new Error('Нет связи с сервером'), { reason: 'server' });
const within = (promise, ms) => ms
  ? Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(serverDown()), ms))]) : promise;
const post = (path, body = {}) => api(`/api/lock/${path}`, { method: 'POST', body: JSON.stringify(body) });

function panel(title) {
  const root = document.createElement('div');
  root.className = 'app-lock';
  root.setAttribute('role', 'dialog');
  root.setAttribute('aria-modal', 'true');
  root.setAttribute('aria-label', title);
  root.innerHTML = `<form class="app-lock-card"><h2>${title}</h2><div class="lock-fields"></div>
    <p class="lock-error" role="alert"></p><div class="lock-actions"></div></form>`;
  document.body.append(root);
  document.documentElement.classList.add('app-locked');
  root.close = () => closeAnimated(root);
  return root;
}

const reducedMotion = stillMotion;

/** Экран защиты растворяется, открывая приложение под собой. */
function closeAnimated(root, frames = [{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(1.08)' }]) {
  document.documentElement.classList.remove('app-locked');
  if (reducedMotion() || !root.animate) { root.remove(); return; }
  root.style.pointerEvents = 'none';
  root.animate(frames, { duration: 380, easing: 'cubic-bezier(.3,.7,.2,1)', fill: 'forwards' })
    .finished.catch(() => {}).then(() => root.remove());
}

function error(root, failure) {
  root.querySelector('.lock-error').textContent = failure.name === 'NotAllowedError'
    ? 'Биометрия отменена. Можно войти по PIN.' : failure.message || 'Не удалось подтвердить биометрию';
}

function decode(value) {
  return Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
}
function encode(value) {
  return btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function passkey(register = false) {
  const kind = register ? 'register' : 'authenticate';
  const { data } = await post(`${kind}/options`);
  const options = data.options;
  options.challenge = decode(options.challenge);
  if (options.user) options.user.id = decode(options.user.id);
  for (const list of ['allowCredentials', 'excludeCredentials']) {
    for (const item of options[list] || []) item.id = decode(item.id);
  }
  let credential;
  biometricPrompt = true;
  try { credential = await navigator.credentials[register ? 'create' : 'get']({ publicKey: options }); }
  finally { biometricPrompt = false; }
  const response = {};
  for (const key of ['clientDataJSON', 'attestationObject', 'authenticatorData', 'signature', 'userHandle']) {
    if (credential.response[key]) response[key] = encode(credential.response[key]);
  }
  const result = await post(`${kind}/verify`, { id: data.id, credential: {
    id: credential.id, rawId: encode(credential.rawId), type: credential.type, response,
    clientExtensionResults: credential.getClientExtensionResults(),
  } });
  if (!register) setUnlock(result.data.unlock);
  rememberPasskey(encode(credential.rawId));
}

let nativeManagerPromise;
function nativeManager() {
  if (!tg?.initData || !tg.isVersionAtLeast?.('7.2') || !tg.BiometricManager) return Promise.resolve(null);
  if (!nativeManagerPromise) nativeManagerPromise = new Promise(resolve => {
    const manager = tg.BiometricManager;
    if (manager.isInited) { resolve(manager.isBiometricAvailable ? manager : null); return; }
    const timer = setTimeout(() => resolve(null), 5000);
    manager.init(() => { clearTimeout(timer); resolve(manager.isBiometricAvailable ? manager : null); });
  });
  return nativeManagerPromise;
}

async function supportsBiometry() {
  if (tg?.initData) return false;
  try {
    return window.isSecureContext && !!window.PublicKeyCredential &&
      await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch { return false; }
}

async function biometry(register = false) {
  const manager = await nativeManager();
  if (!manager) {
    if (tg?.initData) throw new Error('Биометрия недоступна в Telegram на этом устройстве. Используйте PIN.');
    return passkey(register);
  }
  const label = biometricLabel(manager.biometricType, tg.platform);
  // Доступ уже запрещали: Telegram больше не спросит сам, сразу ответит «нет».
  // Разрешить можно только в его настройках бота — открываем их.
  if (manager.isAccessRequested && !manager.isAccessGranted) {
    try { manager.openSettings(); } catch { /* старый Telegram — подскажем словами */ }
    throw new Error(`Разрешите ${label} для бота в открывшихся настройках Telegram и попробуйте снова. Вход по PIN работает.`);
  }
  biometricPrompt = true;
  try {
    if (register) {
      if (!manager.isAccessGranted) {
        const granted = await new Promise(resolve => manager.requestAccess({ reason: 'Разблокировка школьного дневника' }, resolve));
        if (!granted) throw new Error(`Доступ к ${label} не разрешён. Вход по PIN работает.`);
      }
      const { data } = await post('biometric/register', { device: manager.deviceId });
      const saved = await new Promise(resolve => manager.updateBiometricToken(data.token, resolve));
      if (!saved) throw new Error('Не удалось сохранить ключ биометрии. Вход по PIN работает.');
    } else {
      const token = await new Promise((resolve, reject) => {
        manager.authenticate({ reason: 'Открыть школьный дневник' }, (ok, secret) => {
          if (ok && secret) resolve(secret);
          else reject(new Error(`${label} не подтверждён. Можно войти по PIN.`));
        });
      });
      const result = await post('biometric/unlock', { device: manager.deviceId, token });
      setUnlock(result.data.unlock);
    }
  } finally { biometricPrompt = false; }
}

function statusPath(native) {
  const params = new URLSearchParams({ origin: location.origin });
  if (native) params.set('device', native.deviceId);
  return `/api/lock/status?${params}`;
}

function choosePin({ required = false, changing = false } = {}) {
  const root = panel(changing ? 'Новый PIN' : 'Создайте PIN');
  root.querySelector('.lock-actions').innerHTML = required ? '' : '<button type="button" data-close>Отмена</button>';
  let first = '';
  return new Promise(resolve => {
    const close = result => { pad.destroy(); first = ''; root.close(); resolve(result); };
    const pad = mountPinpad(root, async (pin, controls) => {
      if (!first) {
        first = pin;
        root.querySelector('h2').textContent = 'Повторите PIN';
        controls.reset('Ещё раз те же 4 цифры');
        return;
      }
      if (pin !== first) {
        controls.failure();
        root.querySelector('.lock-error').textContent = 'Коды не совпали. Попробуйте ещё раз.';
        controls.reset('Повторите выбранный PIN');
        return;
      }
      const result = await post('pin', { pin });
      setUnlock(result.data.unlock); setActive(true); markUnlocked(); rememberPin(pin);
      await controls.success();
      close(true);
    }, { hint: 'Придумайте 4 цифры для входа' });
    root.querySelector('[data-close]')?.addEventListener('click', () => close(false));
  });
}

export async function unlockApp() {
  if (demo) { document.documentElement.classList.remove('app-locked'); return; }
  if (pending) return pending;
  pending = (async () => {
    const root = panel('Вход в дневник');
    root.querySelector('.lock-error').textContent = 'Проверяем защиту…';
    // Проверка PIN не должна ждать ответ Telegram о биометрии (до 5 с).
    const nativeReady = nativeManager();
    const known = rememberedRequired();
    // PIN можно проверить и без сети — тогда сервер долго не ждём: iPhone
    // без интернета держал запрос до 10 с на «Проверяем защиту…».
    // Чем открыть без сети: хешем PIN или ключом доступа этого устройства.
    const offlineReady = () => !!savedVerifier() || savedPasskeys().length > 0;
    const canOffline = known === true && offlineReady();
    let status;
    while (!status) {
      // Без сети сервер не спросить. Если защиты не было, сразу открываем
      // сохранённые данные, а не крутим «Проверяем защиту…».
      const offline = navigator.onLine === false;
      if (offline && known === false) { root.close(); return; }
      try {
        if (offline) throw serverDown();
        // Без защиты долго ждать тоже незачем: сохранённое откроется и так.
        status = (await within(api(statusPath(null)), canOffline || known === false ? 4000 : 0)).data;
      } catch (failure) {
        if (failure.reason === 'server' && known === false) { root.close(); return; }
        if (failure.reason === 'server' && canOffline && offlineReady()) {
          status = { required: true, pin: true, offline: true };
          break;
        }
        root.querySelector('.lock-error').textContent = failure.reason !== 'server' ? failure.message
          : offlineWiped() ? WIPED_MESSAGE : 'Нет связи с сервером. Вход по PIN продолжится, когда появится интернет.';
        root.querySelector('.lock-actions').innerHTML = '<button type="button">Повторить</button>';
        await new Promise(resolve => {
          root.querySelector('button').onclick = resolve;
          window.addEventListener('online', resolve, { once: true });
        });
        root.querySelector('.lock-error').textContent = 'Проверяем защиту…';
        root.querySelector('.lock-actions').innerHTML = '';
      }
    }
    setActive(status.required);
    if (!active) { forgetPin(); root.close(); return; }
    // Список ключей с сервера: вход без сети по ключу доступа не зависит от
    // того, входили ли по нему здесь после очистки офлайн-данных.
    if (Array.isArray(status.passkey_ids)) savePasskeys(status.passkey_ids);
    root.querySelector('.lock-error').textContent = '';
    root.querySelector('.lock-actions').innerHTML = '';
    const biometricReady = (async () => {
      if (status.offline) {
        // Без сети — только ключ доступа, которым уже входили на этом устройстве.
        if (tg?.initData || !savedPasskeys().length || !await supportsBiometry()) return null;
        return { label: webBiometricLabel(), offline: true };
      }
      const native = await nativeReady;
      let registered = false;
      if (native) {
        try { registered = (await api(statusPath(native))).data.biometric; } catch { /* PIN работает. */ }
      }
      const available = nativeBiometricEnabled(native, registered) ||
        (!tg?.initData && status.passkeys && await supportsBiometry());
      // Telegram сам показывает Face ID / Touch ID — запускаем сразу, как
      // в банковских приложениях; PIN остаётся под ним, если отменить.
      return available ? { label: native ? biometricLabel(native.biometricType, tg.platform) : webBiometricLabel(),
        auto: Boolean(native) } : null;
    })();
    await new Promise(resolve => {
      let pad;
      const finish = async () => {
        pad?.destroy(); markUnlocked();
        // Уже заданные пароли не сбрасываем: сначала подтверждение, затем выбор PIN.
        if (!status.pin) {
          root.remove();
          await choosePin({ required: true });
        } else root.close();
        resolve();
      };
      if (status.pin) {
        pad = mountPinpad(root, async (pin, controls) => {
          let result = null;
          if (!status.offline) {
            try { result = await post('unlock', { pin }); }
            catch (failure) { if (failure.reason !== 'server' || !savedVerifier()) throw failure; }
          }
          if (result) { setUnlock(result.data.unlock); rememberPin(pin); }
          else {
            try { await checkOffline(pin); }
            catch (failure) {
              // Данные стёрты — ключом доступа без сети открывать больше нечего.
              if (failure.wiped || offlineWiped()) root.querySelector('[data-biometric]')?.setAttribute('hidden', '');
              throw failure;
            }
            offlinePin = pin;
          }
          await controls.success(); await finish();
          if (offlinePin && navigator.onLine !== false) resync();
        }, { forgot: status.password_reset && !tg?.initData ? FORGOT_PASSWORD : FORGOT_TELEGRAM });
        biometricReady.then(config => {
          if (!config || !root.isConnected) return;
          const run = pad.setBiometric({ ...config, run: async () => {
            await (config.offline ? offlinePasskey() : biometry());
            await pad.success(); await finish();
          } });
          if (config.auto && !document.hidden) run?.();
        }).catch(() => {});
      } else {
        root.querySelector('.lock-fields').innerHTML = '<p>Введите прежний пароль, затем замените его на PIN из 4 цифр.</p><label>Прежний пароль<input type="password" autocomplete="current-password" required maxlength="128" autofocus></label>';
        root.querySelector('.lock-actions').innerHTML = '<button type="submit">Продолжить</button>';
        root.querySelector('form').onsubmit = async event => {
          event.preventDefault();
          const button = root.querySelector('[type="submit"]'); button.disabled = true;
          try {
            const result = await post('unlock', { password: root.querySelector('input').value });
            setUnlock(result.data.unlock); await finish();
          } catch (failure) { error(root, failure); }
          finally { button.disabled = false; }
        };
        biometricReady.then(config => {
          if (!config || !root.isConnected) return;
          const button = document.createElement('button'); button.type = 'button'; button.className = 'pin-biometric';
          button.textContent = `Войти с ${config.label}`;
          button.onclick = async () => {
            try { await biometry(); await finish(); } catch (failure) { error(root, failure); }
          };
          root.querySelector('.lock-actions').append(button);
        }).catch(() => {});
      }
    });
  })();
  try { await pending; } finally { pending = null; }
}

async function clearOffline() {
  try { localStorage.removeItem('studentName'); } catch { /* приватный режим */ }
  if (!('caches' in window)) return;
  for (const name of await caches.keys()) {
    if (!name.startsWith('ns-offline')) continue;
    const cache = await caches.open(name);
    // Удаляем данные и персональные страницы, сохраняем версионные JS/CSS.
    // Полное удаление кэша заставляло iPhone качать всё приложение при каждом входе.
    const entries = await cache.keys();
    await Promise.all(entries.filter(request => !/\/static\/v[^/]+\//.test(new URL(request.url).pathname))
      .map(request => cache.delete(request)));
  }
}

const CHEVRON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>';
const SHIELD = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" aria-hidden="true"><path d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6Z"/><path d="m9 12 2 2 4-4" stroke-linecap="round"/></svg>';

const BACK_ARROW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 5-7 7 7 7"/></svg>';

/** Свайп вправо закрывает страницу: она едет за пальцем, как в iOS. */
function swipeToClose(root) {
  let start = null;
  root.addEventListener('touchstart', event => {
    // Общий жест «назад» приложения сработал бы под страницей.
    event.stopPropagation();
    if (event.touches.length !== 1) return;
    const touch = event.touches[0];
    start = { x: touch.clientX, y: touch.clientY, dx: 0, at: Date.now(), dir: null };
  }, { passive: true });
  root.addEventListener('touchmove', event => {
    if (!start) return;
    const touch = event.touches[0];
    const dx = touch.clientX - start.x, dy = touch.clientY - start.y;
    if (!start.dir && Math.hypot(dx, dy) > 10) start.dir = Math.abs(dx) > Math.abs(dy) * 1.2 && dx > 0 ? 'x' : 'y';
    if (start.dir !== 'x') return;
    start.dx = Math.max(0, dx);
    root.style.transform = `translateX(${start.dx}px)`;
  }, { passive: true });
  const end = () => {
    if (!start) return;
    const { dx, dir, at } = start; start = null;
    if (dir !== 'x') return;
    const fast = dx / Math.max(1, Date.now() - at) > 0.5;
    if (dx > root.clientWidth * 0.33 || (fast && dx > 40)) { root.style.transform = ''; root.close(dx); return; }
    root.animate([{ transform: `translateX(${dx}px)` }, { transform: 'translateX(0)' }], { duration: 220, easing: 'ease-out' });
    root.style.transform = '';
  };
  root.addEventListener('touchend', end);
  root.addEventListener('touchcancel', end);
}

/** Строка-переключатель настроек: подпись, тумблер и пояснение под ним. */
function switchRow(group, title, note, checked) {
  const row = document.createElement('label'); row.className = 'ls-row';
  row.innerHTML = '<span class="ls-row-text"><span class="ls-row-title"></span><span class="ls-row-note"></span></span>' +
    '<input type="checkbox" class="switch" switch>';
  row.querySelector('.ls-row-title').textContent = title;
  row.querySelector('.ls-row-note').textContent = note;
  const toggle = row.querySelector('input'); toggle.checked = checked;
  group.append(row);
  return toggle;
}

export async function openLockSettings() {
  const native = await nativeManager();
  const { data } = await api(statusPath(native));
  setActive(data.required);
  const on = data.enabled && data.pin;
  const root = document.createElement('div');
  root.className = 'app-lock lock-settings';
  root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-label', 'Защита входа');
  root.innerHTML = `<header class="ls-bar"><button type="button" class="ls-back" data-close aria-label="Назад">${BACK_ARROW}</button><h2>Защита входа</h2></header>
    <div class="ls-body">
      <section class="ls-hero ${on ? 'on' : ''}"><div class="ls-hero-icon">${SHIELD}</div>
        <h3>${on ? 'PIN включён' : 'PIN не задан'}</h3>
        <p>${on ? 'Дневник открывается только после ввода 4 цифр. PIN общий для всех ваших устройств.'
          : 'Задайте 4 цифры — без них никто не откроет дневник на вашем телефоне.'}</p></section>
      <div class="ls-group"><button type="button" class="ls-row ls-link" data-pin>
        <span class="ls-row-title">${on ? 'Изменить PIN' : 'Задать PIN'}</span>${CHEVRON}</button></div>
      <p class="ls-status" role="status"></p>
    </div>`;
  document.body.append(root);
  document.documentElement.classList.add('app-locked');
  root.close = (from = 0) => closeAnimated(root, [{ transform: `translateX(${from}px)` }, { transform: 'translateX(100%)' }]);
  if (!reducedMotion()) root.animate([{ transform: 'translateX(100%)' }, { transform: 'translateX(0)' }], { duration: 340, easing: 'cubic-bezier(.2,.8,.2,1)' });
  swipeToClose(root);
  const body = root.querySelector('.ls-body');
  const status = root.querySelector('.ls-status');
  const say = (text, bad = false) => { status.textContent = text; status.classList.toggle('bad', bad); };
  const fail = failure => say(failure.name === 'NotAllowedError' ? 'Биометрия отменена.' : failure.message || 'Не получилось', true);
  root.querySelector('[data-close]').onclick = root.close;
  root.querySelector('[data-pin]').onclick = async () => {
    root.remove();
    await choosePin({ changing: data.pin });
    await openLockSettings();
  };
  root.tabIndex = -1; root.focus();
  if (!on) return;

  const heading = text => { const h = document.createElement('h4'); h.className = 'ls-caption'; h.textContent = text; return h; };
  const group = document.createElement('div'); group.className = 'ls-group';
  body.insertBefore(heading('Как входить'), status); body.insertBefore(group, status);

  const supported = native || (!tg?.initData && await supportsBiometry());
  if (supported) {
    const label = native ? biometricLabel(native.biometricType, tg.platform) : webBiometricLabel();
    const toggle = switchRow(group, label, native ? 'Вместо PIN на этом устройстве'
      : 'Вместо PIN на этом устройстве. Выключение отзывает ключи для этого адреса.',
    native ? nativeBiometricEnabled(native, data.biometric) : data.passkeys > 0);
    toggle.onchange = async () => {
      const wanted = toggle.checked; toggle.disabled = true;
      try {
        if (wanted) await biometry(true);
        else if (native) {
          await post('biometric/disable', { device: native.deviceId });
          biometricPrompt = true;
          try { await new Promise(resolve => native.updateBiometricToken('', resolve)); }
          catch { /* Сервер уже отозвал секрет. */ }
          finally { biometricPrompt = false; }
        } else { await post('passkey/disable'); forgetPasskeys(); }
        toggle.checked = wanted;
        say(wanted ? `${label} включён` : `${label} выключен`);
      } catch (failure) { toggle.checked = !wanted; fail(failure); }
      finally { toggle.disabled = false; }
    };
  }
  const telegramToggle = switchRow(group, 'PIN в Telegram',
    'Если выключить, в миниаппе Telegram дневник откроется без PIN. В веб-приложении PIN остаётся.', data.require_telegram);
  telegramToggle.onchange = async () => {
    const wanted = telegramToggle.checked; telegramToggle.disabled = true;
    try {
      const result = await post('settings', { require_telegram: wanted });
      setUnlock(result.data.unlock); setActive(tg?.initData ? wanted : data.enabled);
      data.require_telegram = wanted;
    } catch (failure) { telegramToggle.checked = !wanted; fail(failure); }
    finally { telegramToggle.disabled = false; }
  };

  const returnToggle = switchRow(group, 'PIN при возврате',
    'Спрашивать и после сворачивания. Если выключить — только при запуске приложения. Для этого устройства.',
    askOnReturn());
  returnToggle.onchange = () => {
    try { localStorage.setItem(RETURN_KEY, returnToggle.checked ? '1' : '0'); } catch { /* приватный режим */ }
    say(returnToggle.checked ? 'PIN будет спрашиваться при каждом возврате' : 'При возврате PIN не спросим');
  };

  const danger = document.createElement('div'); danger.className = 'ls-group';
  danger.innerHTML = '<button type="button" class="ls-row ls-link ls-danger"><span class="ls-row-title">Выключить защиту</span></button>';
  body.insertBefore(danger, status);
  const note = document.createElement('p'); note.className = 'ls-footnote';
  note.textContent = `Забыли PIN? ${FORGOT_TELEGRAM}`;
  body.append(note);
  const disable = danger.querySelector('button');
  let armed = 0;
  // Выключение — второе нажатие в течение 4 с, чтобы не снять защиту случайно.
  disable.onclick = async () => {
    if (!armed) {
      disable.querySelector('.ls-row-title').textContent = 'Нажмите ещё раз, чтобы выключить';
      armed = setTimeout(() => { armed = 0; disable.querySelector('.ls-row-title').textContent = 'Выключить защиту'; }, 4000);
      return;
    }
    clearTimeout(armed); armed = 0; disable.disabled = true;
    try { await post('disable'); setActive(false); forgetPin(); setUnlock(''); root.close(); }
    catch (failure) { fail(failure); disable.disabled = false; }
  };
}

// Свернули и вернулись. По умолчанию PIN не спрашиваем, пока приложение не
// закрыто; можно включить проверку при каждом возврате. Вход на сервере
// живёт 12 часов — после этого PIN нужен всё равно.
const RETURN_KEY = 'lockOnReturn';
const UNLOCK_TTL_MS = 11 * 3600 * 1000;
let unlockedAt = 0;
function markUnlocked() { unlockedAt = Date.now(); }
function askOnReturn() {
  try { return localStorage.getItem(RETURN_KEY) === '1'; } catch { return false; }
}
function relock() {
  document.documentElement.classList.add('app-locked');
  post('logout').catch(() => {});
  setUnlock(''); offlinePin = ''; offlineBiometric = false;
  unlockApp();
}
const expired = () => Date.now() - unlockedAt > UNLOCK_TTL_MS;

document.addEventListener('visibilitychange', () => {
  if (!active || pending || biometricPrompt) return;
  if (document.hidden) {
    // Скрываем данные сразу, чтобы их не было на снимке в переключателе приложений.
    if (askOnReturn()) document.documentElement.classList.add('app-locked');
  } else if (askOnReturn() || expired()) relock();
});
window.addEventListener('pageshow', event => { if (event.persisted && active && (askOnReturn() || expired())) relock(); });
