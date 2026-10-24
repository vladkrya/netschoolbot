// Ключи входа (passkeys): перевод между JSON сервера (app/web/passkeys.py)
// и WebAuthn браузера. Ключи блокировки (PIN) — отдельно, в lock.js.
'use strict';

import { tg } from './core.js';

function decode(value) {
  return Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
}
function encode(value) {
  return btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** В Telegram ключи не работают: его окно не даёт сайту связку ключей. */
export function passkeysSupported() {
  return !tg?.initData && window.isSecureContext && Boolean(window.PublicKeyCredential && navigator.credentials);
}

/** Окно системы «Использовать ключ входа?» закрыли — это не ошибка. */
export function cancelled(error) {
  return error?.name === 'NotAllowedError' || error?.name === 'AbortError';
}

/** options от сервера → ключ: create (register) или get. */
export async function usePasskey(options, register = false) {
  options.challenge = decode(options.challenge);
  if (options.user) options.user.id = decode(options.user.id);
  for (const list of ['allowCredentials', 'excludeCredentials']) {
    for (const item of options[list] || []) item.id = decode(item.id);
  }
  const credential = await navigator.credentials[register ? 'create' : 'get']({ publicKey: options });
  const response = {};
  for (const key of ['clientDataJSON', 'attestationObject', 'authenticatorData', 'signature', 'userHandle']) {
    if (credential.response[key]) response[key] = encode(credential.response[key]);
  }
  return {
    id: credential.id, rawId: encode(credential.rawId), type: credential.type, response,
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}
