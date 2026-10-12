// Быстрый старт: PIN и проверка доступа не ждут загрузки всех экранов приложения.
import { api, demo, state, tg, token } from './core.js';
import { unlockApp } from './lock.js';
import { initIosHaptics } from './haptics.js';

async function boot() {
  tg?.ready();
  tg?.expand();
  initIosHaptics();
  // Ни Telegram, ни ссылки с токеном — вход по почте.
  if (!demo && !token && !tg?.initData) {
    const { showAuth } = await import('./account.js');
    await showAuth();
    return;
  }
  await unlockApp();
  // Ещё не вошли в «Сетевой город» — сначала вход, потом дневник.
  if (!demo) {
    const info = await api('/api/account').then(({ data }) => data, () => null);
    if (info?.needs_sgo) {
      const { showSgoLogin } = await import('./account.js');
      await showSgoLogin(info);
      return;
    }
  }
  // Первый запрос идёт параллельно загрузке модулей интерфейса, после разблокировки.
  if (!demo) state.initialDiary = api('/api/diary?week=0&window=current', {
    onFresh: fresh => {
      state.initialDiaryFresh = fresh;
      window.dispatchEvent(new CustomEvent('netschool:diaryfresh', { detail: fresh }));
    },
  })
    .then(payload => ({ payload }), error => ({ error }));
  for (const url of window.NETSCHOOL.modules || []) {
    if (document.querySelector(`link[rel="modulepreload"][href="${url}"]`)) continue;
    const link = document.createElement('link'); link.rel = 'modulepreload'; link.href = url;
    document.head.append(link);
  }
  await import('./app.js');
  document.getElementById('bootSplash')?.remove();
  try { sessionStorage.removeItem('bootRetries'); } catch { /* приватный режим */ }
}
boot().catch(() => window.netschoolBootFailed());
