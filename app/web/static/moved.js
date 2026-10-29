// Плашка на прежних адресах: сайт переехал, приложение нужно поставить заново.
// Подключается только когда адрес страницы не основной (см. moved_to в app.html).
(() => {
  const script = document.currentScript;
  const target = script?.dataset.to || '';
  if (!target) return;
  const KEY = 'ns-moved-hidden';
  const DAY = 24 * 60 * 60 * 1000;
  try {
    if (Date.now() - Number(localStorage.getItem(KEY) || 0) < DAY) return;
  } catch { /* приватный режим */ }

  const host = new URL(target).host;
  const bar = document.createElement('div');
  bar.setAttribute('role', 'status');
  bar.style.cssText = 'position:fixed;left:8px;right:8px;bottom:calc(8px + env(safe-area-inset-bottom));' +
    'z-index:2147483000;display:flex;gap:10px;align-items:center;padding:12px 14px;border-radius:14px;' +
    'background:var(--card,#fff);color:var(--text,#1c2430);box-shadow:0 6px 24px rgba(0,0,0,.25);' +
    'font:14px/1.35 -apple-system,system-ui,sans-serif;max-width:520px;margin:0 auto';
  const text = document.createElement('div');
  text.style.flex = '1';
  text.textContent = `Сайт переехал на ${host}. Откройте новый адрес, войдите заново и ` +
    'установите приложение на экран ещё раз: это старый адрес, он скоро отключится.';
  const go = document.createElement('a');
  go.href = `${target}/`;
  go.textContent = 'Перейти';
  go.style.cssText = 'padding:8px 14px;border-radius:10px;background:#4a6fae;color:#fff;text-decoration:none;font-weight:600';
  const close = document.createElement('button');
  close.type = 'button';
  close.setAttribute('aria-label', 'Скрыть');
  close.textContent = '×';
  close.style.cssText = 'border:0;background:none;color:inherit;font-size:22px;line-height:1;padding:4px 6px;cursor:pointer';
  close.onclick = () => {
    try { localStorage.setItem(KEY, String(Date.now())); } catch { /* приватный режим */ }
    bar.remove();
  };
  bar.append(text, go, close);
  document.body.append(bar);
})();
