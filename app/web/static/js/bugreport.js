// «Сообщить об ошибке или идее»: текст уходит админу бота, перед отправкой — KryaCaptcha.
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

// Что пишет человек: тексты экрана под ошибку и под идею.
const KINDS = {
  bug: {
    intro: 'Что пошло не так? Опишите, что вы делали и что увидели.',
    placeholder: 'Например: на вкладке «Оценки» не открывается предмет «Алгебра»',
    attach: '📎 Прикрепить скриншот или видео',
  },
  idea: {
    intro: 'Чего не хватает в приложении? Расскажите, что добавить или сделать удобнее.',
    placeholder: 'Например: хочу видеть средний балл класса рядом со своим',
    attach: '📎 Прикрепить картинку или набросок',
  },
};

function formData(kind, text, token, meta, files) {
  const data = new FormData();
  data.append('kind', kind);
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

export async function openBugReport(kind = 'bug') {
  const screen = pushScreen('Ошибка или идея');
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
    screen.innerHTML = '<div class="card"><p>Форма сейчас выключена. Попробуйте позже.</p></div>';
    return;
  }

  const frameSrc = `${base}/captcha-frame?${new URLSearchParams({
    bind: config.bind, theme: isDark() ? 'dark' : 'light',
  })}`;
  screen.innerHTML = `<div class="card bug-report">
      <div class="feedback-kinds" role="radiogroup" aria-label="О чём сообщение">
        <button class="feedback-kind" data-kind="bug" role="radio">
          <span class="feedback-kind-icon">🐞</span><b>Ошибка</b><span>что-то не работает</span></button>
        <button class="feedback-kind" data-kind="idea" role="radio">
          <span class="feedback-kind-icon">💡</span><b>Идея</b><span>чего не хватает</span></button>
      </div>
      <p id="bugIntro"></p>
      <p class="muted small">Сообщение получит разработчик. Логин и пароль писать не нужно.</p>
      <textarea class="field" id="bugText" rows="7" maxlength="4000"></textarea>
      <div id="bugFiles" class="compose-files"></div>
      <label class="btn secondary attach-btn"><span id="bugAttach"></span>
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

  const setKind = (value) => {
    kind = KINDS[value] ? value : 'bug';
    screen.querySelectorAll('.feedback-kind').forEach((button) => {
      const on = button.dataset.kind === kind;
      button.classList.toggle('active', on);
      button.setAttribute('aria-checked', String(on));
    });
    screen.querySelector('#bugIntro').textContent = KINDS[kind].intro;
    screen.querySelector('#bugAttach').textContent = KINDS[kind].attach;
    text.placeholder = KINDS[kind].placeholder;
  };
  screen.querySelectorAll('.feedback-kind').forEach((button) =>
    button.addEventListener('click', () => setKind(button.dataset.kind)));
  setKind(kind);

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
      banner(kind === 'idea' ? 'Опишите идею хотя бы парой слов' : 'Опишите, что случилось, хотя бы парой слов', true);
      text.focus();
      return;
    }
    send.disabled = true;
    send.textContent = files.length ? 'Загружаем…' : 'Отправляем…';
    try {
      await api('/api/bug-report', {
        method: 'POST',
        body: formData(kind, body, token, meta, files),
      });
      notify('success');
      banner(kind === 'idea' ? 'Спасибо за идею!' : 'Спасибо! Отчёт отправлен.', false, 4000);
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
