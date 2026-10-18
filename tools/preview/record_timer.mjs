const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
const [appUrl, dir] = process.argv.slice(2);
rmSync(dir, { recursive: true, force: true }); mkdirSync(dir, { recursive: true });
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 360, height: 760 }, deviceScaleFactor: 3, hasTouch: true, isMobile: true, colorScheme: 'dark', locale: 'ru-RU', timezoneId: 'Europe/Moscow' });
const page = await ctx.newPage();
await page.clock.install({ time: new Date('2026-10-15T10:29:55+03:00') });
await page.goto(appUrl);
await page.waitForTimeout(2600);
const box = await page.locator('#lessonTimer').boundingBox();
const frames = []; let n = 0;
const start = Date.now() / 1000;
const clip = { x: 0, y: box.y - 14, width: 360, height: box.height + 28 };
while (Date.now() / 1000 - start < 7) {
  const name = `f${String(n++).padStart(5, '0')}.png`;
  const t = Date.now() / 1000;
  await page.screenshot({ path: `${dir}/${name}`, clip, scale: 'device', animations: 'allow' });
  frames.push([name, (t + Date.now() / 1000) / 2]);
}
writeFileSync(`${dir}/timeline.json`, JSON.stringify({ start, frames, box }));
console.log('frames', n, JSON.stringify(box));
await browser.close();
