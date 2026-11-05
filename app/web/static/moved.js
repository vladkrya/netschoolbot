// Плашка на прежних адресах: сайт переехал. «Перенести» берёт у сервера ссылку
// на новый домен с уже выполненным входом и показывает, как поставить
// приложение на экран заново. Подключается, только когда адрес страницы не
// основной (см. moved_to в app.html).
(() => {
  const script = document.currentScript;
  const target = script?.dataset.to || '';
  const base = script?.dataset.base || '';
  if (!target) return;
  const KEY = 'ns-moved-hidden';
  const DAY = 24 * 60 * 60 * 1000;
  const hiddenToday = () => {
    try { return Date.now() - Number(localStorage.getItem(KEY) || 0) < DAY; } catch { return false; }
  };

  const ua = navigator.userAgent;
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia?.('(display-mode: standalone)').matches || navigator.standalone === true;
  const telegram = Boolean(window.Telegram?.WebApp?.initData)
    || /Telegram/i.test(ua) || new URLSearchParams(location.search).has('tgWebAppData')
    || /(?:^|[&#])tgWebAppData=/.test(location.hash);
  const nativeAndroid = /; wv\)|SGOSHKAAndroid|NetschoolAndroid/i.test(ua);
  const installed = standalone || window.matchMedia?.('(display-mode: fullscreen)').matches
    || window.matchMedia?.('(display-mode: minimal-ui)').matches;
  if (!installed && !telegram && !nativeAndroid) {
    // Это вкладка браузера: дневник здесь больше не запускаем.
    window.SGOSHKA_MOVING = true;
    const destination = new URL(target);
    destination.pathname = location.pathname;
    destination.search = location.search;
    destination.hash = location.hash;
    destination.searchParams.delete('token');
    const moving = document.createElement('div');
    moving.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#f4f6fb;color:#182438;display:grid;place-items:center;font:16px system-ui;padding:24px;text-align:center';
    moving.textContent = 'Переходим на ' + new URL(target).host + '…';
    document.body.append(moving);
    (async () => {
      const timeout = setTimeout(() => location.replace(destination.href), 5000);
      try {
        // Вход переносится только через штатный endpoint с проверкой PIN.
        const { api } = await import(script.src.replace(/moved\.js.*$/, 'js/core.js'));
        if (window.NETSCHOOL?.token) {
          const result = await Promise.race([
            api('/api/migrate-link'),
            new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 5000)),
          ]);
          if (typeof result?.data?.url === 'string') {
            const signed = new URL(result.data.url);
            if (signed.origin === new URL(target).origin && signed.searchParams.has('enter')) {
              destination.pathname = signed.pathname;
              destination.searchParams.set('enter', signed.searchParams.get('enter'));
            }
          }
        }
      } catch { /* На новом домене можно войти заново. */ }
      clearTimeout(timeout);
      location.replace(destination.href);
    })();
    return;
  }
  // Установленные приложения и Telegram пока остаются на прежнем домене.
  // Ранее назначенный срок отключения к ним сейчас не применяется.
  if (nativeAndroid) return;
  const host = new URL(target).host;

  const style = (el, css) => { el.style.cssText = css; return el; };
  const make = (tag, css, text) => {
    const el = style(document.createElement(tag), css);
    if (text) el.textContent = text;
    return el;
  };
  const font = 'font:14px/1.4 -apple-system,system-ui,sans-serif;';
  const button = (label, css) => {
    const el = make('button', 'border:0;cursor:pointer;padding:10px 16px;border-radius:10px;background:#4a6fae;' +
      'color:#fff;font-weight:600;font-size:15px;' + (css || ''), label);
    el.type = 'button';
    return el;
  };

  /** Ссылка на новый адрес с входом или '' — не вышло. Запрос идёт через
   *  клиент самого приложения: он знает токен, разблокировку по PIN и режим
   *  Telegram, а без них сервер аккаунт с защитой не опознает. */
  async function link() {
    try {
      const { api } = await import(script.src.replace(/moved\.js.*$/, 'js/core.js'));
      const url = (await api('/api/migrate-link'))?.data?.url;
      if (typeof url === 'string' && url.startsWith(`${target}/`)) return url;
    } catch { /* нет сети или не вошли */ }
    return '';
  }

  function steps() {
    if (ios) {
      return standalone
        ? ['Нажмите «Открыть»: внизу откроется окно с новым адресом, вход выполнится сам.',
          'Нажмите значок компаса справа внизу («Открыть в Safari»).',
          'В Safari нажмите «Поделиться» → «На экран „Домой“» → «Добавить». Подсказка покажется на странице.',
          'Старую иконку СГОшки с экрана можно удалить.']
        : ['Нажмите «Открыть»: новый адрес откроется с уже выполненным входом.',
          'Если это Safari, нажмите «Поделиться» → «На экран „Домой“» → «Добавить». Подсказка покажется на странице.',
          'Старую иконку СГОшки с экрана можно удалить.'];
    }
    return ['Нажмите «Открыть»: новый адрес откроется с уже выполненным входом.'];
  }

  function sheet() {
    const veil = make('div', 'position:fixed;inset:0;z-index:2147483001;background:rgba(0,0,0,.5);display:flex;' +
      'align-items:flex-end;justify-content:center;padding:8px;' + font);
    const card = make('div', 'background:var(--card,#fff);color:var(--text,#1c2430);border-radius:16px;width:100%;' +
      'max-width:520px;padding:18px;padding-bottom:calc(18px + env(safe-area-inset-bottom));box-sizing:border-box;' +
      'max-height:90vh;overflow:auto');
    card.append(make('div', 'font-size:18px;font-weight:700;margin-bottom:6px', `Переезд на ${host}`),
      make('div', 'margin-bottom:12px;opacity:.8', 'Вход перенесётся сам, заново логиниться не нужно.'));
    const note = make('div', 'margin-bottom:12px;color:#d14343;display:none');
    note.setAttribute('role', 'alert');
    const list = make('ol', 'margin:0 0 14px;padding-left:22px');
    for (const text of steps()) list.append(make('li', 'margin:0 0 8px', text));
    const open = button('Открыть', 'width:100%');
    const cancel = button('Позже', 'width:100%;margin-top:8px;background:transparent;color:inherit;opacity:.7');
    cancel.onclick = () => veil.remove();
    let plain = false;
    open.onclick = async () => {
      if (plain) { location.href = `${target}/`; return; }
      open.disabled = true;
      open.textContent = 'Готовим ссылку…';
      const url = await link();
      open.disabled = false;
      if (url) { open.textContent = 'Открыть'; location.href = url; return; }
      // Вход перенести не вышло: честно говорим, а не открываем гостевой экран молча.
      plain = true;
      open.textContent = 'Открыть без входа';
      note.textContent = 'Не удалось перенести вход. Откройте новый адрес и войдите заново, как обычно.';
      note.style.display = 'block';
    };
    card.append(list, note, open, cancel);
    veil.append(card);
    veil.addEventListener('click', (event) => { if (event.target === veil) veil.remove(); });
    document.body.append(veil);
  }

  function hideButton(bar) {
    const close = make('button', 'border:0;background:none;color:inherit;font-size:22px;line-height:1;padding:4px 6px;cursor:pointer', '×');
    close.type = 'button';
    close.setAttribute('aria-label', 'Скрыть');
    close.onclick = () => {
      try { localStorage.setItem(KEY, String(Date.now())); } catch { /* приватный режим */ }
      bar.remove();
    };
    return close;
  }

  function bar(message, action) {
    const el = make('div', 'position:fixed;left:8px;right:8px;bottom:calc(8px + env(safe-area-inset-bottom));' +
      'z-index:2147483000;display:flex;gap:10px;align-items:center;padding:12px 14px;border-radius:14px;' +
      'background:var(--card,#fff);color:var(--text,#1c2430);box-shadow:0 6px 24px rgba(0,0,0,.25);' +
      'max-width:520px;margin:0 auto;' + font);
    el.setAttribute('role', 'status');
    el.append(make('div', 'flex:1', message));
    if (action) el.append(action);
    el.append(hideButton(el));
    document.body.append(el);
  }

  (async () => {
    if (hiddenToday()) return;
    const go = button('Перенести');
    go.onclick = sheet;
    bar(`Сайт переехал на ${host}. Перенесём вход, заново логиниться не придётся.`, go);
  })();
})();
