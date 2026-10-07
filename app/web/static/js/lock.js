// Блокировка запрашивается до любых данных дневника; секрет сессии только в памяти.
import { api, demo, setUnlock, tg } from './core.js';
import { biometricLabel, nativeBiometricEnabled } from './biometrics.js';

let active = false;
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
    ? 'Биометрия отменена. Можно войти по паролю.' : failure.message || 'Не удалось подтвердить биометрию';
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
    if (tg?.initData) throw new Error('Биометрия недоступна в Telegram на этом устройстве. Используйте пароль.');
    return passkey(register);
  }
  biometricPrompt = true;
  try {
    if (register) {
      if (!manager.isAccessGranted) {
        const granted = await new Promise(resolve => manager.requestAccess({ reason: 'Разблокировка школьного дневника' }, resolve));
        if (!granted) throw new Error('Разрешите биометрию в настройках Telegram или используйте пароль.');
      }
      const { data } = await post('biometric/register', { device: manager.deviceId });
      const saved = await new Promise(resolve => manager.updateBiometricToken(data.token, resolve));
      if (!saved) throw new Error('Не удалось сохранить ключ биометрии. Вход по паролю работает.');
    } else {
      const token = await new Promise((resolve, reject) => {
        manager.authenticate({ reason: 'Открыть школьный дневник' }, (ok, secret) => {
          if (ok && secret) resolve(secret);
          else reject(new Error('Биометрия отменена. Можно войти по паролю.'));
        });
      });
      const result = await post('biometric/unlock', { device: manager.deviceId, token });
      setUnlock(result.data.unlock);
    }
  } finally { biometricPrompt = false; }
}

export async function unlockApp() {
  if (demo) { document.documentElement.classList.remove('app-locked'); return; }
  if (pending) return pending;
  pending = (async () => {
    const root = panel('Вход в дневник');
    root.querySelector('.lock-error').textContent = 'Проверяем блокировку…';
    const native = await nativeManager();
    const statusPath = native ? `/api/lock/status?device=${encodeURIComponent(native.deviceId)}` : '/api/lock/status';
    let status;
    // При отсутствии сети не открываем ранее сохранённые данные в обход блокировки.
    while (!status) {
      try { status = (await api(statusPath)).data; }
      catch (failure) {
        error(root, failure);
        root.querySelector('.lock-actions').innerHTML = '<button type="button">Повторить</button>';
        await new Promise(resolve => { root.querySelector('button').onclick = resolve; });
      }
    }
    active = status.enabled;
    if (!active) { root.close(); return; }
    await clearOffline();
    root.querySelector('.lock-error').textContent = '';
    root.querySelector('.lock-fields').innerHTML = '<label>Пароль<input type="password" autocomplete="current-password" required maxlength="128" autofocus></label>';
    root.querySelector('.lock-actions').innerHTML = '<button type="submit">Открыть</button>';
    if (nativeBiometricEnabled(native, status.biometric) || (!tg?.initData && status.passkeys && await supportsBiometry())) {
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = native
        ? `Войти с ${biometricLabel(native.biometricType, tg.platform)}` : 'Войти с Passkey';
      root.querySelector('.lock-actions').append(button);
      button.onclick = async () => {
        try { await biometry(); finish(); } catch (failure) { error(root, failure); }
      };
    }
    let finish;
    await new Promise(resolve => {
      finish = () => { root.close(); resolve(); };
      root.querySelector('form').onsubmit = async event => {
        event.preventDefault();
        const submit = root.querySelector('[type="submit"]');
        submit.disabled = true;
        try {
          const result = await post('unlock', { password: root.querySelector('input').value });
          setUnlock(result.data.unlock); finish();
        } catch (failure) { error(root, failure); }
        finally { submit.disabled = false; }
      };
    });
  })();
  try { await pending; } finally { pending = null; }
}

async function clearOffline() {
  if ('caches' in window) {
    for (const name of await caches.keys()) {
      if (name.startsWith('ns-offline')) await caches.delete(name);
    }
  }
}

export async function openLockSettings() {
  const native = await nativeManager();
  const { data } = await api(native ? `/api/lock/status?device=${encodeURIComponent(native.deviceId)}` : '/api/lock/status');
  const root = panel('Защита входа');
  root.querySelector('.lock-fields').innerHTML = `<p>Пароль защищает дневник при открытии и возвращении в приложение. Он действует на всех устройствах.</p>
    <label>${data.enabled ? 'Новый пароль' : 'Пароль'}<input type="password" autocomplete="new-password" minlength="6" maxlength="128" required></label>
    <label>Повторите пароль<input type="password" autocomplete="new-password" minlength="6" maxlength="128" required></label>
    <p>От 6 символов. Если пароль забыт, сбросьте защиту командой /resetapplock в личном чате с ботом.</p>`;
  root.querySelector('.lock-actions').innerHTML = '<button type="submit">Сохранить пароль</button><button type="button" data-close>Закрыть</button>';
  root.querySelector('[data-close]').onclick = root.close;
  root.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const inputs = root.querySelectorAll('input[type="password"]');
    if (inputs[0].value !== inputs[1].value) { error(root, new Error('Пароли не совпадают')); return; }
    try {
      const result = await post('password', { password: inputs[0].value });
      setUnlock(result.data.unlock); active = true;
      await clearOffline(); root.close(); await openLockSettings();
    } catch (failure) { error(root, failure); }
  };
  if (data.enabled) {
    if (native) {
      const label = biometricLabel(native.biometricType, tg.platform);
      const row = document.createElement('label');
      row.className = 'switch-row';
      const text = document.createElement('span');
      text.textContent = label;
      const toggle = document.createElement('input');
      toggle.type = 'checkbox'; toggle.className = 'switch'; toggle.setAttribute('switch', '');
      toggle.checked = nativeBiometricEnabled(native, data.biometric);
      row.append(text, toggle);
      root.querySelector('.lock-fields').append(row);
      const hint = document.createElement('p');
      hint.textContent = 'Вход с биометрией на этом устройстве. Включается только этим тумблером.';
      root.querySelector('.lock-fields').append(hint);
      toggle.onchange = async () => {
        const wanted = toggle.checked;
        toggle.disabled = true;
        try {
          if (wanted) {
            await biometry(true);
            root.querySelector('.lock-error').textContent = `${label} включён`;
          } else {
            // Сначала отзыв на сервере: даже оставшийся в Telegram секрет больше не действует.
            await post('biometric/disable', { device: native.deviceId });
            biometricPrompt = true;
            try {
              await new Promise(resolve => native.updateBiometricToken('', resolve));
            } catch { /* секрет уже отозван; его удаление не должно возвращать тумблер */ }
            finally { biometricPrompt = false; }
            root.querySelector('.lock-error').textContent = `${label} выключен`;
          }
          toggle.checked = wanted;
        } catch (failure) { toggle.checked = !wanted; error(root, failure); }
        finally { toggle.disabled = false; }
      };
    } else if (!tg?.initData && await supportsBiometry()) {
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = 'Настроить Passkey';
      root.querySelector('.lock-actions').prepend(button);
      button.onclick = async () => {
        try { await passkey(true); root.querySelector('.lock-error').textContent = 'Passkey настроен'; }
        catch (failure) { error(root, failure); }
      };
    } else {
      root.querySelector('.lock-error').textContent = tg?.initData
        ? 'Биометрия недоступна в Telegram на этом устройстве. Вход по паролю работает.'
        : 'Passkey недоступен в этом браузере. Вход по паролю работает.';
    }
    const disable = document.createElement('button');
    disable.type = 'button'; disable.textContent = 'Выключить защиту';
    root.querySelector('.lock-actions').append(disable);
    disable.onclick = async () => {
      try { await post('disable'); active = false; setUnlock(''); root.close(); }
      catch (failure) { error(root, failure); }
    };
  }
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
