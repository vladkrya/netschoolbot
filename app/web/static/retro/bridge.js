// Мост между ретро-сайтом (netschool-retro, код в app.js — без изменений) и
// ботом. Их фронт ходит в свой сервер по /api/…; здесь эти запросы уходят в
// /api/retro/… нашего сервера (app/web/retro.py) с нашим удостоверением:
// initData Telegram, токен ссылки, разблокировка PIN. Ещё мост ведёт
// «Выйти» и окно входа обратно в приложение и открывает вложения писем
// через нашу страницу ожидания.
'use strict';

(function () {
  const config = window.RETRO || {};
  const base = config.base || '';
  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = (tg && tg.initData) || '';
  const realFetch = window.fetch.bind(window);

  // Их демо-режим — флаг demo в localStorage (state.ts): ставим его по адресу.
  try {
    if (config.demo) localStorage.setItem('demo', 'true');
    else localStorage.removeItem('demo');
  } catch { /* приватный режим */ }

  let unlock = '';
  try { unlock = sessionStorage.getItem('ns-retro-unlock') || ''; } catch { /* приватный режим */ }

  function headers(extra) {
    const result = new Headers(extra || {});
    result.set('X-Netschool-Client', initData ? 'telegram' : 'web');
    if (initData) result.set('X-Telegram-Init-Data', initData);
    if (config.token) result.set('X-Netschool-Token', config.token);
    if (unlock) result.set('X-Netschool-Unlock', unlock);
    return result;
  }

  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

  /** В обычное приложение. Один раз за сессию оно не отправит обратно сюда:
   *  иначе ошибка входа гоняла бы между страницами по кругу. */
  function toApp(settings = false) {
    try { sessionStorage.setItem('ns-retro-skip', '1'); } catch { /* приватный режим */ }
    location.replace((config.app_url || `${base}/`) + (settings ? '#settings' : ''));
  }

  /** «Выйти» на странице «Обо мне»: ретро выключается, открывается приложение. */
  async function leave() {
    try { localStorage.setItem('ns-retro', 'off'); } catch { /* приватный режим */ }
    if (!config.demo) {
      await realFetch(`${base}/api/prefs`, {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ retro: 'off' }),
      }).catch(() => {});
    }
    return json({ ok: true });
  }

  window.retroBridge = { toApp };

  document.addEventListener('click', async (event) => {
    const button = event.target.closest && event.target.closest('[data-retro-exit]');
    if (!button) return;
    event.preventDefault();
    // Сразу блокируем возврат в ретро, даже если сохранение ещё идёт.
    try { sessionStorage.setItem('ns-retro-skip', '1'); } catch { /* приватный режим */ }
    const saving = leave();
    await Promise.race([saving, new Promise(resolve => setTimeout(resolve, 1500))]);
    toApp(button.dataset.retroExit === 'settings');
  });

  window.fetch = async function (input, init) {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (typeof url !== 'string' || !url.startsWith('/api/')) return realFetch(input, init);
    const options = { ...(init || {}) };
    if (url === '/api/logout') return leave();
    // В демо сервера нет: гостевая книга пустая, писать в неё нельзя.
    if (config.demo) {
      if (url.startsWith('/api/guestbook')) return json({ entries: [], token: '', can_post: false });
      return json({ detail: 'В демо-режиме этого нет' }, 400);
    }
    options.headers = headers(options.headers);
    options.cache = 'no-store';
    const response = await realFetch(`${base}/api/retro/${url.slice(5)}`, options);
    // 423 — дневник закрыт PIN-кодом: ввести его можно только в приложении.
    if (response.status === 423) {
      toApp();
      return new Promise(() => {});
    }
    if (response.ok) return response;
    // Ошибки нашего сервера ({error}) — в их виде ({detail}): api.ts показывает detail.
    const body = await response.json().catch(() => ({}));
    return json({ detail: body.detail || body.error || `Ошибка ${response.status}` }, response.status);
  };

  // Вложения писем: ссылка их сайта ведёт на /api/mail/attachment/…, а файл
  // отдаёт наша страница ожидания по билету — заголовков в ссылку не положить.
  document.addEventListener('click', async (event) => {
    const link = event.target.closest && event.target.closest('a[href^="/api/mail/attachment/"]');
    if (!link) return;
    event.preventDefault();
    const match = link.getAttribute('href').match(/attachment\/(\d+)\?name=([^&]*)/);
    if (!match || config.demo) return;
    let credential = '';
    try {
      const response = await realFetch(`${base}/api/attachment-ticket`, { headers: headers(), cache: 'no-store' });
      credential = `ticket=${encodeURIComponent((await response.json()).data.ticket)}`;
    } catch {
      credential = initData ? `tgWebAppData=${encodeURIComponent(initData)}` : `token=${encodeURIComponent(config.token || '')}`;
    }
    const target = `${location.origin}${base}/attachment/${match[1]}?${credential}&name=${match[2]}`;
    if (initData && tg.openLink) tg.openLink(target, { try_instant_view: false });
    else window.open(target, '_blank', 'noopener');
  });

  if (tg) {
    try { tg.ready(); tg.expand(); } catch { /* старый клиент */ }
  }
})();
