// Расписание недели картинкой: рисуется здесь же на canvas (со своими
// правками расписания), а дальше — в чат с ботом, чтобы переслать, в меню
// «Поделиться» или файлом на устройство.
'use strict';

import { addDays, api, dayMonth, demo, esc, initData, isoToday, lessonNo, mondayOf, WEEKDAYS_FULL } from './core.js';
import { dayData, ensureWeek, isHiddenWeekend } from './day.js';
import { closeSheet, openSheet } from './sheet.js';
import { notify, select, tap } from './haptics.js';

const WIDTH = 1080;
const PAD = 56;
const COLUMN_GAP = 28;
const ROW = 50;

/** Кнопка под расписанием недели. */
export function shareWeekButton(monday) {
  return `<button class="btn outline share-week" data-share-week="${monday}">🖼 Расписание картинкой</button>`;
}

export function bindShareWeek(container) {
  container.querySelectorAll('[data-share-week]').forEach((button) => button.addEventListener('click', (event) => {
    event.stopPropagation();
    openScheduleImage(button.dataset.shareWeek);
  }));
}

function color(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function fit(ctx, text, width) {
  if (ctx.measureText(text).width <= width) return text;
  let cut = text;
  while (cut.length > 1 && ctx.measureText(`${cut}…`).width > width) cut = cut.slice(0, -1);
  return `${cut}…`;
}

// Чей это дневник: сайт и бот внизу картинки — её пересылают дальше.
let botName = null;
async function loadBotName() {
  if (botName !== null || demo) return botName || '';
  try {
    const { data } = await api('/api/account/config');
    botName = (data.bot || '').split('/').pop();
  } catch {
    return '';
  }
  return botName;
}

/** Неделя в две колонки: дни сверху вниз, у каждого — уроки со временем и кабинетом. */
export function drawWeek(monday, { site = location.host, bot = '' } = {}) {
  const days = [];
  for (let index = 0; index < 7; index += 1) {
    const iso = addDays(monday, index);
    const lessons = dayData(iso)?.lessons || [];
    if (!lessons.length && (index >= 5 || isHiddenWeekend(iso))) continue;
    days.push({ iso, index, lessons });
  }
  const columns = 2;
  const columnWidth = (WIDTH - PAD * 2 - COLUMN_GAP) / columns;
  const blockHeight = (day) => 78 + Math.max(1, day.lessons.length) * ROW + 24;
  const heights = [0, 0];
  const placed = days.map((day) => {
    const column = heights[0] <= heights[1] ? 0 : 1;
    const top = heights[column];
    heights[column] += blockHeight(day) + COLUMN_GAP;
    return { ...day, column, top };
  });
  const header = 186;
  const footer = 110;
  const height = header + Math.max(...heights, 200) + PAD - COLUMN_GAP + footer;
  const canvas = document.createElement('canvas');
  canvas.width = WIDTH;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  const accent = color('--primary', '#3b6fc4');
  const font = (weight, size) => `${weight} ${size}px -apple-system, "Segoe UI", Roboto, Arial, sans-serif`;

  ctx.fillStyle = '#f3f4f8';
  ctx.fillRect(0, 0, WIDTH, height);
  ctx.fillStyle = '#14171f';
  ctx.font = font(800, 54);
  ctx.fillText('Расписание', PAD, PAD + 46);
  ctx.fillStyle = '#6c7280';
  ctx.font = font(500, 34);
  ctx.fillText(`${dayMonth(monday)} – ${dayMonth(addDays(monday, 6))}`, PAD, PAD + 94);

  for (const day of placed) {
    const x = PAD + day.column * (columnWidth + COLUMN_GAP);
    const y = header + day.top;
    const h = blockHeight(day);
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.roundRect(x, y, columnWidth, h, 28);
    ctx.fill();
    ctx.fillStyle = accent;
    ctx.font = font(800, 34);
    ctx.fillText(WEEKDAYS_FULL[day.index], x + 28, y + 52);
    ctx.fillStyle = '#6c7280';
    ctx.font = font(500, 26);
    const date = dayMonth(day.iso);
    ctx.fillText(date, x + columnWidth - 28 - ctx.measureText(date).width, y + 50);
    if (!day.lessons.length) {
      ctx.fillStyle = '#6c7280';
      ctx.font = font(500, 28);
      ctx.fillText('Уроков нет', x + 28, y + 78 + 34);
      continue;
    }
    day.lessons.forEach((lesson, row) => {
      const line = y + 78 + row * ROW + 34;
      ctx.fillStyle = '#9aa0ab';
      ctx.font = font(700, 26);
      ctx.fillText(String(lessonNo(lesson) || row + 1), x + 28, line);
      ctx.fillStyle = '#6c7280';
      ctx.font = font(500, 24);
      const time = lesson.start || '';
      ctx.fillText(time, x + 64, line);
      ctx.fillStyle = '#14171f';
      ctx.font = font(600, 28);
      const room = lesson.room ? ` · ${String(lesson.room).split(' ')[0]}` : '';
      ctx.fillText(fit(ctx, `${lesson.subject}${room}`, columnWidth - 172), x + 144, line);
    });
  }
  const line = height - footer + 20;
  ctx.fillStyle = '#dfe2e8';
  ctx.fillRect(PAD, line, WIDTH - PAD * 2, 2);
  ctx.fillStyle = accent;
  ctx.font = font(700, 30);
  ctx.fillText(site, PAD, line + 56);
  if (bot) {
    ctx.fillStyle = '#6c7280';
    ctx.font = font(600, 28);
    const label = `Telegram: @${bot}`;
    ctx.fillText(label, WIDTH - PAD - ctx.measureText(label).width, line + 56);
  }
  return canvas;
}

const toBlob = (canvas) => new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));

export async function openScheduleImage(monday = mondayOf(isoToday())) {
  tap();
  const body = openSheet('Расписание картинкой', '<div class="muted small">Рисую…</div>');
  try {
    await ensureWeek(monday);
  } catch (error) {
    body.innerHTML = `<div class="muted small">${esc(error.message)}</div>`;
    return;
  }
  const canvas = drawWeek(monday, { bot: await loadBotName() });
  const blob = await toBlob(canvas);
  const file = new File([blob], `raspisanie-${monday}.png`, { type: 'image/png' });
  const url = URL.createObjectURL(blob);
  const canShare = Boolean(navigator.canShare?.({ files: [file] }));
  const chat = Boolean(initData) || !demo;
  body.innerHTML = `<img class="share-preview" src="${url}" alt="Расписание недели">
    <div class="share-actions">
      ${chat && !demo ? '<button class="btn" id="shareChat">Прислать в чат с ботом</button>' : ''}
      ${canShare ? '<button class="btn outline" id="shareSystem">Поделиться…</button>' : ''}
      <a class="btn outline" id="shareSave" href="${url}" download="${file.name}">Сохранить картинку</a>
    </div>
    <div class="muted small share-status"></div>`;
  const status = body.querySelector('.share-status');
  body.querySelector('#shareChat')?.addEventListener('click', async (event) => {
    select();
    const button = event.currentTarget;
    button.disabled = true;
    status.textContent = 'Отправляю…';
    try {
      await api(`/api/share/schedule?caption=${encodeURIComponent(`Расписание на ${dayMonth(monday)} – ${dayMonth(addDays(monday, 6))}`)}`,
        { method: 'POST', body: blob, headers: { 'Content-Type': 'image/png' } });
      notify('success');
      status.textContent = 'Готово: картинка в чате с ботом — оттуда её можно переслать.';
    } catch (error) {
      notify('error');
      status.textContent = error.reason === 'no_telegram' ? 'Telegram не привязан — сохраните картинку или поделитесь ею.' : error.message;
    } finally {
      button.disabled = false;
    }
  });
  body.querySelector('#shareSystem')?.addEventListener('click', async () => {
    select();
    try {
      await navigator.share({ files: [file], title: 'Расписание' });
      closeSheet();
    } catch { /* передумали */ }
  });
}
