// Недоступность региона — одновременный отказ всех доступных способов.
export function availability(host, now = Date.now() / 1000) {
  const check = host.latest_check;
  const at = check?.checked_at ?? host.checked_at;
  if (!at || now - at > 180) return { state: 'unknown', label: 'Проверяем', tone: 'none' };
  const web = check ? check.ok : ['up', 'slow'].includes(host.web_state) ? true : host.error ? false : null;
  const mobile = check ? check.mobile : host.mobile;
  if (web === false && (mobile === false || !host.in_app)) {
    return { state: 'down', label: 'Сервер недоступен', tone: 'critical' };
  }
  if (web === true) {
    return { state: 'up', label: host.in_app && mobile === false ? 'Веб работает' : 'Работает', tone: 'good' };
  }
  if (mobile === true) return { state: 'up', label: 'Мобильное API работает', tone: 'good' };
  return { state: 'unknown', label: 'Проверяем', tone: 'none' };
}
