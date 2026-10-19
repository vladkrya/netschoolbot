// Настройки и блокировка ретро используют ту же серверную защиту, что миниапп.
const bridge = window.retroBridge;
const demo = Boolean(window.RETRO?.demo);
const $ = id => document.getElementById(id);
const root = document.documentElement;
let protection = null;
let pendingUnlock = null;
let unlockedAt = 0;
const get = key => { try { return localStorage.getItem(key); } catch { return null; } };
const put = (key, value) => { try { localStorage.setItem(key, value); } catch { /* приватный режим */ } };

function appearance() {
  root.classList.toggle('retro-large', get('ns-retro-text') === 'large');
  root.classList.toggle('retro-no-motion', get('ns-motion') === 'off');
}
appearance();

function showLock() {
  root.classList.add('retro-locked');
  $('retroSettings').close();
  if (!$('retroLock').open) $('retroLock').showModal();
}
function reveal() {
  $('retroLock').close();
  root.classList.remove('retro-locked');
  unlockedAt = Date.now();
}

async function unlock(force = false) {
  if (demo) { reveal(); return; }
  protection = await bridge.request('lock/status');
  if (!protection.required && !force) { reveal(); return; }
  if (!force && bridge.hasUnlock()) {
    try { await bridge.request('prefs'); reveal(); return; }
    catch (error) { if (error.status !== 423) throw error; }
  }
  bridge.setUnlock('');
  showLock();
  const field = $('retroUnlockForm').elements.secret;
  field.value = '';
  field.inputMode = protection.pin ? 'numeric' : 'text';
  field.maxLength = protection.pin ? 4 : 128;
  $('retroLockHint').textContent = protection.pin ? 'Введите PIN, чтобы открыть дневник.' : 'Введите пароль защиты дневника.';
  $('retroLockError').textContent = '';
  field.focus();
  await new Promise(resolve => {
    $('retroUnlockForm').onsubmit = async event => {
      event.preventDefault();
      const button = event.currentTarget.querySelector('button');
      button.disabled = true;
      try {
        const result = await bridge.request('lock/unlock', protection.pin ? { pin: field.value } : { password: field.value });
        field.value = '';
        bridge.setUnlock(result.unlock);
        reveal();
        resolve();
      } catch (error) {
        $('retroLockError').textContent = error.message;
        field.value = '';
        field.focus();
      } finally { button.disabled = false; }
    };
  });
}
bridge.ensureUnlocked = (force = false) => {
  if (!pendingUnlock) pendingUnlock = unlock(force).finally(() => { pendingUnlock = null; });
  return pendingUnlock;
};
$('retroLock').addEventListener('cancel', event => event.preventDefault());
$('retroRecovery').onclick = () => bridge.toApp();

async function request(path, body) {
  try { return await bridge.request(path, body); }
  catch (error) {
    if (error.status !== 423) throw error;
    await bridge.ensureUnlocked(true);
    return bridge.request(path, body);
  }
}

async function refreshProtection() {
  protection = demo ? { enabled: false } : await request('lock/status');
  $('retroProtectionStatus').textContent = demo ? 'В демо защита недоступна.' : protection.enabled ? 'Защита включена.' : 'PIN не задан.';
  $('retroRequireTelegram').checked = Boolean(protection.require_telegram);
  $('retroLockOnReturn').checked = get('lockOnReturn') === '1';
  $('retroRequireTelegram').disabled = demo || !protection.enabled;
  $('retroLockOnReturn').disabled = demo || !protection.enabled;
  $('retroDisablePin').disabled = demo || !protection.enabled;
  $('retroLockNow').disabled = demo || !protection.enabled;
  for (const field of $('retroPinSettings').elements) field.disabled = demo;
}
async function settings() {
  $('retroSettingsError').textContent = '';
  $('retroPinSettings').reset();
  $('retroTextSize').value = get('ns-retro-text') === 'large' ? 'large' : 'normal';
  $('retroMotion').checked = get('ns-motion') !== 'off';
  $('retroSettings').showModal();
  try { await refreshProtection(); }
  catch (error) { $('retroSettingsError').textContent = error.message; }
}
document.addEventListener('click', event => {
  if (event.target.closest('[data-retro-settings]')) settings();
  if (event.target.closest('[data-settings-close]')) $('retroSettings').close();
});
$('retroTextSize').onchange = event => { put('ns-retro-text', event.target.value); appearance(); };
$('retroMotion').onchange = async event => {
  const value = event.target.checked ? 'on' : 'off';
  put('ns-motion', value); appearance();
  if (!demo) {
    try { await request('prefs', { motion: value }); }
    catch (error) { $('retroSettingsError').textContent = error.message; }
  }
};
$('retroLockOnReturn').onchange = event => put('lockOnReturn', event.target.checked ? '1' : '0');
$('retroPinSettings').onsubmit = async event => {
  event.preventDefault();
  const form = event.currentTarget;
  $('retroSettingsError').textContent = '';
  if (!/^[0-9]{4}$/.test(form.elements.pin.value)) { $('retroSettingsError').textContent = 'PIN должен состоять из 4 цифр.'; return; }
  if (form.elements.pin.value !== form.elements.confirm.value) { $('retroSettingsError').textContent = 'PIN не совпадают.'; return; }
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    const result = await request('lock/pin', { pin: form.elements.pin.value });
    bridge.setUnlock(result.unlock);
    form.reset();
    await refreshProtection();
  } catch (error) { $('retroSettingsError').textContent = error.message; }
  finally { button.disabled = demo; }
};
$('retroRequireTelegram').onchange = async event => {
  try {
    const result = await request('lock/settings', { require_telegram: event.target.checked });
    bridge.setUnlock(result.unlock);
    await refreshProtection();
  } catch (error) { event.target.checked = !event.target.checked; $('retroSettingsError').textContent = error.message; }
};
$('retroDisablePin').onclick = async () => {
  try { await request('lock/disable', {}); bridge.setUnlock(''); await refreshProtection(); }
  catch (error) { $('retroSettingsError').textContent = error.message; }
};
async function relock() {
  showLock();
  const logout = bridge.request('lock/logout', {}).catch(() => {});
  bridge.setUnlock('');
  await logout;
  await bridge.ensureUnlocked(true);
}
$('retroLockNow').onclick = () => relock().catch(error => { $('retroLockError').textContent = error.message; });
document.addEventListener('visibilitychange', () => {
  if (!protection?.enabled || pendingUnlock) return;
  if (document.hidden) {
    if (get('lockOnReturn') === '1') root.classList.add('retro-locked');
  } else if (get('lockOnReturn') === '1' || Date.now() - unlockedAt > 11 * 3600 * 1000) {
    relock().catch(error => { $('retroLockError').textContent = error.message; });
  }
});
window.addEventListener('pageshow', event => {
  if (event.persisted && protection?.required) relock().catch(error => { $('retroLockError').textContent = error.message; });
});

// Никакие данные ретро не запрашиваются до проверки защиты.
async function start() {
  try {
    await bridge.ensureUnlocked();
    await import('./app.js');
  } catch (error) {
    showLock();
    $('retroLockError').textContent = error.message;
    $('retroUnlockForm').onsubmit = event => { event.preventDefault(); start(); };
  }
}
start();
