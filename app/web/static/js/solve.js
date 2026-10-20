// Кнопка «Решение» под домашним заданием и экран с решением (web/solver.py).
//
// Решение пишет модель по учебнику и файлам задания; приходит потоком, в
// Markdown с формулами в $…$. Формулы рисуем сами — дроби столбиком, корни,
// системы со скобкой, — как в тетради: внешние библиотеки из России
// грузятся ненадёжно.
'use strict';

import { $, api, banner, esc, state } from './core.js';
import { notify, tap } from './haptics.js';
import { ICONS } from './icons.js';
import { loadProfile } from './profile.js';
import { popScreen, pushScreen } from './screens.js';
import { textStream } from './text-stream.js';

// Модель пишет большое решение минуты две; поток ждём с запасом.
const STREAM_TIMEOUT_MS = 330000;
const WAITING = '<div class="solve-wait">Ищу в учебнике и решаю…</div>';

/** Место под кнопку: заполняется, когда известно, что решения доступны. */
export function solveSlot(item, index) {
  return `<div class="hw-solve" data-solve="${index}" hidden></div>`;
}

/** Показать кнопки «Решение» на только что открытом экране. */
export function loadSolve(body, items) {
  const fill = () => {
    if (!state.profile?.solver || state.profile?.readonly) return;
    body.querySelectorAll('[data-solve]').forEach((slot) => {
      const item = items[Number(slot.dataset.solve)];
      if (!item) return;
      slot.innerHTML = `<button class="solve-btn">${ICONS.sparkles}<span>Решение</span></button>`;
      slot.hidden = false;
      slot.querySelector('button').addEventListener('click', () => openSolution(item));
    });
  };
  if (state.profile) fill();
  else loadProfile().then(fill).catch(() => { /* без профиля кнопки просто нет */ });
}

// ───────────────────── решение в фоне и «пилюля» ─────────────────────
// Решение идёт, пока приложение открыто, даже если экран закрыли или ушли
// на другую вкладку: вместо экрана слева над панелью висит кружок, а когда
// решение готово, он раскрывается в «Решение готово». Нажатие — снова экран.
// Свернули само приложение и поток оборвался — сервер решает дальше и
// сохраняет решение; вернулись — переспрашиваем, оно придёт из базы.

const jobs = new Map();
let pill = null;

function jobKey(item) {
  return JSON.stringify([item.subject, item.date, item.text || '']);
}

function startJob(item, refresh = false) {
  const key = jobKey(item);
  const previous = jobs.get(key);
  const job = {
    key, item, text: previous?.text || '', partial: '', status: 'running', error: '',
    view: previous?.view || null, seen: false, hidden: document.visibilityState === 'hidden',
    resumed: Boolean(previous?.resumed), at: Date.now(),
  };
  jobs.set(key, job);
  // Пустой текст — сервер начал попытку заново (reset): снова «решаю».
  const live = textStream((partial) => { job.partial = partial; paint(job); });
  paint(job);
  syncPill();
  api('/api/solve', {
    method: 'POST',
    streamTimeout: STREAM_TIMEOUT_MS,
    onDelta: (partial) => live.push(partial),
    body: JSON.stringify({
      subject: item.subject, date: item.date, text: item.text || '', topic: item.topic || '',
      // Комментарий учителя («1 ряд — …»): строкой, если экран его уже
      // подгрузил, иначе сервер спросит сам по id задания.
      ...(typeof item.comment === 'string' ? { comment: item.comment } : {}),
      ...(item.id ? { id: item.id } : {}),
      attachments: (item.attachments || []).map(({ id, name }) => ({ id, name })),
      refresh,
    }),
  }).then(async ({ data }) => {
    await live.finish();
    job.text = data.text;
    job.status = 'done';
  }, (error) => {
    live.cancel();
    job.status = 'error';
    job.error = error.message;
    job.reason = error.reason;
  }).then(() => {
    if (jobs.get(key) !== job) return;
    // Обрыв, пока приложение было свёрнуто: сервер решение дописал —
    // переспрашиваем один раз, как только приложение снова на экране.
    if (job.status === 'error' && job.hidden && !job.resumed && !['limit', 'disabled', 'busy'].includes(job.reason)) {
      job.resumed = true;
      job.status = 'running';
      if (document.visibilityState === 'visible') startJob(item);
      else job.resume = true;
      return;
    }
    job.at = Date.now();
    job.seen = Boolean(job.view);
    notify(job.status === 'done' ? 'success' : 'error');
    paint(job);
    syncPill();
  });
  return job;
}

document.addEventListener('visibilitychange', () => {
  const visible = document.visibilityState === 'visible';
  for (const job of [...jobs.values()]) {
    if (!visible && job.status === 'running') job.hidden = true;
    if (visible && job.resume) {
      job.resume = false;
      startJob(job.item);
    }
  }
});

/** Нарисовать состояние решения на его экране, если экран открыт. */
function paint(job) {
  const view = job.view;
  if (!view) return;
  const running = job.status === 'running';
  if (running) view.out.innerHTML = job.partial ? renderMarkdown(job.partial) : WAITING;
  else if (job.status === 'done') view.out.innerHTML = renderMarkdown(job.text);
  else view.out.innerHTML = `<div class="empty">${esc(job.error)}</div>`;
  view.actions.hidden = running || !job.text;
  view.retry.hidden = job.status !== 'error' || Boolean(job.text);
  view.minimize.hidden = !running;
}

export function openSolution(item) {
  tap();
  const body = pushScreen('Решение', item.subject);
  body.innerHTML = `
    <div class="solve">
      <div class="solve-task">
        <div class="detail-label">Задание</div>
        <div class="detail-text">${esc(item.text || '—')}</div>
      </div>
      <div class="solve-out md" data-out>${WAITING}</div>
      <div class="solve-actions" data-actions hidden>
        <button class="btn outline" data-copy>Скопировать</button>
        <button class="btn outline" data-again>Решить заново</button>
      </div>
      <button class="btn outline" data-retry hidden>Попробовать ещё раз</button>
      <button class="solve-minimize" data-minimize hidden>Свернуть — решение продолжится</button>
      <div class="muted small solve-note">Решение пишет ИИ по учебнику — проверьте его, прежде чем сдавать.</div>
    </div>`;
  const view = {
    body,
    out: body.querySelector('[data-out]'),
    actions: body.querySelector('[data-actions]'),
    retry: body.querySelector('[data-retry]'),
    minimize: body.querySelector('[data-minimize]'),
  };
  let job = jobs.get(jobKey(item));
  // Ошибку, которую уже видели, при новом открытии пробуем заново.
  if (!job || (job.status === 'error' && job.seen)) job = startJob(item);
  job.view = view;
  job.seen = job.status !== 'running';
  paint(job);
  syncPill();

  const current = () => jobs.get(jobKey(item));
  body.querySelector('[data-copy]').addEventListener('click', () => {
    navigator.clipboard?.writeText(current().text).then(() => banner('Скопировано'), () => banner('Не удалось скопировать', true));
  });
  body.querySelector('[data-again]').addEventListener('click', () => {
    if (current().status !== 'running') startJob(item, true);
  });
  view.retry.addEventListener('click', () => {
    if (current().status !== 'running') startJob(item);
  });
  view.minimize.addEventListener('click', () => {
    tap('soft');
    popScreen();
  });
}

// Экран закрыли (назад, свайп, другая вкладка) — решение уходит в кружок.
function watchScreens() {
  const host = $('screens');
  if (!host) return;
  new MutationObserver(() => {
    let changed = false;
    for (const job of jobs.values()) {
      if (job.view && !job.view.body.isConnected) {
        job.view = null;
        changed = true;
      }
    }
    if (changed) syncPill();
  }).observe(host, { childList: true });
}

/** Кружок над панелью: решения без открытого экрана — идут или не просмотрены. */
function syncPill() {
  if (!pill) {
    if (!document.body) return;
    pill = document.createElement('button');
    pill.className = 'solve-pill';
    pill.hidden = true;
    pill.addEventListener('click', () => {
      const job = pill.job;
      if (job) openSolution(job.item);
    });
    document.body.appendChild(pill);
    watchScreens();
  }
  const waiting = [...jobs.values()].filter((job) => !job.view && (job.status === 'running' || !job.seen));
  if (!waiting.length) {
    pill.hidden = true;
    pill.job = null;
    return;
  }
  // Готовое важнее идущего; среди равных — самое свежее.
  const ready = waiting.filter((job) => job.status !== 'running');
  const job = (ready.length ? ready : waiting).sort((a, b) => b.at - a.at)[0];
  const more = waiting.length - 1;
  const state = job.status;
  pill.job = job;
  pill.dataset.state = state;
  pill.setAttribute('aria-label', state === 'running' ? `Решаю: ${job.item.subject}` : `Решение: ${job.item.subject}`);
  const icon = state === 'running' ? `<span class="solve-pill-spin"></span>${ICONS.sparkles}`
    : state === 'done' ? ICONS.check : '<b>!</b>';
  const label = state === 'running' ? ''
    : `<span class="solve-pill-text"><b>${state === 'done' ? 'Решение готово' : 'Не получилось решить'}</b>`
      + `<small>${esc(job.item.subject)}</small></span>`;
  pill.innerHTML = `<span class="solve-pill-icon">${icon}</span>${label}`
    + (more > 0 ? `<span class="solve-pill-more">+${more}</span>` : '');
  pill.hidden = false;
}

// ───────────────────────────── Markdown ─────────────────────────────

/** Строчная разметка: формулы, **жирный**, *курсив*, `код`. */
function inline(text) {
  return text.split(/(\$[^$\n]+\$)/g).map((part) => {
    if (part.length > 2 && part.startsWith('$') && part.endsWith('$')) {
      return `<span class="m">${renderMath(part.slice(1, -1))}</span>`;
    }
    return esc(part)
      .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
      .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<i>$2</i>')
      .replace(/`([^`\n]+)`/g, '<code>$1</code>');
  }).join('');
}

function tableRow(line) {
  return line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
}

export function renderMarkdown(source) {
  const lines = String(source || '').replace(/\r/g, '').split('\n');
  const html = [];
  let paragraph = [];
  let list = null;

  const flush = () => {
    if (paragraph.length) html.push(`<p>${paragraph.map(inline).join('<br>')}</p>`);
    paragraph = [];
    if (list) html.push(`<${list.tag}>${list.items.map((x) => `<li>${inline(x)}</li>`).join('')}</${list.tag}>`);
    list = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) { flush(); continue; }

    // $$ … $$ — формула отдельной строкой, может занимать несколько строк.
    if (trimmed.startsWith('$$')) {
      flush();
      let formula = trimmed.slice(2);
      if (formula.endsWith('$$') && formula.length >= 2) formula = formula.slice(0, -2);
      else {
        while (i + 1 < lines.length && !lines[i + 1].includes('$$')) formula += '\n' + lines[++i];
        if (i + 1 < lines.length) formula += '\n' + lines[++i].replace(/\$\$.*$/, '');
      }
      html.push(`<div class="m-block">${renderMath(formula.trim())}</div>`);
      continue;
    }
    const heading = trimmed.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flush();
      html.push(`<h${heading[1].length <= 2 ? 3 : 4}>${inline(heading[2])}</h${heading[1].length <= 2 ? 3 : 4}>`);
      continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(trimmed)) { flush(); html.push('<hr>'); continue; }
    if (trimmed.startsWith('|') && i + 1 < lines.length && /^\|?\s*:?-{2,}/.test(lines[i + 1].trim())) {
      flush();
      const head = tableRow(trimmed);
      i++;
      const rows = [];
      while (i + 1 < lines.length && lines[i + 1].trim().startsWith('|')) rows.push(tableRow(lines[++i]));
      html.push(`<div class="md-table"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead>`
        + `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
      continue;
    }
    const bullet = trimmed.match(/^[-*•]\s+(.*)$/);
    const numbered = trimmed.match(/^\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      const tag = bullet ? 'ul' : 'ol';
      if (paragraph.length || (list && list.tag !== tag)) flush();
      list ??= { tag, items: [] };
      list.items.push((bullet || numbered)[1]);
      continue;
    }
    if (list) flush();
    paragraph.push(trimmed);
  }
  flush();
  return html.join('');
}

// ───────────────────────────── формулы ─────────────────────────────

const SYMBOLS = {
  cdot: '·', times: '×', div: '÷', pm: '±', mp: '∓', le: '≤', leq: '≤', ge: '≥', geq: '≥', ne: '≠', neq: '≠',
  approx: '≈', sim: '∼', equiv: '≡', Rightarrow: '⇒', Leftrightarrow: '⇔', Leftarrow: '⇐', to: '→', rightarrow: '→',
  in: '∈', notin: '∉', subset: '⊂', cup: '∪', cap: '∩', infty: '∞', degree: '°', circ: '°', angle: '∠',
  triangle: '△', parallel: '∥', perp: '⊥', varnothing: '∅', emptyset: '∅', ldots: '…', dots: '…', cdots: '⋯',
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', Delta: 'Δ', varepsilon: 'ε', epsilon: 'ε', lambda: 'λ',
  mu: 'μ', nu: 'ν', pi: 'π', rho: 'ρ', sigma: 'σ', Sigma: 'Σ', tau: 'τ', varphi: 'φ', phi: 'φ', omega: 'ω',
  Omega: 'Ω', quad: ' ', qquad: '  ', ',': ' ', ';': ' ', ':': ' ', '!': '',
  '{': '{', '}': '}', '%': '%', '#': '#', '&': '&', '_': '_', ' ': ' ', '|': '‖', lbrace: '{', rbrace: '}',
  left: '', right: '', displaystyle: '', limits: '', log: 'log', ln: 'ln', lg: 'lg', sin: 'sin', cos: 'cos',
  tg: 'tg', ctg: 'ctg', tan: 'tg', cot: 'ctg', min: 'min', max: 'max', lim: 'lim', sum: '∑', prod: '∏',
  uparrow: '↑', downarrow: '↓', leftrightarrow: '↔', rightleftharpoons: '⇄', longrightarrow: '⟶',
};
const WORDS = new Set(['log', 'ln', 'lg', 'sin', 'cos', 'tg', 'ctg', 'tan', 'cot', 'min', 'max', 'lim']);

/** LaTeX-подмножество → HTML. Незнакомое показывается как есть, без слэша. */
export function renderMath(source) {
  let pos = 0;
  const src = String(source);

  function group() {
    // {…} или один символ/команда.
    while (src[pos] === ' ') pos++;
    if (src[pos] === '{') {
      pos++;
      const inner = sequence('}');
      pos++;
      return inner;
    }
    return atom();
  }

  function optional() {
    if (src[pos] !== '[') return '';
    pos++;
    const inner = sequence(']');
    pos++;
    return inner;
  }

  function command() {
    pos++; // «\»
    let name = '';
    if (/[A-Za-z]/.test(src[pos] || '')) while (/[A-Za-z]/.test(src[pos] || '')) name += src[pos++];
    else name = src[pos++] || '';
    switch (name) {
      case 'frac': case 'dfrac': case 'tfrac': {
        const num = group();
        const den = group();
        return `<span class="m-frac"><span class="m-num">${num}</span><span class="m-den">${den}</span></span>`;
      }
      case 'sqrt': {
        const index = optional();
        const under = group();
        return `<span class="m-sqrt">${index ? `<sup class="m-idx">${index}</sup>` : ''}<span class="m-rad">√</span><span class="m-under">${under}</span></span>`;
      }
      case 'text': case 'mathrm': case 'textrm': case 'operatorname': case 'mbox': {
        while (src[pos] === ' ') pos++;
        if (src[pos] !== '{') return '';
        const end = src.indexOf('}', pos);
        const raw = src.slice(pos + 1, end < 0 ? src.length : end);
        pos = end < 0 ? src.length : end + 1;
        return `<span class="m-text">${esc(raw)}</span>`;
      }
      case 'vec': return `<span class="m-vec">${group()}</span>`;
      case 'overline': case 'bar': return `<span class="m-over">${group()}</span>`;
      case 'mathbf': case 'textbf': case 'boldsymbol': return `<b>${group()}</b>`;
      case 'begin': {
        const env = src.slice(pos).match(/^\{(\w+\*?)\}/);
        if (!env) return '';
        pos += env[0].length;
        const close = `\\end{${env[1]}}`;
        const end = src.indexOf(close, pos);
        const raw = src.slice(pos, end < 0 ? src.length : end);
        pos = end < 0 ? src.length : end + close.length;
        const rows = raw.split(/\\\\/).map((row) => row.trim()).filter(Boolean)
          .map((row) => `<span class="m-row">${renderMath(row.replace(/&/g, ' '))}</span>`).join('');
        // cases — система со скобкой; aligned, array и прочее — просто строки.
        return env[1] === 'cases'
          ? `<span class="m-cases"><span class="m-brace">{</span><span class="m-rows">${rows}</span></span>`
          : `<span class="m-rows">${rows}</span>`;
      }
      case 'end': group(); return '';
      default:
        if (name in SYMBOLS) {
          const value = SYMBOLS[name];
          return WORDS.has(name) ? `<span class="m-fn">${value}</span>` : esc(value);
        }
        return esc(name);
    }
  }

  function atom() {
    const ch = src[pos];
    if (ch === undefined) return '';
    if (ch === '\\') return command();
    if (ch === '{') return group();
    // Минус между числами и скобками — с пробелами, как «+»; в начале и
    // после «(» или «=» — знак числа, без них.
    const before = src.slice(0, pos).trimEnd().slice(-1);
    pos++;
    if (/[A-Za-z]/.test(ch)) return `<i>${ch}</i>`;
    if (ch === '-') return /[\w)}\]]/.test(before) ? ' − ' : '−';
    if (ch === '*') return '·';
    if (ch === '~') return ' ';
    if (ch === '<' || ch === '>' || ch === '=' || ch === '+' || ch === '−') return ` ${esc(ch)} `;
    return esc(ch);
  }

  function sequence(stop) {
    let out = '';
    while (pos < src.length && src[pos] !== stop) {
      const ch = src[pos];
      if (ch === '^' || ch === '_') {
        pos++;
        const tag = ch === '^' ? 'sup' : 'sub';
        out += `<${tag}>${group()}</${tag}>`;
        continue;
      }
      // Лишняя «}» от модели не должна съедать остаток формулы.
      if (ch === '}') { pos++; continue; }
      out += atom();
    }
    return out;
  }

  return sequence(null);
}
