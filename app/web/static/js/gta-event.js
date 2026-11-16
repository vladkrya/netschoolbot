import { applyGtaViEvent, isGtaViEventTheme } from './appearance.js';
import { base } from './core.js';
import { getPref, setPref } from './prefs.js';

let dialog;
let checking = false;

export function syncGtaViEvent() {
  applyGtaViEvent();
  if (!isGtaViEventTheme() || getPref('gtaViEvent')) {
    dialog?.close();
    return false;
  }
  if (dialog?.open) return true;
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.className = 'gta-event-dialog';
    dialog.setAttribute('aria-labelledby', 'gtaEventTitle');
    dialog.innerHTML = `
      <h2 id="gtaEventTitle">GTA VI — временная тема</h2>
      <p>В ожидании легендарной GTA VI мы на время переоформили приложение в стиле классической Vice City. Это часть тематического события.</p>
      <p>Если хочется вернуться к привычному оформлению, это можно сделать прямо сейчас.</p>
      <div class="notice-actions">
        <button class="btn" type="button" autofocus>Закрыть</button>
        <button class="btn outline" type="button">Вернуть предыдущую тему</button>
      </div>`;
    const [close, restore] = dialog.querySelectorAll('button');
    const select = (value) => {
      setPref('gtaViEvent', value);
      syncGtaViEvent();
    };
    close.addEventListener('click', () => select('closed'));
    restore.addEventListener('click', () => select('off'));
    dialog.addEventListener('cancel', (event) => {
      event.preventDefault();
      select('closed');
    });
    document.body.append(dialog);
  }
  dialog.showModal();
  return true;
}

export async function initGtaViEvent() {
  const refresh = async () => {
    if (document.hidden || checking) return false;
    checking = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(base + '/theme-event.json', {
        cache: 'no-store', credentials: 'omit', signal: controller.signal,
      });
      if (!response.ok) return false;
      const { active } = await response.json();
      if (typeof active !== 'boolean') return false;
      applyGtaViEvent(active);
      return syncGtaViEvent();
    } catch {
      return false;
    } finally {
      clearTimeout(timeout);
      checking = false;
    }
  };
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('online', refresh);
  setInterval(refresh, 60_000);
  return refresh();
}
