// Тип сообщает Telegram: модель iPhone не раскрывается в WebView/User-Agent.
export function biometricLabel(type, platform) {
  const apple = platform === 'ios' || platform === 'macos';
  if (type === 'face') return apple ? 'Face ID' : 'Распознавание лица';
  if (type === 'finger') return apple ? 'Touch ID' : 'Отпечаток пальца';
  return 'Биометрия';
}

export function nativeBiometricEnabled(manager, registered) {
  return Boolean(manager?.isBiometricAvailable && manager.isAccessGranted &&
    manager.isBiometricTokenSaved && registered);
}

/** Вне Telegram браузер не говорит, какая биометрия есть, — угадываем по
 *  устройству. iPhone без кнопки «Домой» (высота от 812pt) — Face ID. */
export function webBiometricLabel() {
  const ua = navigator.userAgent;
  const iPad = /iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  if (/iPhone|iPod/.test(ua)) return Math.max(screen.width, screen.height) >= 812 ? 'Face ID' : 'Touch ID';
  if (iPad) return 'Face ID или Touch ID';
  if (/Macintosh/.test(ua)) return 'Touch ID';
  if (/Windows/.test(ua)) return 'Windows Hello';
  return 'Биометрия';
}
