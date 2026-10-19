// Изменения без сети: отметки «выполнено», свои задачи, настройки.
//
// Без связи с сервером изменение сразу видно на экране и ложится в очередь
// на этом устройстве (localStorage) — она переживает и закрытие приложения.
// Появилась сеть — очередь уходит на сервер по порядку, а оттуда, как обычно,
// на другие устройства и в бота. Одно и то же (отметка одного задания, одна
// задача) хранится одной записью: последнее изменение заменяет прежнее.
'use strict';

import { api, banner, demo, isOffline } from './core.js';

const KEY = 'ns-outbox';
const RETRY_MS = 30000;

function read() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || '[]');
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}

let queue = read();
// Своя задача, созданная без сети, живёт с временным отрицательным id, пока
// сервер не выдаст настоящий. Экран мог ещё держать временный — сверяем здесь.
const realIds = new Map();

function save() {
  try {
    if (queue.length) localStorage.setItem(KEY, JSON.stringify(queue));
    else localStorage.removeItem(KEY);
  } catch { /* хранилище недоступно — очередь продержится до перезагрузки */ }
  document.dispatchEvent(new CustomEvent('netschool:outbox', { detail: { count: queue.length } }));
}

/** Ждущее отправки изменение по ключу. */
export const pendingOp = (id) => queue.find((op) => op.id === id) || null;
/** Все ждущие с ключом, начинающимся с prefix (`hw:`, `note:`). */
export const pendingOps = (prefix) => queue.filter((op) => op.id.startsWith(prefix));

export const realId = (id) => realIds.get(id) ?? id;

const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
let inFlight = null;

/** base — какими были на сервере меняемые поля до первого изменения. Вернули
 *  всё как было (отметили и тут же сняли) — менять на сервере нечего, и
 *  изменение из очереди уходит, а не считается ждущим. */
export function enqueue(id, path, body, base = null) {
  const prev = pendingOp(id);
  // Прежнее уже в пути: после него сервер будет другим, сравнивать не с чем.
  const origin = inFlight?.id === id ? null : { ...base, ...prev?.base };
  if (origin && Object.keys(origin).length && Object.keys(origin).every((key) => same(body[key], origin[key]))) {
    dequeue(id);
    return;
  }
  const v = (prev?.v || 0) + 1;
  queue = [...queue.filter((op) => op.id !== id), { id, path, body, v, ...(origin && Object.keys(origin).length ? { base: origin } : {}) }];
  save();
}

export function dequeue(id) {
  queue = queue.filter((op) => op.id !== id);
  save();
}

/** До сервера не достучались — повторим, когда появится сеть. */
const unreachable = (error) => error?.source === 'app';
// Сервер ответил отказом по делу (задачу уже удалили на другом устройстве) —
// повторять бесполезно. С причиной (PIN, вход) — ждём, это пройдёт.
const rejected = (error) => !unreachable(error) && !error?.reason;

/** Отправить сразу, а без связи — в очередь. → { queued, data }. Ошибку
 *  сервера по существу (неверные данные) отдаёт как есть. */
export async function send(id, path, body, base = null) {
  if (!demo && (isOffline() || pendingOp(id))) {
    // Уже ждёт более раннее изменение того же — новое встаёт на его место.
    enqueue(id, path, body, base);
    if (!isOffline()) flush();
    return { queued: true, data: null };
  }
  try {
    const { data } = await api(path, { method: 'POST', body: JSON.stringify(body) });
    return { queued: false, data };
  } catch (error) {
    if (!unreachable(error)) throw error;
    enqueue(id, path, body, base);
    return { queued: true, data: null };
  }
}

let flushing = null;

/** Отправить очередь. Сразу после неудачи ничего не повторяет. */
export function flush() {
  if (demo || !queue.length || isOffline()) return Promise.resolve();
  // Через then: отправка, начатая изнутри отправки, ждёт текущую, а не идёт второй.
  flushing ||= Promise.resolve().then(drain).finally(() => { flushing = null; });
  return flushing;
}

async function drain() {
  let sent = 0;
  while (queue.length && !isOffline()) {
    const op = queue[0];
    let data;
    inFlight = op;
    try {
      ({ data } = await api(op.path, { method: 'POST', body: JSON.stringify(op.body) }));
    } catch (error) {
      if (!rejected(error)) break;
      if (pendingOp(op.id)?.v === op.v) dequeue(op.id);
      continue;
    } finally {
      inFlight = null;
    }
    sent += 1;
    delivered(op, data);
  }
  if (!sent) return;
  if (!queue.length) banner('Изменения, сделанные без сети, отправлены', false, 2500);
  document.dispatchEvent(new CustomEvent('netschool:outbox-sent'));
}

/** Изменение принято сервером: убрать из очереди. Пока оно шло, то же самое
 *  могли поменять ещё раз — тогда в очереди остаётся новое. */
function delivered(op, data) {
  const current = pendingOp(op.id);
  const created = op.path === '/api/notes' && data?.id;
  if (created) {
    const temp = Number(op.id.slice('note:'.length));
    realIds.set(temp, data.id);
    document.dispatchEvent(new CustomEvent('netschool:note-created', { detail: { temp, note: data } }));
  }
  if (current?.v === op.v) {
    dequeue(op.id);
    // «Выполнено» сервер при создании не принимает — отдельным изменением.
    if (created && op.body.done) enqueue(`note:${data.id}`, `/api/notes/${data.id}`, { done: true });
  } else if (current && created) {
    // Задачу поправили, пока она создавалась: правка — уже к настоящей.
    dequeue(op.id);
    enqueue(`note:${data.id}`, `/api/notes/${data.id}`, current.body);
  } else if (created) {
    // Удалили, пока она создавалась.
    enqueue(`note:${data.id}`, `/api/notes/${data.id}`, { delete: true });
  }
}

if (!demo) {
  document.addEventListener('netschool:network', ({ detail }) => { if (!detail.offline) flush(); });
  // Сеть была, а сервер не ответил (перезапуск) — попробуем ещё.
  setInterval(flush, RETRY_MS);
  // Число ждущих — общей плашке (core.js), как только она понадобится.
  setTimeout(() => {
    save();
    flush();
  }, 0);
}
