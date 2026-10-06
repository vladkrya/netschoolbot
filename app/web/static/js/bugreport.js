// «Сообщить об ошибке»: текст уходит админу бота, перед отправкой — KryaCaptcha.
//
// Капча живёт в iframe (/captcha-frame) того же origin: стили её SDK задают
// правила для всего документа и сломали бы приложение. Рамка присылает
// postMessage {type: 'kryacaptcha', event}; пока открыто окно с головоломкой,
// iframe растягивается на весь экран.
'use strict';

import { api, banner, base, esc, tg } from './core.js';
import { pushScreen, popScreen } from './screens.js';
import { notify } from './haptics.js';

let listener = null;

// Как у сервера: до 5 файлов, не больше 15 МБ в сумме (nginx пропускает 16).
const MAX_FILES = 5;
const MAX_TOTAL_BYTES = 15 * 1024 * 1024;

function fileSize(bytes) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} МБ` : `${Math.max(1, Math.round(bytes / 1024))} КБ`;
}

function formData(text, token, meta, files) {
  const data = new FormData();
  data.append('text', text);
  data.append('token', token);
  data.append('meta', JSON.stringify(meta));
  for (const file of files) data.append('file', file, file.name);
  return data;
}

function isDark() {
  const theme = document.documentElement.getAttribute('data-theme');
  if (theme) return theme === 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function deviceInfo() {
  return {
    screen: document.querySelector('.nav-item.active')?.dataset.tab || '',
    platform: tg?.platform || navigator.platform || '',
    version: tg?.version || '',
    viewport: `${window.innerWidth}×${window.innerHeight}`,
    ua: navigator.userAgent,
  };
}

export async function openBugReport() {
  const screen = pushScreen('Сообщить об ошибке');
  screen.innerHTML = '<div class="muted small">Загрузка…</div>';
  // Тип вкладки берём до того, как экран перекроет приложение.
  const meta = deviceInfo();
  let config;
  try {
    ({ data: config } = await api('/api/bug-report'));
  } catch (error) {
    screen.innerHTML = `<div class="muted small">${esc(error.message)}</div>`;
    return;
  }
  if (!config.enabled) {
    screen.innerHTML = '<div class="card"><p>Отчёты об ошибках сейчас выключены. Напишите админу бота.</p></div>';
    return;
  }

  const frameSrc = `${base}/captcha-frame?${new URLSearchParams({
    bind: config.bind, theme: isDark() ? 'dark' : 'light',
  })}`;
  screen.innerHTML = `<div class="card bug-report">
      <p>Что пошло не так? Опишите, что вы делали и что увидели — сообщение получит разработчик.
      Логин и пароль писать не нужно.</p>
      <textarea class="field" id="bugText" rows="7" maxlength="4000"
        placeholder="Например: на вкладке «Оценки» не открывается предмет «Алгебра»"></textarea>
      <div id="bugFiles" class="compose-files"></div>
      <label class="btn secondary attach-btn">📎 Прикрепить фото или видео
        <input type="file" id="bugFile" accept="image/*,video/*" multiple hidden></label>
      <p class="muted small">Чтобы отличить вас от бота, пройдите капчу.</p>
      <iframe class="captcha-frame" id="bugCaptcha" title="Капча" src="${esc(frameSrc)}"></iframe>
      <p class="muted small" id="bugCaptchaState">Капча загружается…</p>
      <button class="btn" id="bugSend" disabled>Отправить</button>
    </div>`;

  const frame = screen.querySelector('#bugCaptcha');
  const state = screen.querySelector('#bugCaptchaState');
  const send = screen.querySelector('#bugSend');
  const text = screen.querySelector('#bugText');
  let token = '';
  const files = [];

  const renderFiles = () => {
    const list = screen.querySelector('#bugFiles');
    list.innerHTML = files
      .map((file, index) => `<div class="compose-file"><span class="compose-file-name">${esc(file.name)}</span>
          <span class="muted small">${fileSize(file.size)}</span>
          <button class="compose-file-x" data-drop="${index}" aria-label="Убрать файл">✕</button></div>`)
      .join('');
    list.querySelectorAll('[data-drop]').forEach((button) =>
      button.addEventListener('click', () => {
        files.splice(Number(button.dataset.drop), 1);
        renderFiles();
      })
    );
  };

  screen.querySelector('#bugFile').addEventListener('change', (event) => {
    for (const file of event.target.files) {
      const total = files.reduce((sum, item) => sum + item.size, 0) + file.size;
      const media = /^(image|video)\//.test(file.type);
      const problem = !media ? `«${file.name}» — не фото и не видео`
        : files.length >= MAX_FILES ? `Не больше ${MAX_FILES} файлов`
        : file.size === 0 ? `«${file.name}» пустой`
        : total > MAX_TOTAL_BYTES ? `Файлы больше ${MAX_TOTAL_BYTES / 1024 / 1024} МБ в сумме` : '';
      if (problem) {
        notify('error');
        banner(problem, true);
        continue;
      }
      files.push(file);
    }
    event.target.value = '';
    renderFiles();
  });

  // Экран — свой контекст наложения (z-index 30), панель вкладок выше него:
  // на время головоломки поднимаем весь экран, иначе панель закрывает капчу.
  const setFrameOpen = (open) => {
    frame.classList.toggle('expanded', open);
    frame.closest('.screen')?.classList.toggle('captcha-open', open);
  };

  if (listener) window.removeEventListener('message', listener);
  listener = (event) => {
    // Свой origin и именно наша рамка: другие окна сюда не пишут.
    if (event.origin !== location.origin || event.source !== frame.contentWindow) return;
    const data = event.data || {};
    if (data.type !== 'kryacaptcha') return;
    switch (data.event) {
      case 'ready':
        state.textContent = 'Нажмите «Я человек».';
        break;
      case 'error':
        state.textContent = 'Капча не загрузилась. Проверьте интернет и откройте экран заново.';
        break;
      case 'modal':
        setFrameOpen(Boolean(data.open));
        break;
      case 'token':
        token = String(data.token || '');
        setFrameOpen(false);
        state.textContent = 'Капча пройдена — можно отправлять.';
        send.disabled = !token;
        notify('success');
        break;
      case 'fail':
        token = '';
        setFrameOpen(false);
        send.disabled = true;
        state.textContent = 'Капча не пройдена. Попробуйте ещё раз.';
        notify('error');
        break;
    }
  };
  window.addEventListener('message', listener);

  send.addEventListener('click', async () => {
    const body = text.value.trim();
    if (body.length < 10) {
      banner('Опишите, что случилось, хотя бы парой слов', true);
      text.focus();
      return;
    }
    send.disabled = true;
    send.textContent = files.length ? 'Загружаем…' : 'Отправляем…';
    try {
      await api('/api/bug-report', {
        method: 'POST',
        body: formData(body, token, meta, files),
      });
      notify('success');
      banner('Спасибо! Отчёт отправлен.');
      window.removeEventListener('message', listener);
      listener = null;
      popScreen();
    } catch (error) {
      notify('error');
      banner(error.message, true);
      send.textContent = 'Отправить';
      if (error.reason === 'captcha') {
        // Токен одноразовый: после отказа нужна новая капча.
        token = '';
        frame.src = frameSrc;
        state.textContent = 'Пройдите капчу заново.';
      } else {
        send.disabled = !token;
      }
    }
  });
}
