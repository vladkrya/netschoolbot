// Быстрый старт: PIN и проверка доступа не ждут загрузки всех экранов приложения.
import { api, demo, state, tg } from './core.js';
import { unlockApp } from './lock.js';
import { initIosHaptics } from './haptics.js';

async function boot() {
  tg?.ready();
  tg?.expand();
  initIosHaptics();
  await unlockApp();
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
