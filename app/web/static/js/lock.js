// Блокировка запрашивается до любых данных дневника; секрет сессии только в памяти.
import { api, demo, setUnlock, tg } from './core.js';
import { biometricLabel, nativeBiometricEnabled } from './biometrics.js';
import { mountPinpad } from './pinpad.js';

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
      setUnlock(result.data.unlock); active = true;
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
    const native = await nativeManager();
    let status;
    while (!status) {
      try { status = (await api(statusPath(native))).data; }
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
    root.querySelector('.lock-actions').innerHTML = '';
    const canBiometric = nativeBiometricEnabled(native, status.biometric) ||
      (!tg?.initData && status.passkeys && await supportsBiometry());
    const label = native ? biometricLabel(native.biometricType, tg.platform) : 'Touch ID / Face ID';
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
      const biometric = canBiometric ? { label, run: async () => { await biometry(); await finish(); } } : null;
      if (status.pin) {
        pad = mountPinpad(root, async pin => {
          const result = await post('unlock', { pin });
          setUnlock(result.data.unlock); await finish();
        }, { biometric });
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
        if (biometric) {
          const button = document.createElement('button'); button.type = 'button';
          button.textContent = `Войти с ${label}`;
          button.onclick = () => biometric.run().catch(failure => error(root, failure));
          root.querySelector('.lock-actions').append(button);
        }
      }
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
  const { data } = await api(statusPath(native));
  const root = panel('Защита входа');
  root.querySelector('.lock-fields').innerHTML = `<p>Четыре цифры, чтобы открыть дневник. PIN действует на всех ваших устройствах.</p>
    <div class="pin-setting-status">${data.enabled && data.pin ? 'PIN установлен · 4 цифры' : 'Установите PIN для защиты дневника'}</div>`;
  root.querySelector('.lock-actions').innerHTML = `<button type="button" data-pin>${data.enabled && data.pin ? 'Изменить PIN' : 'Задать PIN'}</button><button type="button" data-close>Готово</button>`;
  root.querySelector('[data-close]').onclick = root.close;
  root.querySelector('[data-pin]').onclick = async () => {
    root.remove();
    await choosePin({ changing: data.pin });
    await openLockSettings();
  };
  const hint = document.createElement('p');
  hint.textContent = 'Забыли PIN? Отправьте /resetapplock в личный чат с ботом.';
  root.querySelector('.lock-fields').append(hint);
  if (data.enabled && data.pin) {
    const supported = native || (!tg?.initData && await supportsBiometry());
    if (supported) {
      const label = native ? biometricLabel(native.biometricType, tg.platform) : 'Touch ID / Face ID';
      const row = document.createElement('label'); row.className = 'switch-row';
      const text = document.createElement('span'); text.textContent = `Вход по ${label}`;
      const toggle = document.createElement('input');
      toggle.type = 'checkbox'; toggle.className = 'switch'; toggle.setAttribute('switch', '');
      toggle.checked = native ? nativeBiometricEnabled(native, data.biometric) : data.passkeys > 0;
      row.append(text, toggle); root.querySelector('.lock-fields').append(row);
      const hint = document.createElement('p');
      hint.textContent = native ? 'Подтверждение на этом устройстве вместо PIN.' : 'Устройство подтверждает вход лицом, отпечатком или своим кодом. Выключение отзывает ключи входа для этого адреса дневника.';
      root.querySelector('.lock-fields').append(hint);
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
          root.querySelector('.lock-error').textContent = wanted ? 'Вход с биометрией включён' : 'Вход с биометрией выключен';
        } catch (failure) { toggle.checked = !wanted; error(root, failure); }
        finally { toggle.disabled = false; }
      };
    } else root.querySelector('.lock-error').textContent = 'Биометрия недоступна на этом устройстве. Вход по PIN работает.';
    const disable = document.createElement('button'); disable.type = 'button'; disable.textContent = 'Выключить защиту';
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
