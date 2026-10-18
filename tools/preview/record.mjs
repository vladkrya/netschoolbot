// Запись приложения размером с телефон (360×760, кадры 720×1520) и временной
// шкалы подписей и касаний — фон, рамку и подписи накладывает compose.py.
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';

const appUrl = process.argv[2];
const dir = process.argv[3];
rmSync(dir, { recursive: true, force: true });
mkdirSync(dir, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 360, height: 760 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true, colorScheme: 'dark', locale: 'ru-RU', timezoneId: 'Europe/Moscow' });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
// Конец урока 09:50–10:30 попадает в кадр.
await page.clock.install({ time: new Date('2026-10-15T10:29:55+03:00') });
await page.addInitScript(() => {
  if (sessionStorage.getItem('seeded')) return;
  sessionStorage.setItem('seeded', '1');
  // Учёт «увиденных» оценок начат давно — оценки прошлых дней вспыхнут как новые.
  localStorage.setItem('ns-seen-marks', JSON.stringify({ since: '2020-01-01', keys: [] }));
  localStorage.setItem('ns-shown-averages', '{}');
});
await page.goto(appUrl);

const cdp = await ctx.newCDPSession(page);
const frames = [];
const events = [];
let count = 0;
cdp.on('Page.screencastFrame', async ({ data, metadata, sessionId }) => {
  const name = `f${String(count++).padStart(5, '0')}.jpg`;
  writeFileSync(`${dir}/${name}`, Buffer.from(data, 'base64'));
  frames.push([name, metadata.timestamp]);
  await cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {});
});
const now = () => Date.now() / 1000;
const log = (event) => events.push({ t: now(), ...event });
const wait = (ms) => page.waitForTimeout(ms);
const caption = (title, sub = '') => log({ kind: 'caption', title, sub });
const finger = (x, y, on, press = false) => log({ kind: 'finger', x, y, on, press });
const touch = (type, x, y) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] });

async function swipe(x0, y0, x1, y1, ms = 260) {
  // Палец идёт по реальному времени: сколько бы ни шла каждая команда в
  // браузер, жест длится ms, как живой быстрый свайп.
  finger(x0, y0, true, true);
  await wait(60);
  await touch('touchStart', x0, y0);
  const begin = Date.now();
  for (;;) {
    const p = Math.min(1, (Date.now() - begin) / ms);
    const k = 1 - (1 - p) ** 2;
    const x = x0 + (x1 - x0) * k, y = y0 + (y1 - y0) * k;
    await touch('touchMove', x, y);
    finger(x, y, true, true);
    if (p >= 1) break;
  }
  await touch('touchEnd');
  finger(x1, y1, false);
}
async function tap(selector, nth = 0, dx = 0.5) {
  const box = await page.locator(selector).nth(nth).boundingBox({ timeout: 5000 });
  if (!box) throw new Error('нет элемента ' + selector);
  const x = box.x + box.width * dx, y = box.y + box.height / 2;
  finger(x, y, true); await wait(200);
  finger(x, y, true, true); await wait(90);
  await page.mouse.click(x, y);
  finger(x, y, true); await wait(110);
  finger(x, y, false);
}

await wait(3000); // приложение грузится
await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: 720, maxHeight: 1520, everyNthFrame: 1 });
log({ kind: 'start' });
const smoothTo = (selector) => page.evaluate((sel) => document.querySelector(sel)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), selector);
async function typeInto(selector, text) {
  await tap(selector);
  await page.keyboard.type(text, { delay: 28 });
}

caption('Таймер урока', 'до секунды');
await wait(1700);
caption('Урок окончен', 'и сразу — перемена');
await wait(1500);
await page.clock.fastForward(4200);
await wait(1500);

caption('Дни — свайпом', 'новые оценки вспыхивают');
await swipe(40, 470, 300, 474, 240);
await wait(1500);
caption('Оценка — в урок', 'перелетает в карточку');
await tap('.ds-mark', 0);
await wait(1000);
caption('Д/З выполнено', 'заливка от пальца');
await smoothTo('.screen [data-done-toggle]');
await wait(500);
await tap('.screen [data-done-toggle]', 0, 0.25);
await wait(800);
caption('Назад — свайпом', 'с любого места экрана');
await swipe(190, 430, 340, 434, 240);
await wait(1000);

caption('Неделя и календарь', 'неделя — свайпом, месяц — в календаре');
await swipe(300, 125, 70, 127, 240);
await wait(550);
await swipe(70, 125, 300, 127, 240);
await wait(500);
await tap('#dayTitle');
await wait(450);
await tap('[data-shift="1"]');
await wait(500);
await tap('[data-shift="-1"]');
await wait(400);
await tap('[data-date="2026-10-16"]');
await wait(700);

caption('Свои уроки', 'кружок или секция');
await smoothTo('[data-add-lesson]');
await wait(450);
await tap('[data-add-lesson]');
await wait(450);
await page.fill('#sheetBody .edit-subject', 'Робототехника');
await page.fill('#sheetBody .edit-start', '14:30');
await page.fill('#sheetBody .edit-end', '15:15');
await wait(350);
await tap('#sheetBody .edit-save');
await wait(500);
await smoothTo('.lesson-card:has(.edit-badge)');
await wait(900);
caption('К сегодня', 'одной кнопкой');
await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
await wait(450);
await tap('#dayToday');
await wait(600);

caption('Задания', 'отметил — и зачеркнулось');
await tap('.nav-item[data-tab="homework"]');
await wait(500);
await tap('.hw-card:not(.done) button.hw-check', 0, 0.5);
await wait(1300);
caption('Свои задачи', 'ко времени — с напоминанием');
await tap('[data-add-note]');
await wait(450);
await page.fill('#sheetBody .note-text', 'Кружок рисования');
await page.fill('#sheetBody .note-time', '16:30');
await page.dispatchEvent('#sheetBody .note-time', 'input');
await wait(700);
await tap('#sheetBody .note-save');
await wait(500);
await smoothTo('.hw-card.own');
await wait(900);

caption('Средний балл', 'с весами — и средний класса');
await tap('.nav-item[data-tab="totals"]');
await wait(1000);
await tap('.subject-card', 1);
await wait(1500);
await smoothTo('.screen .whatif');
await wait(500);
caption('А если получить «5»?', 'балл пересчитается сразу');
await tap('.screen .whatif-add [data-add="5"]');
await wait(600);
await tap('.screen .whatif-add [data-add="5"]');
await wait(600);
await tap('.screen .whatif-add [data-add="2"]');
await wait(900);
caption('Назад — и справа', 'от правого края');
await swipe(356, 430, 200, 434, 240);
await wait(600);

caption('Чаты класса', 'только ученики, по нику');
await tap('.nav-item[data-tab="mail"]');
await wait(500);
await tap('[data-mail-mode="chat"]');
await wait(500);
await page.fill('#chatNick', 'Otlichnik');
await tap('#chatAgree');
await tap('#chatJoin');
await wait(700);
await tap('[data-room="0"]');
await wait(700);
await page.fill('.screen textarea', 'Всем привет!');
await tap('.screen .chat-send');
await wait(900);
await swipe(190, 430, 340, 434, 240);
await wait(400);

caption('ИИ-помощник', 'знает ваши оценки');
await tap('.nav-item[data-tab="day"]');
await wait(450);
await tap('#aiFab');
await wait(550);
await page.fill('#aiText', 'Что подтянуть?');
await tap('#aiSend');
await wait(1800);
await swipe(190, 430, 340, 434, 240);
await wait(400);

caption('Тема и цвет', 'светлая, тёмная, любой акцент');
await tap('.nav-item[data-tab="settings"]');
await wait(600);
await smoothTo('.theme-seg');
await wait(450);
await tap('[data-theme-set="light"]');
await wait(900);
await tap('[data-accent]', 3);
await wait(700);

caption('Потяните вниз', 'чтобы обновить');
await tap('.nav-item[data-tab="day"]');
await wait(400);
await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
await wait(350);
await swipe(180, 250, 182, 400, 240);
await wait(1000);
log({ kind: 'end' });
await wait(3000);

await cdp.send('Page.stopScreencast');
await wait(300);
writeFileSync(`${dir}/timeline.json`, JSON.stringify({ frames, events }));
console.log('frames:', count, 'errors:', errors);
await browser.close();
