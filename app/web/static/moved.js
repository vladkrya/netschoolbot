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
  try {
    if (Date.now() - Number(localStorage.getItem(KEY) || 0) < DAY) return;
  } catch { /* приватный режим */ }

  const ua = navigator.userAgent;
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  const android = /Android/.test(ua);
  const standalone = window.matchMedia?.('(display-mode: standalone)').matches || navigator.standalone === true;
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

  /** Ссылка на новый адрес с входом; без сессии на этом адресе — просто адрес. */
  async function link() {
    try {
      const headers = {};
      const token = window.NETSCHOOL?.token;
      if (token) headers['X-Netschool-Token'] = token;
      const initData = window.Telegram?.WebApp?.initData;
      if (initData) headers['X-Telegram-Init-Data'] = initData;
      const response = await fetch(`${base}/api/migrate-link`, { headers, credentials: 'same-origin' });
      const url = (await response.json())?.data?.url;
      if (response.ok && typeof url === 'string' && url.startsWith(`${target}/`)) return url;
    } catch { /* нет сети или не вошли */ }
    return `${target}/`;
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
    if (android) {
      return ['Нажмите «Открыть»: новый адрес откроется с уже выполненным входом.',
        'В Chrome нажмите меню ⋮ → «Установить приложение» (или «Добавить на главный экран»). Подсказка покажется на странице.',
        'Старую иконку СГОшки можно удалить.'];
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
    const list = make('ol', 'margin:0 0 14px;padding-left:22px');
    for (const text of steps()) list.append(make('li', 'margin:0 0 8px', text));
    const open = button('Открыть', 'width:100%');
    const cancel = button('Позже', 'width:100%;margin-top:8px;background:transparent;color:inherit;opacity:.7');
    cancel.onclick = () => veil.remove();
    open.onclick = async () => {
      open.disabled = true;
      open.textContent = 'Готовим ссылку…';
      const url = await link();
      open.disabled = false;
      open.textContent = 'Открыть';
      location.href = url;
    };
    card.append(list, open, cancel);
    veil.append(card);
    veil.addEventListener('click', (event) => { if (event.target === veil) veil.remove(); });
    document.body.append(veil);
  }

  const bar = make('div', 'position:fixed;left:8px;right:8px;bottom:calc(8px + env(safe-area-inset-bottom));' +
    'z-index:2147483000;display:flex;gap:10px;align-items:center;padding:12px 14px;border-radius:14px;' +
    'background:var(--card,#fff);color:var(--text,#1c2430);box-shadow:0 6px 24px rgba(0,0,0,.25);' +
    'max-width:520px;margin:0 auto;' + font);
  bar.setAttribute('role', 'status');
  const text = make('div', 'flex:1', `Сайт переехал на ${host}. Перенесём вход, заново логиниться не придётся.`);
  const go = button('Перенести');
  go.onclick = sheet;
  const close = make('button', 'border:0;background:none;color:inherit;font-size:22px;line-height:1;padding:4px 6px;cursor:pointer', '×');
  close.type = 'button';
  close.setAttribute('aria-label', 'Скрыть');
  close.onclick = () => {
    try { localStorage.setItem(KEY, String(Date.now())); } catch { /* приватный режим */ }
    bar.remove();
  };
  bar.append(text, go, close);
  document.body.append(bar);
})();
