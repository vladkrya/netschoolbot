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
