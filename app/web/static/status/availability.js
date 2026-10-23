// Недоступность региона — одновременный отказ всех доступных способов.
export function availability(host, now = Date.now() / 1000) {
  const check = host.latest_check;
  const at = check?.checked_at ?? host.checked_at;
  const mobile = check ? check.mobile : host.mobile;
  // С наших серверов веб ни разу не открылся: это не сбой региона, а маршрут бота.
  if (host.state === 'blocked' && mobile !== true) {
    return { state: 'blocked', label: 'Недоступен с наших серверов', tone: 'none' };
  }
  if (!at || now - at > 180) return { state: 'unknown', label: 'Проверяем', tone: 'none' };
  const web = check ? check.ok : ['up', 'slow'].includes(host.web_state) ? true : host.error ? false : null;
  if (web === false && (mobile === false || !host.in_app)) {
    return { state: 'down', label: 'Сервер недоступен', tone: 'critical' };
  }
  // Сайт и мобильное API отвечают на проверки без входа, но дневники у людей
  // не грузятся (фоновые проверки и загрузки в приложении упираются в ошибку
  // сервера). Снаружи регион зелёным быть не должен — иначе «всё хорошо», а
  // войти нельзя.
  if (host.diary_issues) {
    return { state: 'issues', label: 'Проблемы с дневниками', tone: 'warning' };
  }
  if (web === true) {
    return { state: 'up', label: host.in_app && mobile === false ? 'Веб работает' : 'Работает', tone: 'good' };
  }
  if (mobile === true) return { state: 'up', label: 'Мобильное API работает', tone: 'good' };
  return { state: 'unknown', label: 'Проверяем', tone: 'none' };
}
