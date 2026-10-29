// Плашка на прежних адресах: сайт переехал. «Перенести» берёт у сервера ссылку
// на новый домен с уже выполненным входом и показывает, как поставить
// приложение на экран заново. Подключается, только когда адрес страницы не
// основной (см. moved_to в app.html).
(() => {
  const script = document.currentScript;
  const target = script?.dataset.to || '';
  const base = script?.dataset.base || '';
  if (!target) return;
  // Android пока остаются на прежнем адресе: им позже выйдет новый APK.
  if (/Android/.test(navigator.userAgent)) return;
  const KEY = 'ns-moved-hidden';
  const DAY = 24 * 60 * 60 * 1000;
  const hiddenToday = () => {
    try { return Date.now() - Number(localStorage.getItem(KEY) || 0) < DAY; } catch { return false; }
  };

  const ua = navigator.userAgent;
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
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

  /** Срок отключения этого адреса, если приложение уже поставлено с нового; иначе null. */
  async function deadline() {
    try {
      const { api } = await import(script.src.replace(/moved\.js.*$/, 'js/core.js'));
      const value = (await api('/api/migrate-status'))?.data?.deadline;
      return typeof value === 'number' ? value : null;
    } catch { return null; }
  }

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

  /** Срок вышел: приложение здесь больше не открываем, зовём в новое. */
  function disabled() {
    const veil = make('div', 'position:fixed;inset:0;z-index:2147483002;background:var(--bg,#eef1f6);color:var(--text,#1c2430);' +
      'display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:24px;text-align:center;' + font);
    veil.append(make('div', 'font-size:20px;font-weight:700', 'Это приложение отключено'),
      make('div', 'max-width:360px', `СГОшка переехала на ${host}. Откройте новое приложение с иконки на экране «Домой».`));
    const open = button('Открыть сайт');
    open.onclick = () => { location.href = `${target}/`; };
    veil.append(open);
    document.body.append(veil);
  }

  (async () => {
    const due = await deadline();
    if (due !== null && Date.now() / 1000 >= due) return disabled();
    if (hiddenToday()) return;
    if (due !== null) {
      const date = new Date(due * 1000).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
      return bar(`Вы уже пользуетесь новым приложением на ${host}. Это перестанет работать ${date}, открывайте СГОшку с новой иконки.`);
    }
    const go = button('Перенести');
    go.onclick = sheet;
    bar(`Сайт переехал на ${host}. Перенесём вход, заново логиниться не придётся.`, go);
  })();
})();
