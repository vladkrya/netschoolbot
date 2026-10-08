// Блокировка запрашивается до любых данных дневника; секрет сессии только в памяти.
import { api, demo, setUnlock, tg } from './core.js';
import { biometricLabel, nativeBiometricEnabled } from './biometrics.js';
import { mountPinpad } from './pinpad.js';

let active = false;
const FORGOT = 'Отправьте /resetapplock в личный чат с ботом — защита сбросится, и PIN можно будет задать заново.';
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
  root.close = () => { root.remove(); document.documentElement.classList.remove('app-locked'); };
  return root;
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
  biometricPrompt = true;
  try {
    if (register) {
      if (!manager.isAccessGranted) {
        const granted = await new Promise(resolve => manager.requestAccess({ reason: 'Разблокировка школьного дневника' }, resolve));
        if (!granted) throw new Error('Разрешите биометрию в настройках Telegram или используйте PIN.');
      }
      const { data } = await post('biometric/register', { device: manager.deviceId });
      const saved = await new Promise(resolve => manager.updateBiometricToken(data.token, resolve));
      if (!saved) throw new Error('Не удалось сохранить ключ биометрии. Вход по PIN работает.');
    } else {
      const token = await new Promise((resolve, reject) => {
        manager.authenticate({ reason: 'Открыть школьный дневник' }, (ok, secret) => {
          if (ok && secret) resolve(secret);
          else reject(new Error('Биометрия отменена. Можно войти по PIN.'));
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
      setUnlock(result.data.unlock); setActive(true);
      await clearOffline(); close(true);
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
    let status;
    while (!status) {
      // Без сети сервер не спросить. Если защиты не было, сразу открываем
      // сохранённые данные, а не крутим «Проверяем защиту…».
      const offline = navigator.onLine === false;
      if (offline && known === false) { root.close(); return; }
      try {
        if (offline) throw Object.assign(new Error('Нет интернета'), { reason: 'server' });
        status = (await api(statusPath(null))).data;
      } catch (failure) {
        if (failure.reason === 'server' && known === false) { root.close(); return; }
        root.querySelector('.lock-error').textContent = failure.reason === 'server'
          ? 'Нет связи с сервером. Вход по PIN продолжится, когда появится интернет.' : failure.message;
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
    if (!active) { root.close(); return; }
    await clearOffline();
    root.querySelector('.lock-error').textContent = '';
    root.querySelector('.lock-actions').innerHTML = '';
    const biometricReady = (async () => {
      const native = await nativeReady;
      let registered = false;
      if (native) {
        try { registered = (await api(statusPath(native))).data.biometric; } catch { /* PIN работает. */ }
      }
      const available = nativeBiometricEnabled(native, registered) ||
        (!tg?.initData && status.passkeys && await supportsBiometry());
      return available ? { label: native ? biometricLabel(native.biometricType, tg.platform) : 'Touch ID / Face ID' } : null;
    })();
    await new Promise(resolve => {
      let pad;
      const finish = async () => {
        pad?.destroy();
        // Уже заданные пароли не сбрасываем: сначала подтверждение, затем выбор PIN.
        if (!status.pin) {
          root.remove();
          await choosePin({ required: true });
        } else root.close();
        resolve();
      };
      if (status.pin) {
        pad = mountPinpad(root, async pin => {
          const result = await post('unlock', { pin });
          setUnlock(result.data.unlock); await finish();
        }, { forgot: FORGOT });
        biometricReady.then(config => {
          if (config && root.isConnected) pad.setBiometric({ ...config, run: async () => { await biometry(); await finish(); } });
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
  root.innerHTML = `<header class="ls-bar"><h2>Защита входа</h2><button type="button" class="ls-done" data-close>Готово</button></header>
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
  root.close = () => { root.remove(); document.documentElement.classList.remove('app-locked'); };
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
    const label = native ? biometricLabel(native.biometricType, tg.platform) : 'Touch ID / Face ID';
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
        } else await post('passkey/disable');
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

  const danger = document.createElement('div'); danger.className = 'ls-group';
  danger.innerHTML = '<button type="button" class="ls-row ls-link ls-danger"><span class="ls-row-title">Выключить защиту</span></button>';
  body.insertBefore(danger, status);
  const note = document.createElement('p'); note.className = 'ls-footnote';
  note.textContent = `Забыли PIN? ${FORGOT}`;
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
    try { await post('disable'); setActive(false); setUnlock(''); root.close(); }
    catch (failure) { fail(failure); disable.disabled = false; }
  };
}

// Скрываем данные немедленно; подтверждение требуется при каждом возвращении.
document.addEventListener('visibilitychange', () => {
  if (!active || pending || biometricPrompt) return;
  if (document.hidden) {
    document.documentElement.classList.add('app-locked');
    post('logout').catch(() => {});
    setUnlock('');
  } else unlockApp();
});
window.addEventListener('pageshow', event => { if (event.persisted && active) { setUnlock(''); unlockApp(); } });
