// Страница статуса «Сетевого города»: данные — /status/data, жалобы — /status/report.
import { watchStatus } from './live.js';
import { availability } from './availability.js';

const base = document.body.dataset.base || '';
const $ = (selector) => document.querySelector(selector);
const SVG = 'http://www.w3.org/2000/svg';

const ERRORS = {
  timeout: 'таймаут',
  connect: 'нет соединения',
  dns: 'ошибка DNS',
  ssl: 'ошибка TLS-сертификата',
  unavailable: 'сервер недоступен',
  ProtocolError: 'ошибка соединения',
  maintenance: 'технические работы — объявление на сервере',
  invalid_response: 'сервер вернул неожиданный ответ',
};

let data = null;
let range = '24h';
let query = '';
const expanded = new Set();
try { range = localStorage.getItem('status-range') || '24h'; } catch { range = '24h'; }
if (!['24h', '7d', '30d'].includes(range)) range = '24h';

// ───────────────────────────── форматирование ─────────────────────────────

const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });
const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' });
const fullFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

function when(ts) {
  if (!ts) return '—';
  const date = new Date(ts * 1000);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return `сегодня в ${timeFmt.format(date)}`;
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return `вчера в ${timeFmt.format(date)}`;
  return fullFmt.format(date);
}

function ago(ts) {
  if (!ts) return '—';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'только что';
  if (s < 3600) return `${Math.round(s / 60)} мин назад`;
  if (s < 86400) return `${Math.round(s / 3600)} ч назад`;
  return when(ts);
}

function duration(seconds) {
  const m = Math.max(1, Math.round(seconds / 60));
  if (m < 60) return `${m} мин`;
  const h = Math.floor(m / 60), rest = m % 60;
  if (h < 48) return rest ? `${h} ч ${rest} мин` : `${h} ч`;
  return `${Math.round(h / 24)} дн`;
}

function plural(n, one, few, many) {
  const a = n % 10, b = n % 100;
  if (a === 1 && b !== 11) return one;
  if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
  return many;
}

const pct = (v) => (v == null ? '—' : `${v >= 99.995 ? '100' : v.toFixed(v >= 99 ? 2 : 1)}%`);
const errorText = (e) => (!e ? '' : ERRORS[e] || ({ 'http 403': 'доступ запрещён (403)', 'http 503': 'сервис недоступен (503)' })[e] || (e.startsWith('http ') ? `ошибка HTTP ${e.slice(5)}` : e));
const name = (h) => h.region || 'Регион без названия';

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function tone(share) {
  if (share == null) return 'none';
  if (share >= 0.99) return 'good';
  if (share >= 0.9) return 'warning';
  if (share >= 0.5) return 'serious';
  return 'critical';
}

function bucketLabel(index) {
  const start = new Date((data.since + index * data.bucket_s) * 1000);
  const end = new Date((data.since + (index + 1) * data.bucket_s) * 1000);
  if (data.bucket_s < 86400 && start.toDateString() === end.toDateString()) {
    return `${dateFmt.format(start)}, ${timeFmt.format(start)}–${timeFmt.format(end)}`;
  }
  return `${fullFmt.format(start)} – ${fullFmt.format(end)}`;
}

// ───────────────────────────── подсказка ─────────────────────────────

const tip = $('#tooltip');
// Мышью подсказка ходит за курсором; пальцем наведения нет — она открывается
// касанием и висит, пока не коснутся другого места.
let touchInput = false;
document.addEventListener('pointerdown', (event) => { touchInput = event.pointerType !== 'mouse'; }, true);
let tipKey = null, tipUndo = null;

function showTip(event, html) {
  tip.innerHTML = html;
  tip.hidden = false;
  const pad = 12;
  const { innerWidth: w, innerHeight: h } = window;
  const box = tip.getBoundingClientRect();
  let x, y;
  if (touchInput) {
    // Над пальцем, иначе он закроет текст.
    x = Math.min(event.clientX - box.width / 2, w - box.width - 8);
    y = event.clientY - box.height - 2 * pad;
    if (y < 8) y = event.clientY + 2 * pad;
  } else {
    x = event.clientX + pad; y = event.clientY + pad;
    if (x + box.width > w - 8) x = event.clientX - box.width - pad;
    if (y + box.height > h - 8) y = event.clientY - box.height - pad;
  }
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}
function hideTip() {
  tip.hidden = true;
  tipUndo?.();
  tipKey = tipUndo = null;
}
/** Подсказка по касанию: повторное касание того же места её закрывает.
 *  highlight подсвечивает выбранное и возвращает, как подсветку снять. */
function tapTip(event, key, html, highlight) {
  event.stopPropagation();
  event.preventDefault();
  const same = !tip.hidden && tipKey === key;
  hideTip();
  if (same) return;
  showTip(event, html);
  tipKey = key;
  tipUndo = highlight?.() || null;
}
document.addEventListener('click', () => { if (touchInput) hideTip(); });

// В приложении страница живёт в рамке, а из рамки внутри Telegram ссылки
// с target=_blank не открываются. Адрес отдаём приложению — оно откроет его
// через openLink, как остальные внешние ссылки.
if (document.body.classList.contains('embed') && window.parent !== window) {
  document.addEventListener('click', (event) => {
    const link = event.target.closest?.('a[target="_blank"]');
    if (!link) return;
    event.preventDefault();
    window.parent.postMessage({ type: 'status-open-link', url: link.href }, location.origin);
  });
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// ───────────────────────────── графики ─────────────────────────────

function barChart(container, title, values, color, describe) {
  container.replaceChildren();
  container.append(el('div', { class: 'title' }, title));
  const width = Math.max(280, container.clientWidth);
  const height = 80, top = 6, bottom = 18, left = 26;
  const plotH = height - top - bottom, plotW = width - left;
  const max = Math.max(1, ...values);
  const step = max <= 4 ? 1 : Math.ceil(max / 4);
  const yMax = Math.ceil(max / step) * step;
  const chart = svg('svg', { viewBox: `0 0 ${width} ${height}`, height, role: 'img', 'aria-label': title });
  for (let v = 0; v <= yMax; v += step) {
    const y = top + plotH - (v / yMax) * plotH;
    chart.append(svg('line', { class: v ? 'gridline' : 'baseline', x1: left, x2: width, y1: y, y2: y }));
    const label = svg('text', { class: 'axis', x: left - 6, y: y + 3.5, 'text-anchor': 'end' });
    label.textContent = v;
    chart.append(label);
  }
  const slot = plotW / values.length;
  const barW = Math.max(1, Math.min(18, slot - 2));
  const hover = svg('rect', { class: 'hover-col', y: top, height: plotH, width: slot, visibility: 'hidden' });
  chart.append(hover);
  values.forEach((value, i) => {
    const x = left + i * slot;
    if (value > 0) {
      const h = Math.max(2, (value / yMax) * plotH);
      const r = Math.min(4, barW / 2, h);
      const bx = x + (slot - barW) / 2, by = top + plotH - h;
      // Скругление только у вершины: столбик стоит на оси.
      chart.append(svg('path', {
        fill: color,
        d: `M${bx},${by + h}V${by + r}Q${bx},${by} ${bx + r},${by}H${bx + barW - r}Q${bx + barW},${by} ${bx + barW},${by + r}V${by + h}Z`,
      }));
    }
    const hit = svg('rect', { class: 'hit', x, y: top, width: slot, height: plotH });
    const text = () => `<b>${esc(bucketLabel(i))}</b>${describe(i, value)}`;
    const highlight = () => {
      hover.setAttribute('x', x); hover.setAttribute('visibility', 'visible');
      return () => hover.setAttribute('visibility', 'hidden');
    };
    hit.addEventListener('pointermove', (event) => {
      if (event.pointerType !== 'mouse') return;
      highlight();
      showTip(event, text());
    });
    hit.addEventListener('pointerleave', (event) => {
      if (event.pointerType !== 'mouse') return;
      hover.setAttribute('visibility', 'hidden'); hideTip();
    });
    hit.addEventListener('click', (event) => {
      if (touchInput) tapTip(event, `${title}:${i}`, text(), highlight);
    });
    chart.append(hit);
  });
  const ticks = Math.min(6, values.length);
  for (let t = 0; t < ticks; t++) {
    const i = Math.round((t * (values.length - 1)) / Math.max(1, ticks - 1));
    const date = new Date((data.since + i * data.bucket_s) * 1000);
    const label = svg('text', {
      class: 'axis', x: left + i * slot + slot / 2, y: height - 4,
      'text-anchor': t === 0 ? 'start' : t === ticks - 1 ? 'end' : 'middle',
    });
    label.textContent = data.range === '24h' ? timeFmt.format(date) : dateFmt.format(date);
    chart.append(label);
  }
  container.append(chart);
}

function renderCharts() {
  const css = getComputedStyle(document.documentElement);
  const { down, checked, reports } = data.overview;
  barChart($('#chart-down'), 'Недоступных серверов', down, css.getPropertyValue('--critical').trim(), (i, v) => {
    const hosts = data.hosts.filter((h) => h.bars[i] != null && h.bars[i] < 0.5).map(name);
    return `<div class="row">Недоступно<span class="num">${v} из ${checked[i]}</span></div>`
      + (hosts.length ? `<div class="row">${esc(hosts.slice(0, 6).join(', '))}${hosts.length > 6 ? '…' : ''}</div>` : '');
  });
  barChart($('#chart-reports'), 'Жалоб от пользователей', reports, css.getPropertyValue('--series-reports').trim(), (i, v) => {
    const hosts = data.hosts.filter((h) => h.report_bars[i]).map((h) => `${name(h)} (${h.report_bars[i]})`);
    return `<div class="row">Жалоб<span class="num">${v}</span></div>`
      + (hosts.length ? `<div class="row">${esc(hosts.slice(0, 6).join(', '))}</div>` : '');
  });
}

// ───────────────────────────── серверы ─────────────────────────────

function matches(host) {
  if (!query) return true;
  return (host.region || '').toLowerCase().includes(query);
}

const BLOCKED_TEXT = 'Сервер не отвечает на запросы с наших серверов, поэтому бот не может его проверить и работать с ним. Это не обязательно сбой: из других сетей сайт может открываться.';

function hostDetails(host) {
  const incidents = data.incidents.filter((i) => i.host === host.host).slice(0, 5);
  const versions = data.versions.filter((v) => v.host === host.host).slice(0, 5);
  const lastCheck = host.checked_at
    ? `${when(host.checked_at)}: ${host.error ? errorText(host.error) : `ответ за ${host.latency_ms} мс`}` : 'не проверялся';
  return el('div', { class: 'host-details' },
    host.account_checks ? el('div', { class: 'notice' },
      el('b', {}, host.diary_issues && host.state !== 'down' ? 'Проблемы с дневниками. ' : 'Проверки дневников. '),
      `За последние 30 минут: ${host.account_checks.good} успешных, ${host.account_checks.total - host.account_checks.good} с ошибкой. `,
      'Это обезличенные загрузки дневников ботом и в приложении. Неверный пароль и подтверждение входа не учитываются.') : '',
    host.error === 'maintenance' ? el('div', { class: 'notice' },
      el('b', {}, 'Технические работы. '), 'Сервер показывает объявление о техническом обслуживании. Срок завершения неизвестен.')
      : availability(host).state === 'down' ? el('div', { class: 'notice' },
        'Сервер временно недоступен. Возможны технические работы или сбой; причина не подтверждена.') : '',
    host.state === 'blocked' ? el('p', { class: 'muted' }, BLOCKED_TEXT) : '',
    host.notice ? el('div', { class: 'notice' },
      el('b', {}, host.app_disabled ? 'ИРТех: регион отключён в приложении NetSchool. ' : 'ИРТех: '), host.notice) : '',
    host.latest_check?.mobile_error ? el('p', { class: 'notice' }, `Мобильное API: ${errorText(host.latest_check.mobile_error)}.`) : '',
    el('dl', { class: 'facts' },
      el('dt', {}, 'Адрес'), el('dd', {}, el('a', { href: host.url, target: '_blank', rel: 'noopener' }, host.url)),
      el('dt', {}, 'Версия'), el('dd', {}, host.version ? [el('code', {}, host.version), host.version_changed_at ? ` · с ${when(host.version_changed_at)}` : ''] : 'неизвестна'),
      el('dt', {}, 'Мобильное API'), el('dd', {}, mobileText(host)),
      el('dt', {}, 'Отклик'), el('dd', {}, host.avg_latency_ms == null ? '—' : `${(host.avg_latency_ms / 1000).toFixed(1)} с в среднем за сутки`),
      el('dt', {}, 'Проверка'), el('dd', {}, lastCheck),
      el('dt', {}, 'Последний ответ'), el('dd', {}, when(host.last_ok_at)),
      el('dt', {}, 'Успешных проверок'), el('dd', {}, `${pct(host.uptime)} за выбранный период`),
      host.reports_24h ? [el('dt', {}, 'Жалобы'), el('dd', {}, `${host.reports_24h} за сутки`)] : '',
    ),
    incidents.length ? [el('h3', {}, 'Сбои за период'), el('ul', {}, incidents.map((i) => el('li', {},
      `${when(i.start)} — ${i.ongoing ? 'продолжается' : `~${duration(i.end - i.start)}`}`,
      i.error ? `, ${errorText(i.error)}` : '')))] : '',
    versions.length > 1 || versions[0]?.previous ? [el('h3', {}, 'Обновления'), el('ul', {}, versions.map((v) => el('li', {},
      `${when(v.changed_at)}: `, el('code', {}, v.version), v.previous ? el('span', { class: 'muted' }, ` (была ${v.previous})`) : '')))] : '',
    el('button', { class: 'small', type: 'button', onclick: () => openReport(host.host) }, 'Сообщить о сбое'),
  );
}

function mobileText(host) {
  if (!host.in_app) return 'региона нет в приложении NetSchool';
  if (host.app_disabled) return 'регион отключён в приложении';
  if (host.mobile == null) return 'нет данных';
  return `${host.mobile ? 'отвечает' : 'не отвечает'}${host.mobile_uptime != null ? ` · ${pct(host.mobile_uptime)} за период` : ''}`;
}

function hostRow(host) {
  const state = availability(host);
  const blocked = host.state === 'blocked';
  const strip = el('div', { class: `strip${blocked ? ' blocked' : host.bars.length > 60 ? ' dense' : ''}`, role: 'img',
    'aria-label': blocked ? `${name(host)}: пока нет истории проверок` : `Доступность ${name(host)}: ${pct(host.uptime)}` });
  if (blocked) {
    strip.append(el('span', {}, 'пока нет истории проверок'));
  } else {
    const cellTip = (i) => {
      const share = host.bars[i], reports = host.report_bars[i];
      return `<b>${esc(bucketLabel(i))}</b>`
        + `<div class="row"><i class="sw ${tone(share)}"></i>${share == null ? 'Нет проверок' : 'Отвечал'}`
        + `${share == null ? '' : `<span class="num">${Math.round(share * 1000) / 10}%</span>`}</div>`
        + (reports ? `<div class="row">Жалоб<span class="num">${reports}</span></div>` : '');
    };
    host.bars.forEach((share, i) => {
      const cell = el('div', { class: `cell ${tone(share)}${host.report_bars[i] ? ' has-reports' : ''}` });
      cell.addEventListener('pointermove', (event) => { if (event.pointerType === 'mouse') showTip(event, cellTip(i)); });
      cell.addEventListener('pointerleave', (event) => { if (event.pointerType === 'mouse') hideTip(); });
      strip.append(cell);
    });
    // Пальцем ячейки в пару пикселей не попасть — берём ближайшую по X,
    // включая зазоры. Касание полоски показывает данные, а не раскрывает регион.
    strip.addEventListener('click', (event) => {
      if (!touchInput) return;
      const box = strip.getBoundingClientRect();
      const count = host.bars.length;
      const i = Math.min(count - 1, Math.max(0, Math.floor(((event.clientX - box.left) / box.width) * count)));
      const cell = strip.children[i];
      tapTip(event, `${host.host}:${i}`, cellTip(i), () => {
        cell.classList.add('active');
        return () => cell.classList.remove('active');
      });
    });
  }
  const isOpen = expanded.has(host.host);
  const sub = [];
  if (host.error === 'maintenance') sub.push('технические работы');
  else if (state.state === 'down' && host.down_since) sub.push(`текущий сбой ~${duration(Date.now() / 1000 - host.down_since)}`);
  else if (host.version) sub.push(host.version);
  if (host.app_disabled) sub.push('отключён в приложении');
  const toggle = () => {
    if (expanded.has(host.host)) expanded.delete(host.host); else expanded.add(host.host);
    renderHosts();
  };
  return el('div', { class: `host${isOpen ? ' open' : ''}`, 'data-host': host.host },
    el('button', { class: 'host-row', type: 'button', 'aria-expanded': String(isOpen), onclick: toggle },
      el('span', { class: 'host-name' },
        el('span', { class: 'region' }, el('i', { class: `dot ${state.tone}`, 'aria-hidden': 'true' }), name(host)),
        el('span', { class: 'addr' }, sub.join(' · '))),
      el('span', { class: 'state-label' }, state.label),
      strip,
      el('span', { class: 'num', title: 'Доля успешных проверок за выбранный период' }, blocked ? '—' : pct(host.uptime)),
      el('span', { class: 'chevron', 'aria-hidden': 'true' }, isOpen ? '▴' : '▾'),
    ),
    el('a', { class: 'region-url', href: host.url, target: '_blank', rel: 'noopener' }, host.url.replace(/^https?:\/\//, '')),
    isOpen ? hostDetails(host) : '',
  );
}

function renderHosts() {
  // Недоступные с наших серверов — в самом конце: о самом регионе они ничего не говорят.
  const order = { down: 0, issues: 1, up: 2, unknown: 3, blocked: 4 };
  const hosts = data.hosts.filter(matches)
    .sort((a, b) => (order[availability(a).state] - order[availability(b).state]) || name(a).localeCompare(name(b), 'ru'));
  $('#hosts').replaceChildren(...(hosts.length ? hosts.map(hostRow) : [el('div', { class: 'muted' }, 'Ничего не нашлось')]));
  $('#strip-from').textContent = data.range === '24h' ? '24 ч назад' : data.range === '7d' ? '7 дней назад' : '30 дней назад';
}

// ───────────────────────────── списки ─────────────────────────────

let tab = 'incidents';
const LIMIT = 5;

function list(container, items, render, empty) {
  const shown = container.dataset.all ? items : items.slice(0, LIMIT);
  const children = shown.length ? shown.map(render) : [el('div', { class: 'empty' }, empty)];
  if (items.length > LIMIT) {
    children.push(el('button', { class: 'small more', type: 'button', onclick: () => {
      container.dataset.all = container.dataset.all ? '' : '1';
      list(container, items, render, empty);
    } }, container.dataset.all ? 'Свернуть' : `Показать все (${items.length})`));
  }
  container.replaceChildren(...children);
}

function renderLists() {
  const container = $('#feed');
  document.querySelectorAll('#feed-tabs button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.tab === tab)));
  if (tab === 'incidents') {
    list(container, data.incidents.filter((i) => matches(i)), (i) => el('div', { class: 'item' },
      el('i', { class: `dot ${i.ongoing ? 'critical' : 'none'}`, 'aria-hidden': 'true' }),
      el('div', {},
        el('div', {}, el('b', {}, name(i)), i.ongoing
          ? ` — недоступен уже ${duration(Date.now() / 1000 - i.start)}`
          : ` — не отвечал ~${duration(i.end - i.start)}`),
        el('div', { class: 'when' }, `${when(i.start)}${i.error ? ` · ${errorText(i.error)}` : ''}`))),
      'За этот период сбоев не было');
  } else if (tab === 'versions') {
    // «Впервые замечена» — не обновление: это просто начало наблюдений.
    list(container, data.versions.filter((v) => v.previous && matches(v)), (v) => el('div', { class: 'item' },
      el('i', { class: 'dot accent', 'aria-hidden': 'true' }),
      el('div', {},
        el('div', {}, el('b', {}, name(v)), ': ', el('code', {}, v.previous), ' → ', el('code', {}, v.version)),
        el('div', { class: 'when' }, when(v.changed_at)))),
      'Обновлений пока не было');
  } else {
    list(container, data.reports.filter((r) => matches(r)), (r) => el('div', { class: 'item' },
      el('i', { class: 'dot accent', 'aria-hidden': 'true' }),
      el('div', {},
        el('div', {}, el('b', {}, name(r)), ` — ${data.kinds[r.kind] || r.kind}`),
        el('div', { class: 'when' }, ago(r.created_at)))),
      'Жалоб не было');
  }
}

document.querySelectorAll('#feed-tabs button').forEach((button) => button.addEventListener('click', () => {
  tab = button.dataset.tab;
  delete $('#feed').dataset.all;
  if (data) renderLists();
}));

function renderBanner() {
  const down = data.hosts.filter((h) => availability(h).state === 'down');
  const issues = data.hosts.filter((h) => availability(h).state === 'issues');
  const checked = data.hosts.filter((h) => !['unknown', 'blocked'].includes(availability(h).state));
  const banner = $('#banner');
  let cls = 'good', icon = '✓', title = 'Серверы доступны', sub = '';
  if (down.length) {
    cls = 'critical'; icon = '!';
    title = `${down.length} ${plural(down.length, 'сервер недоступен', 'сервера недоступны', 'серверов недоступны')}`;
    sub = down.map(name).join(', ');
  } else if (issues.length) {
    cls = 'warning'; icon = '!';
    title = `${issues.length} ${plural(issues.length, 'регион с проблемами', 'региона с проблемами', 'регионов с проблемами')}`;
    sub = `Сайт открывается, но дневники не загружаются: ${issues.map(name).join(', ')}`;
  } else if (!checked.length) {
    cls = ''; icon = '…'; title = 'Проверяем серверы';
    sub = 'Результаты появятся после проверки.';
  } else {
    sub = `${checked.length} ${plural(checked.length, 'регион доступен', 'региона доступны', 'регионов доступны')}`;
  }
  banner.className = `banner ${cls}`;
  banner.querySelector('.banner-icon').textContent = icon;
  $('#banner-title').textContent = title;
  $('#banner-sub').textContent = sub;
  $('#updated').textContent = when(data.generated_at);
}

function render() {
  if (!data) return;
  renderBanner();
  renderCharts();
  renderHosts();
  renderLists();
}

// ───────────────────────────── данные ─────────────────────────────

let loading = false, loadAgain = false, connected = false;
async function load() {
  if (loading) { loadAgain = true; return; }
  loading = true;
  const requestedRange = range;
  $('#refresh-status').disabled = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`${base}/status/data?range=${requestedRange}`, { cache: 'no-store', signal: controller.signal });
    if (!response.ok) throw new Error(response.status);
    const result = await response.json();
    if (requestedRange !== range) { loadAgain = true; return; }
    data = result;
    render();
  } catch (error) {
    if (!data) {
      $('#banner-title').textContent = 'Не удалось загрузить статус';
      $('#banner-sub').textContent = 'Обновите страницу через минуту.';
    }
  } finally {
    clearTimeout(timeout);
    loading = false;
    $('#refresh-status').disabled = false;
    if (loadAgain) { loadAgain = false; load(); }
  }
}
$('#refresh-status').addEventListener('click', load);

document.querySelectorAll('#range button').forEach((button) => {
  button.setAttribute('aria-pressed', String(button.dataset.range === range));
  button.addEventListener('click', () => {
    range = button.dataset.range;
    try { localStorage.setItem('status-range', range); } catch { /* приватный режим */ }
    document.querySelectorAll('#range button').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
    load();
  });
});

$('#search').addEventListener('input', (event) => {
  query = event.target.value.trim().toLowerCase();
  if (data) { renderHosts(); renderLists(); }
});

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => data && renderCharts(), 150);
});
window.addEventListener('scroll', hideTip, { passive: true });

// ───────────────────────────── жалоба ─────────────────────────────

const dialog = $('#report-dialog');
const form = $('#report-form');

function openReport(host) {
  if (!data) return;
  const select = $('#report-host');
  const hosts = [...data.hosts].sort((a, b) => name(a).localeCompare(name(b), 'ru'));
  select.replaceChildren(el('option', { value: '' }, 'Выберите регион'),
    ...hosts.map((h) => el('option', { value: h.host }, name(h))));
  select.value = host || '';
  const kinds = $('#report-kinds');
  kinds.replaceChildren(...Object.entries(data.kinds).map(([value, label], i) =>
    el('label', {}, el('input', { type: 'radio', name: 'kind', value, checked: i === 0 }), label)));
  form.comment.value = '';
  form.website.value = '';
  const error = $('#report-error');
  error.textContent = ''; error.className = 'form-error';
  form.querySelector('[type=submit]').disabled = false;
  dialog.showModal();
}

document.querySelectorAll('[data-report]').forEach((b) => b.addEventListener('click', () => openReport('')));
dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const error = $('#report-error');
  error.className = 'form-error';
  if (!form.host.value) { error.textContent = 'Выберите сервер'; return; }
  const submit = form.querySelector('[type=submit]');
  submit.disabled = true;
  try {
    const response = await fetch(`${base}/status/report`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        host: form.host.value, kind: form.kind.value, comment: form.comment.value, website: form.website.value,
      }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.ok) {
      error.textContent = result.error || 'Не получилось отправить — попробуйте позже';
      submit.disabled = false;
      return;
    }
    error.className = 'form-error ok';
    error.textContent = 'Спасибо! Жалоба принята.';
    setTimeout(() => { dialog.close(); load(); }, 1200);
  } catch {
    error.textContent = 'Нет связи — попробуйте ещё раз';
    submit.disabled = false;
  }
});

load();
const stopLive = watchStatus(base, load, (online) => {
  connected = online;
  $('#live-status').textContent = online ? 'В реальном времени' : 'Переподключаемся…';
});
// Запасной путь для сетей, где WebSocket закрыт.
setInterval(() => { if (!document.hidden && !connected) load(); }, 30000);
window.addEventListener('pagehide', (event) => { if (!event.persisted) stopLive(); });
