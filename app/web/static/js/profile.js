// Боковое меню (☰): ученик, оформление, уведомления, калькулятор —
// как в официальном приложении, где настройки живут в выдвижной панели.
'use strict';

import { $, api, banner, esc, pushEnabled, state } from './core.js';
import { loadCalc } from './calc.js';
import { pushScreen, syncBackButton } from './screens.js';

const THEME_KEY = 'ns-theme';

export async function loadProfile(force = false) {
  if (state.profile && !force) return state.profile;
  const { data } = await api('/api/profile');
  state.profile = data;
  return data;
}

function currentTheme() {
  try {
    return localStorage.getItem(THEME_KEY) || 'auto';
  } catch {
    // Приватный режим и запрет на хранилище — не повод ломать экран.
    return 'auto';
  }
}

export function applyStoredTheme() {
  const theme = currentTheme();
  if (theme === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', theme);
}

export function isDrawerOpen() {
  return $('drawer').classList.contains('open');
}

export function closeDrawer() {
  $('drawer').classList.remove('open');
  syncBackButton();
}

export async function openDrawer() {
  $('drawer').classList.add('open');
  syncBackButton();
  const panel = $('drawerBody');
  if (!state.profile) {
    panel.innerHTML = '<div class="muted small">Загрузка…</div>';
    try {
      await loadProfile();
    } catch (error) {
      panel.innerHTML = `<div class="muted small">${esc(error.message)}</div>`;
      return;
    }
  }
  render();
}

function render() {
  const data = state.profile;
  const theme = currentTheme();
  const radio = (value, label) =>
    `<label class="drawer-option"><input type="radio" name="theme" value="${value}" ${theme === value ? 'checked' : ''}><span>${label}</span></label>`;
  const labels = {
    grades: 'Оценки',
    changes: 'Изменения оценок',
    deletes: 'Удаления оценок',
    homework: 'Домашние задания',
    mail: 'Школьная почта',
    weekly_summary: 'Сводка по понедельникам',
  };

  $('drawerBody').innerHTML = `
    <section class="drawer-section">
      <div class="drawer-heading">Аккаунт ученика</div>
      <div class="drawer-name">${esc(data.name)}</div>
      <div class="muted small">${esc(data.school)}</div>
      ${data.students.length > 1 ? `<div class="drawer-children">${data.students
        .map((s) => `<button class="chip ${s.current ? 'active' : ''}" data-student="${s.id}">${esc(s.name)}</button>`)
        .join('')}</div>` : ''}
    </section>
    <section class="drawer-section">
      <div class="drawer-heading">Дополнительно</div>
      <button class="drawer-link" id="drawerCalc">🧮 Калькулятор баллов</button>
    </section>
    <section class="drawer-section">
      <div class="drawer-heading">Настройки</div>
      ${radio('light', 'Светлая тема')}
      ${radio('auto', 'Как в системе')}
      ${radio('dark', 'Тёмная тема')}
    </section>
    ${pushEnabled ? `<section class="drawer-section">
      <div class="drawer-heading">Уведомления в браузере</div>
      <button class="btn outline" id="pushBtn">Включить</button>
    </section>` : ''}
    <section class="drawer-section">
      <div class="drawer-heading">Уведомления в Telegram</div>
      ${Object.entries(labels)
        .map(([key, label]) => `<div class="drawer-option static"><span class="check">${data.notifications[key] ? '☑' : '☐'}</span><span>${label}</span></div>`)
        .join('')}
      <div class="muted small">Проверка каждые ${data.interval_minutes} мин · меняются в боте: /settings</div>
    </section>
    <div class="drawer-foot muted small">Дневник «Сетевого города»</div>`;

  $('drawerBody').querySelectorAll('input[name="theme"]').forEach((input) =>
    input.addEventListener('change', () => {
      try {
        localStorage.setItem(THEME_KEY, input.value);
      } catch {
        /* хранилище недоступно — тема продержится до перезагрузки */
      }
      applyStoredTheme();
    })
  );
  $('drawerBody').querySelectorAll('[data-student]').forEach((button) =>
    button.addEventListener('click', () => switchStudent(button))
  );
  $('drawerCalc').addEventListener('click', () => {
    closeDrawer();
    const body = pushScreen('Калькулятор баллов');
    body.innerHTML = '<div id="calcContent"></div>';
    loadCalc();
  });
  $('pushBtn')?.addEventListener('click', (event) => enablePush(event.target));
}

async function switchStudent(button) {
  button.disabled = true;
  try {
    await api('/api/student', {
      method: 'POST',
      body: JSON.stringify({ id: Number(button.dataset.student) }),
    });
    // Дневник другого ребёнка — совсем другие данные, сбрасываем всё.
    location.reload();
  } catch (error) {
    banner(error.message, true);
    button.disabled = false;
  }
}

async function enablePush(button) {
  button.disabled = true;
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      throw new Error('Браузер не поддерживает уведомления');
    }
    if ((await Notification.requestPermission()) !== 'granted') {
      throw new Error('Разрешение не выдано');
    }
    const { base, token } = window.NETSCHOOL;
    const registration = await navigator.serviceWorker.register(
      `${base}/sw.js?token=${encodeURIComponent(token)}`,
      { scope: `${base}/` }
    );
    const { data } = await api('/api/push/key');
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(data.key),
    });
    await api('/api/push/subscribe', {
      method: 'POST',
      body: JSON.stringify(subscription.toJSON()),
    });
    button.textContent = 'Уведомления включены ✓';
  } catch (error) {
    banner(error.message, true);
    button.disabled = false;
  }
}

function urlBase64ToUint8Array(value) {
  const padding = '='.repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((char) => char.charCodeAt(0)));
}
