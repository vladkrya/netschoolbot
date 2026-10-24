// Настройки → «Иконка приложения»: своя картинка на экране «Домой».
// Работает сразу, владелец проверяет её потом (Админ-настройки → Иконки);
// не прошла проверку — её отзывают.
// Сервер — app/web/app_icon.py.
'use strict';

import { api, banner, base, esc, showFailure } from './core.js';
import { notify, tap } from './haptics.js';
import { openedIn, openInBrowser, openInstall, steps, wantsInstall } from './install.js';
import { onScreenRefresh, pushScreen } from './screens.js';

const SIDE = 512;
const DEFAULT = () => `${base}/static/icon-512.png`;

const STEPS = [
  'Нажмите «Выбрать картинку» и выберите фото или картинку PNG, JPEG или WebP из галереи',
  'Лучше берите квадратную: у вытянутой останется только середина, края обрежутся. Что получится, видно на картинке сверху',
  'Нажмите «Поставить». Передумали — «Отмена», и выберите другую',
];
const KEEP_OLD = 'Появится вторая иконка, уже с новой картинкой. Старую пока не удаляйте: если новую отзовут, удалите новую, а старая останется';

/** Как обновить иконку на экране «Домой» — смотря где открыт дневник. */
function refreshSteps() {
  const { platform, place } = openedIn();
  if (platform === 'android') return ['Android сам заменит иконку на экране «Домой» в течение дня — больше ничего делать не нужно'];
  if (platform !== 'ios') {
    return ['<b>Android</b> сам заменит иконку на экране «Домой» в течение дня. <b>iPhone</b> так не умеет: '
      + 'откройте дневник в Safari и добавьте его на экран «Домой» ещё раз. ' + KEEP_OLD];
  }
  if (place === 'telegram') {
    return [
      'iPhone сам иконку не заменит. Нажмите «Открыть в Safari» ниже — дневник откроется уже с новой картинкой',
      'Там нажмите «Поделиться» → «На экран „Домой“». ' + KEEP_OLD,
    ];
  }
  if (place === 'app') {
    return [
      'iPhone сам иконку не заменит. Откройте дневник в Safari. Если он там уже открыт — обновите страницу, иначе добавится старая картинка',
      'Нажмите «Поделиться» → «На экран „Домой“». ' + KEEP_OLD,
    ];
  }
  if (place === 'safari') {
    return ['iPhone сам иконку не заменит — добавьте дневник на экран «Домой» ещё раз прямо отсюда, обновлять страницу не нужно',
      ...steps(), KEEP_OLD];
  }
  return ['iPhone сам иконку не заменит. Откройте дневник в Safari и нажмите «Поделиться» → «На экран „Домой“». ' + KEEP_OLD];
}

/** iPhone берёт картинку для экрана «Домой» из apple-touch-icon открытой
 *  страницы — меняем её сразу, чтобы не пришлось перезагружать дневник. */
function applyToPage(data) {
  const link = document.querySelector('link[rel="apple-touch-icon"]');
  if (link) link.href = data.active ? data.active.replace(/\/512\.png$/, '/180.png') : `${base}/static/apple-touch-icon.png`;
}

/** Середина картинки квадратом 512×512 — на сервер уходит уже она: так
 * запрос маленький, а телефонные фото по 10 МБ не упираются в лимит. */
async function squared(file) {
  const bitmap = await createImageBitmap(file).catch(() => null);
  if (!bitmap) throw new Error('Не удалось открыть картинку — нужна PNG, JPEG или WebP');
  const side = Math.min(bitmap.width, bitmap.height);
  if (side < 64) throw new Error('Картинка слишком маленькая — нужно хотя бы 64×64');
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = SIDE;
  canvas.getContext('2d').drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, SIDE, SIDE);
  bitmap.close?.();
  const png = canvas.toDataURL('image/png');
  // Фото в PNG бывает тяжелее лимита — тогда JPEG (прозрачности в фото нет).
  return png.length < 900_000 ? png : canvas.toDataURL('image/jpeg', 0.9);
}

export function openAppIcon() {
  const body = pushScreen('Иконка приложения', 'на экране «Домой»');
  body.innerHTML = '<div class="muted small">Загрузка…</div>';
  let chosen = '';

  const draw = (data) => {
    const shown = chosen || data.active || DEFAULT();
    const status = chosen ? 'Так она будет выглядеть.'
      : data.checking ? 'Своя иконка работает. Её ещё проверит администратор — если проверка не пройдёт, иконку отзовут.'
        : data.active ? 'Своя иконка работает, проверку она прошла.'
          : 'Стандартная иконка.';
    body.innerHTML = `<div class="card app-icon-card">
        <img class="app-icon-preview" src="${esc(shown)}" alt="" width="96" height="96">
        <div class="muted small">${status}</div>
        ${data.rejected && !chosen && !data.active ? `<div class="app-icon-rejected small">Прошлую иконку отозвали: ${data.comment ? `${esc(data.comment)}` : 'она не прошла проверку'}.</div>` : ''}
        <input type="file" accept="image/png,image/jpeg,image/webp" hidden data-file>
        <div class="app-icon-actions">
          ${chosen ? '<button class="btn" data-send>Поставить</button><button class="btn outline" data-cancel>Отмена</button>'
            : `<button class="btn" data-pick>${data.active ? 'Выбрать другую' : 'Выбрать картинку'}</button>`}
          ${!chosen && data.active ? '<button class="btn outline" data-reset>Вернуть стандартную</button>' : ''}
        </div>
      </div>
      ${wantsInstall() ? `<div class="card app-icon-install">
          <div class="small">Иконка видна только у дневника, добавленного на экран «Домой». В Telegram и в браузере её не видно.</div>
          <button class="btn outline" data-install>Как добавить на экран «Домой»</button>
        </div>` : ''}
      <div class="card">
        <div class="app-icon-howto-title">Как поставить свою иконку</div>
        <ol class="install-steps">
          ${[...STEPS, ...refreshSteps()].map((step, index) => `<li><span class="install-num">${index + 1}</span><span>${step}</span></li>`).join('')}
        </ol>
        ${openedIn().platform === 'ios' && openedIn().place === 'telegram' ? '<button class="btn outline app-icon-safari" data-safari>Открыть в Safari</button>' : ''}
      </div>
      <p class="muted small device-note">Иконка начинает работать сразу, а потом её проверяет администратор.
        Если проверка не пройдёт, иконку отзовут и вернётся стандартная. Чтобы снова поставить стандартную иконку,
        нажмите «Вернуть стандартную» и обновите иконку на экране так же, как описано выше.</p>`;
    bind(data);
  };

  const bind = (data) => {
    const input = body.querySelector('[data-file]');
    body.querySelector('[data-pick]')?.addEventListener('click', () => { tap(); input.click(); });
    body.querySelector('[data-install]')?.addEventListener('click', () => { tap(); openInstall(); });
    body.querySelector('[data-safari]')?.addEventListener('click', openInBrowser);
    input.addEventListener('change', async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        chosen = await squared(file);
        draw(data);
      } catch (error) {
        notify('error');
        banner(error.message, true);
      }
    });
    body.querySelector('[data-cancel]')?.addEventListener('click', () => { tap(); chosen = ''; draw(data); });
    body.querySelector('[data-send]')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.classList.add('loading');
      try {
        const { data: fresh } = await api('/api/app-icon', { method: 'POST', body: JSON.stringify({ image: chosen }) });
        chosen = '';
        notify('success');
        banner('Иконка поставлена. Если проверка не пройдёт, её отзовут');
        applyToPage(fresh);
        draw(fresh);
      } catch (error) {
        notify('error');
        banner(error.message, true);
        button.classList.remove('loading');
      }
    });
    body.querySelector('[data-reset]')?.addEventListener('click', async () => {
      if (!window.confirm('Вернуть стандартную иконку? Своя перестанет работать.')) return;
      try {
        const { data: fresh } = await api('/api/app-icon/reset', { method: 'POST', body: '{}' });
        notify('success');
        applyToPage(fresh);
        draw(fresh);
      } catch (error) {
        notify('error');
        banner(error.message, true);
      }
    });
  };

  const load = () => api('/api/app-icon')
    .then(({ data }) => draw(data))
    .catch((error) => showFailure(body, error, `<div class="muted small">${esc(error.message)}</div>`));
  onScreenRefresh(body, load);
  load();
}
