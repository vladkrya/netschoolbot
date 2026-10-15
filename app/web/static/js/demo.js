// Демо-режим: те же ответы, что у настоящего API, но выдуманные браузером.
//
// Данные детерминированы (зерно — дата и номер урока), поэтому при каждом
// открытии дневник тот же. Всё настраивается в меню ☰ → «Настройки демо»,
// настройки лежат в localStorage и сервер не трогают.
'use strict';

const KEY = 'ns-demo';

const SUBJECTS = [
  ['Русский язык', '12 кабинет русского языка', 'Васильковая Лира Эдуардовна'],
  ['Литература', '12 кабинет русского языка', 'Васильковая Лира Эдуардовна'],
  ['Алгебра', '4 кабинет математики', 'Циркулев Аркадий Гипотенузович'],
  ['Геометрия', '4 кабинет математики', 'Циркулев Аркадий Гипотенузович'],
  ['Физика', '21 кабинет физики', 'Ньютонова Ампера Вольтовна'],
  ['Химия', '18 кабинет химии', 'Колбочкина Реактива Титановна'],
  ['Английский язык', '9 кабинет', 'Грамматикс Дженни Олеговна'],
  ['История', '7 кабинет истории', 'Летописцев Хронос Архивович'],
  ['Биология', '16 кабинет биологии', 'Ромашкина Флора Тычинковна'],
  ['Информатика и ИКТ', '30 компьютерный класс', 'Байтов Кэш Процессорович'],
  ['Физическая культура', 'спортзал', 'Скакалкин Марафон Эстафетович'],
  ['География', '14 кабинет географии', 'Меридианова Карта Глобусовна'],
];

const TOPICS = {
  'Алгебра': ['Линейные уравнения', 'Неравенства', 'Квадратичная функция', 'Системы уравнений'],
  'Физика': ['Второй закон Ньютона', 'Третий закон Ньютона', 'Импульс тела', 'Работа и мощность'],
  'Русский язык': ['Союзы', 'Сложноподчинённое предложение', 'Причастный оборот'],
  'История': ['Реформы Петра I', 'Эпоха дворцовых переворотов', 'Отечественная война 1812 года'],
};

const TIMES = [
  ['08:00', '08:40'], ['08:55', '09:35'], ['09:50', '10:30'], ['10:45', '11:25'],
  ['11:40', '12:20'], ['12:25', '13:05'], ['13:20', '14:00'],
];

const DEFAULTS = {
  student: 'Пятёркин Отличник',
  school: 'Демо-школа №1',
  children: ['Пятёркин Отличник', 'Пятёркина Умница'],
  child: 0,
  lessons: 6,
  density: 35,
  absences: true,
  saturday: false,
  notifications: { telegram: true, grades: true, changes: true, deletes: true, homework: true, mail: false, weekly_summary: false },
  sent: [],
};

export function demoConfig() {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || '{}') };
  } catch {
    return { ...DEFAULTS };
  }
}

export function saveDemoConfig(patch) {
  const next = { ...demoConfig(), ...patch };
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* без хранилища демо живёт до перезагрузки */
  }
  return next;
}

export function resetDemoConfig() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* нечего сбрасывать */
  }
}

/* ────────────────────────── Генерация ────────────────────────── */

function seeded(text) {
  let h = 2166136261;
  for (const ch of text) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    return ((h ^= h >>> 16) >>> 0) / 4294967296;
  };
}

const pad = (n) => String(n).padStart(2, '0');
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parse = (s) => new Date(`${s}T00:00:00`);
const todayIso = () => iso(new Date());

function yearStart(today = new Date()) {
  const year = today.getMonth() >= 8 ? today.getFullYear() : today.getFullYear() - 1;
  return year;
}

function quarters() {
  const y = yearStart();
  return [
    { key: 'q1', label: '1 четверть', start: `${y}-09-01`, end: `${y}-10-31` },
    { key: 'q2', label: '2 четверть', start: `${y}-11-01`, end: `${y + 1}-01-08` },
    { key: 'q3', label: '3 четверть', start: `${y + 1}-01-09`, end: `${y + 1}-03-31` },
    { key: 'q4', label: '4 четверть', start: `${y + 1}-04-01`, end: `${y + 1}-08-31` },
  ];
}

function quarterOf(date) {
  return quarters().find((q) => q.start <= date && date <= q.end)?.key || null;
}

function buildDay(date, cfg) {
  const d = parse(date);
  const weekday = (d.getDay() + 6) % 7;
  const child = cfg.child || 0;
  if (weekday === 6 || (weekday === 5 && !cfg.saturday)) {
    return { date, label: '', weekday, lessons: [], marks: [], homework: [] };
  }
  const rand = seeded(`${date}:${child}`);
  const past = date < todayIso();
  // Пропуск по болезни — целый день, как бывает на самом деле.
  const sick = cfg.absences && past && weekday === 0 && rand() < 0.35;
  const count = Math.max(3, Math.min(7, Number(cfg.lessons) || 6)) - (rand() < 0.3 ? 1 : 0);

  const lessons = [];
  const offset = Math.floor(rand() * SUBJECTS.length);
  for (let n = 0; n < count; n += 1) {
    const [subject, room, teacher] = SUBJECTS[(offset + n * 5 + weekday) % SUBJECTS.length];
    const topics = TOPICS[subject];
    const topic = topics ? topics[Math.floor(rand() * topics.length)] : '';
    const marks = [];
    if (past) {
      if (sick) {
        marks.push(mark(subject, date, 'Посещаемость', '', 'Б', 1));
      } else if (rand() * 100 < cfg.density) {
        const value = pick(rand, [5, 5, 5, 4, 4, 3, 2]);
        marks.push(mark(subject, date, pick(rand, ['Ответ на уроке', 'Самостоятельная работа', 'Контрольная работа']), topic, String(value), 1 + Math.floor(rand() * 3)));
      }
    }
    const homework = rand() < 0.6
      ? [{
          subject,
          date,
          type: 'Домашнее задание',
          text: pick(rand, ['Упр. 23 стр. 27', '§7, стр. 26-28', '№795, №798, №799', 'Выучить записи в тетради', 'Подготовить сообщение']),
          attachments: rand() < 0.2 ? [{ id: 1, name: 'Задание.docx' }] : [],
          id: Math.floor(rand() * 1e6),
          comment: rand() < 0.3 ? 'Решать в тетради для домашних работ, с подробным решением.' : '',
        }].map((h) => ({ ...h, key: demoKey(`${h.date}|${h.subject}|${h.text}`) }))
      : [];
    lessons.push({
      number: n + 1,
      subject,
      time: `${TIMES[n][0]}–${TIMES[n][1]}`,
      start: TIMES[n][0],
      end: TIMES[n][1],
      room: rand() < 0.85 ? room : '',
      teacher,
      topic: past || date === todayIso() ? topic : '',
      marks,
      homework,
    });
  }
  return {
    date,
    label: '',
    weekday,
    lessons,
    marks: lessons.flatMap((l) => l.marks),
    homework: lessons.flatMap((l) => l.homework),
  };
}

function mark(subject, date, type, topic, value, weight) {
  const numeric = Number(value);
  return {
    subject,
    title: type,
    // Как у настоящего API: у работы на уроке названия обычно нет, тема — урока.
    topic: '',
    lesson_topic: topic,
    type,
    date,
    mark: value,
    weight,
    numeric: Number.isFinite(numeric) ? numeric : null,
    quarter: quarterOf(date),
  };
}

function pick(rand, items) {
  return items[Math.floor(rand() * items.length)];
}

function daysBetween(start, end, cfg) {
  const out = [];
  for (let d = parse(start); iso(d) <= end; d.setDate(d.getDate() + 1)) out.push(buildDay(iso(d), cfg));
  return out;
}

function weightedAverage(marks) {
  let total = 0;
  let weights = 0;
  for (const m of marks) {
    if (!Number.isFinite(m.numeric)) continue;
    total += m.numeric * m.weight;
    weights += m.weight;
  }
  return weights ? total / weights : null;
}

/* ─────────────────────────── Ответы API ─────────────────────────── */

const ok = (data) => ({ ok: true, data, stale: false });

function mondayOffset(weeks) {
  const d = new Date();
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7) + weeks * 7);
  return d;
}

const MAIL = [
  { id: 1, subject: 'Родительское собрание в четверг', author: 'Звонкова Перемена Уроковна', text: 'Уважаемые родители!\nСобрание состоится в четверг в 18:00 в кабинете 12.', days: 1, read: false },
  { id: 2, subject: 'Сроки сдачи проектов', author: 'Дедлайнова Проекта Сроковна', text: 'Проекты по информатике принимаются до конца месяца.', days: 7, read: false },
  { id: 3, subject: 'Экскурсия в музей', author: 'Летописцев Хронос Архивович', text: 'В субботу идём в краеведческий музей, сбор у школы в 10:00.', days: 15, read: true },
];

/** Стабильный ключ задания в демо: на сервере это отпечаток записи. */
function demoKey(text) {
  let hash = 0;
  for (const char of text) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash.toString(16).padStart(8, '0').repeat(2);
}

const demoNotes = [];
const demoAnswers = {};

export async function demoApi(path, options = {}) {
  // Небольшая задержка — чтобы в демо были видны анимации загрузки.
  await new Promise((resolve) => setTimeout(resolve, 250));
  const cfg = demoConfig();
  const url = new URL(path, 'https://demo.local');
  const route = url.pathname;
  const body = options.body ? JSON.parse(options.body) : {};

  if (route === '/api/profile') {
    const students = cfg.children.length ? cfg.children : [cfg.student];
    return ok({
      name: students[cfg.child] || cfg.student,
      school: cfg.school,
      students: students.map((name, id) => ({ id, name, current: id === cfg.child })),
      notifications: cfg.notifications,
      interval_minutes: 5,
      quiet_hours: 'Отключены',
      enabled: true,
      timezone: 'Europe/Moscow',
      timezones: [{ id: 'Europe/Moscow', name: 'Москва' }],
      status: { running: true, last_ok_at: new Date().toISOString(), queued: 0 },
    });
  }

  if (route === '/api/diary') {
    const week = Number(url.searchParams.get('week') || 0);
    const start = mondayOffset(week - 1);
    const end = mondayOffset(week + 2);
    end.setDate(end.getDate() - 1);
    return ok({ days: daysBetween(iso(start), iso(end), cfg), week });
  }

  if (route === '/api/marks') {
    const qs = quarters();
    const now = todayIso();
    const current = qs.find((q) => q.start <= now && now <= q.end) || qs[0];
    const days = daysBetween(qs[0].start, current.end, cfg);
    const bySubject = new Map();
    for (const day of days) {
      for (const lesson of day.lessons) {
        const entry = bySubject.get(lesson.subject) || { subject: lesson.subject, marks: [], lessons: {}, teacher: lesson.teacher };
        const key = quarterOf(day.date) || '';
        entry.lessons[key] = entry.lessons[key] || { total: 0, done: 0 };
        entry.lessons[key].total += 1;
        if (day.date <= now) entry.lessons[key].done += 1;
        entry.marks.push(...lesson.marks);
        bySubject.set(lesson.subject, entry);
      }
    }
    const subjects = [...bySubject.values()]
      .sort((a, b) => a.subject.localeCompare(b.subject, 'ru'))
      .map((s) => ({ ...s, average: weightedAverage(s.marks), count: s.marks.length }));
    return ok({
      subjects,
      average: weightedAverage(subjects.flatMap((s) => s.marks)),
      current_quarter: current.key,
      quarters: qs,
      year: `${yearStart()}/${yearStart() + 1}`,
    });
  }

  if (route === '/api/homework/done') {
    if (options.method === 'POST') {
      const done = new Set(cfg.done || []);
      if (body.done) done.add(body.key);
      else done.delete(body.key);
      saveDemoConfig({ done: [...done] });
      return ok({ key: body.key, done: Boolean(body.done) });
    }
    return ok({ keys: cfg.done || [] });
  }

  if (route === '/api/homework') {
    const past = url.searchParams.get('past') === '1';
    const start = past ? mondayOffset(-4) : mondayOffset(0);
    const end = past ? new Date() : mondayOffset(3);
    const items = daysBetween(iso(start), iso(end), cfg)
      .flatMap((d) => d.homework)
      .map((h) => ({ ...h, label: '', key: h.key || demoKey(`${h.date}|${h.subject}|${h.text}`) }));
    return ok({ items });
  }

  if (/^\/api\/homework\/\d+\/comment$/.test(route)) {
    // Комментарий есть не у каждого задания — как в жизни.
    const id = Number(route.split('/')[3]);
    return ok({ comment: id % 2 ? 'Решать в тетради для домашних работ, с подробным решением.' : '' });
  }

  if (route === '/api/mail') {
    const messages = MAIL.map((m) => {
      const sent = new Date();
      sent.setDate(sent.getDate() - m.days);
      return { id: m.id, subject: m.subject, author: m.author, sent: iso(sent), read: m.read };
    });
    return ok({ messages });
  }

  if (route === '/api/mail/recipients') {
    return ok({
      items: SUBJECTS.filter((s, i, all) => all.findIndex((x) => x[2] === s[2]) === i).map(([subject, , teacher], id) => ({
        id: String(id),
        name: teacher,
        organization: `Учитель: ${subject}`,
      })),
    });
  }

  if (route === '/api/mail/send') {
    saveDemoConfig({ sent: [...cfg.sent, { ...body, at: Date.now() }] });
    return { ok: true };
  }

  const mailMatch = route.match(/^\/api\/mail\/(\d+)$/);
  if (mailMatch) {
    const message = MAIL.find((m) => String(m.id) === mailMatch[1]) || MAIL[0];
    message.read = true;
    const sent = new Date();
    sent.setDate(sent.getDate() - message.days);
    return ok({ ...message, sent: iso(sent), attachments: [] });
  }

  if (route === '/api/announcements') {
    return ok({
      items: [
        {
          title: 'Осторожно: мошенники',
          text: 'Мошенники присылают «видеоинструкции от Банка России». Не переходите по ссылкам из незнакомых чатов и никому не сообщайте коды из SMS.',
          author: 'Администрация школы',
          posted: iso(mondayOffset(-1)),
          attachments: [],
        },
        {
          title: 'Каникулы',
          text: 'Осенние каникулы продлятся с 26 октября по 4 ноября.',
          author: 'Директор',
          posted: iso(mondayOffset(-3)),
          attachments: [],
        },
      ],
    });
  }

  if (route === '/api/settings/notifications') {
    const notifications = { ...cfg.notifications, [body.key]: Boolean(body.value) };
    saveDemoConfig({ notifications });
    return ok(notifications);
  }

  if (route === '/api/settings/reminder') {
    const notifications = { ...cfg.notifications, reminder_hour: Number(body.hour) || 18 };
    saveDemoConfig({ notifications });
    return ok(notifications);
  }

  if (route === '/api/settings/marks-filter') {
    const values = (body.values || []).length === 4 ? [] : body.values || [];
    const notifications = { ...cfg.notifications, marks_filter: values };
    saveDemoConfig({ notifications });
    return ok(notifications);
  }

  // Свои задачи в демо живут до перезагрузки страницы.
  if (route === '/api/notes') {
    if (options.method === 'POST') {
      const note = { id: demoNotes.length + 1, date: body.date, subject: body.subject || '', text: body.text, done: false };
      demoNotes.push(note);
      return ok(note);
    }
    return ok({ items: demoNotes.filter((note) => !note.deleted) });
  }
  const noteMatch = route.match(/^\/api\/notes\/(\d+)$/);
  if (noteMatch) {
    const note = demoNotes.find((item) => item.id === Number(noteMatch[1]));
    if (note && body.delete) note.deleted = true;
    else if (note && 'done' in body) note.done = Boolean(body.done);
    return ok({});
  }

  if (route === '/api/student') {
    saveDemoConfig({ child: Number(body.id) || 0 });
    return ok({ name: cfg.children[Number(body.id)] || cfg.student });
  }

  if (route === '/api/history') {
    const items = daysBetween(iso(mondayOffset(-1)), todayIso(), cfg)
      .flatMap((d) => d.lessons.flatMap((l) => l.marks.map((m) => ({ ...m, subject: l.subject, date: d.date }))))
      .slice(-15)
      .reverse()
      .map((m, id) => ({
        // Для показа — и исправленные, и удалённые, не только новые.
        id, kind: id % 5 === 2 ? 'changed' : id % 7 === 4 ? 'deleted' : 'new',
        subject: m.subject, date: m.date, title: m.title || m.type || '',
        mark: m.mark ?? m.value ?? '', old: '3', new: m.mark ?? m.value ?? '', seen: `${m.date}T15:00:00Z`,
      }));
    return ok({ items });
  }

  if (route === '/api/bells') {
    // Звонки школы: обычная сетка Пн–Пт и короткая суббота.
    const times = [['08:30', '09:10'], ['09:20', '10:00'], ['10:15', '10:55'], ['11:10', '11:50'], ['12:00', '12:40'], ['12:50', '13:30']];
    const items = [];
    for (let weekday = 1; weekday <= 6; weekday += 1) {
      times.slice(0, weekday === 6 ? 4 : 6).forEach(([start, end], index) =>
        items.push({ weekday, shift: 1, number: index + 1, start, end, used: true }));
    }
    return ok({ items });
  }

  const answer = route.match(/^\/api\/homework\/(\d+)\/answer(\/text)?$/);
  if (answer) {
    demoAnswers[answer[1]] ||= { text: '', files: [] };
    if (answer[2]) demoAnswers[answer[1]].text = String(body.text || '').trim();
    return ok({ available: true, ...demoAnswers[answer[1]] });
  }

  if (route === '/api/pwa-link') {
    return ok({ url: location.href });
  }

  if (route.startsWith('/api/chat')) return demoChat(route, url, options, body);

  const error = new Error('В демо это недоступно');
  error.reason = 'demo';
  throw error;
}

/* ───────── Чаты в демо: всё в памяти, сервер не участвует ───────── */

const DEMO_ROOMS = [
  { id: 'grade:demo:9', kind: 'grade', title: '9-е классы · школа №1', subtitle: 'Демо-школа №1' },
  { id: 'school:demo', kind: 'school', title: 'Школа №1', subtitle: 'Вся школа' },
  { id: 'global:6-11', kind: 'global', title: 'Общий чат', subtitle: 'Все школы · 6–11 классы' },
];
const demoChatState = { joined: false, nextId: 1, messages: {} };

function demoChatSeed() {
  if (demoChatState.seeded) return;
  demoChatState.seeded = true;
  const now = Date.now() / 1000;
  const add = (room, author, grade, text, ago, extra = {}) => {
    const id = demoChatState.nextId++;
    (demoChatState.messages[room] ||= []).push({
      id, author, grade, admin: false, mine: false, text, deleted: false, hidden: false, pinned: false,
      at: now - ago, reply: null, reactions: {}, my_reactions: [], ...extra,
    });
  };
  add('grade:demo:9', 'Маша', 9, 'Кто понял, что задали по алгебре?', 3600);
  add('grade:demo:9', 'Артём', 9, '№272, №274 и №277, на завтра', 3400, { reactions: { '👍': 3 } });
  add('grade:demo:9', 'Маша', 9, 'Спасибо!', 3300);
  add('grade:demo:9', 'Даня', 9, 'В пятницу экскурсия у всех 9-х?', 7200);
  add('global:6-11', 'Лиза', 8, 'Всем привет! Кто готовится к ОГЭ по биологии?', 600);
}

function demoChat(route, url, options, body) {
  demoChatSeed();
  const overview = () => ok({
    joined: demoChatState.joined, admin: false, as_admin: false, moderator: false, reports: 0,
    reactions: ['👍', '❤️', '😂', '😮', '😢', '🔥'],
    rules: ['В чатах только ученики: вход проверяется через «Сетевой город».',
      'Другим видно только твой ник и номер класса — без фамилии.',
      'Администрация видит все чаты и может удалять сообщения и блокировать нарушителей.',
      'В общем чате нельзя оставлять ссылки, телефоны и ники.'],
    nick_hint: '3–20 символов: буквы, цифры и _, начинается с буквы', unread_total: 2,
    profile: demoChatState.joined ? { nick: demoChatState.nick || 'Отличник', class_name: '9б', grade: 9, banned: false, muted_until: 0 } : null,
    rooms: demoChatState.joined ? DEMO_ROOMS.map((room) => {
      const list = demoChatState.messages[room.id] || [];
      const last = list[list.length - 1];
      return { ...room, unread: room.kind === 'grade' ? 2 : 0, notify: demoChatState.notify?.[room.id] || (room.kind === 'global' ? 'mentions' : 'all'),
        last: last ? { author: last.author, text: last.text, at: last.at } : null };
    }) : [],
  });
  if (route === '/api/chat') return overview();
  if (route === '/api/chat/unread') return ok({ total: demoChatState.joined ? 2 : 0, live: false });
  if (route === '/api/chat/nick') { demoChatState.nick = body.nick; return overview(); }
  if (route === '/api/chat/join') { demoChatState.joined = true; demoChatState.nick = body.nick; return overview(); }
  if (/\/reads$/.test(route)) return ok({ items: [{ nick: 'Маша', grade: 9 }], count: 1 });
  if (route === '/api/chat/leave') { demoChatState.joined = false; return overview(); }
  const action = route.match(/^\/api\/chat\/messages\/(\d+)\/(react|report|moderate|vote|edit)$/);
  if (action) {
    const message = Object.values(demoChatState.messages).flat().find((m) => m.id === Number(action[1]));
    if (action[2] === 'react') {
      const mine = message.my_reactions.includes(body.emoji);
      message.my_reactions = mine ? message.my_reactions.filter((e) => e !== body.emoji) : [...message.my_reactions, body.emoji];
      const count = (message.reactions[body.emoji] || 0) + (mine ? -1 : 1);
      if (count) message.reactions[body.emoji] = count; else delete message.reactions[body.emoji];
    }
    if (action[2] === 'edit') Object.assign(message, { text: body.text, edited: true });
    if (action[2] === 'moderate' && body.action === 'delete') Object.assign(message, { deleted: true, deleted_by: 'author', text: null });
    if (action[2] === 'vote' && message.poll) {
      const poll = message.poll;
      if (poll.my != null) poll.options[poll.my].votes -= 1;
      poll.my = poll.my === body.option ? null : body.option;
      if (poll.my != null) poll.options[poll.my].votes += 1;
      poll.total = poll.options.reduce((sum, option) => sum + option.votes, 0);
      return ok({ ...message, poll: JSON.parse(JSON.stringify(poll)) });
    }
    return ok({ ...message, reactions: { ...message.reactions }, my_reactions: [...message.my_reactions] });
  }
  const match = route.match(/^\/api\/chat\/rooms\/([^/]+)\/(messages|read|mute|notify|members)$/);
  if (!match) return overview();
  const room = decodeURIComponent(match[1]);
  const list = (demoChatState.messages[room] ||= []);
  if (match[2] === 'members') return ok({ items: ['Маша', 'Артём', 'Даня', 'admin'].filter(nick => nick.toLowerCase().startsWith((url.searchParams.get('q') || '').toLowerCase())).map(nick => ({ nick, grade: nick === 'admin' ? null : 9 })) });
  if (match[2] === 'notify') { (demoChatState.notify ||= {})[room] = body.mode; return ok({ notify: body.mode }); }
  if (match[2] === 'read') return { ok: true };
  if (match[2] === 'mute') return ok({ muted: Boolean(body.muted) });
  if (options.method === 'POST') {
    const reply = list.find((m) => m.id === body.reply_to);
    const message = {
      id: demoChatState.nextId++, author: demoChatState.nick || 'Отличник', grade: 9, kind: 'text', admin: false, mine: true, text: body.text,
      deleted: false, hidden: false, pinned: false, at: Date.now() / 1000,
      reply: reply ? { id: reply.id, author: reply.author, text: reply.text } : null, reactions: {}, my_reactions: [],
      poll: body.poll ? { options: body.poll.options.map((text) => ({ text, votes: 0 })), total: 0, my: null } : null,
    };
    list.push(message);
    return ok(message);
  }
  const after = url.searchParams.get('after');
  return ok({
    room: DEMO_ROOMS.find((r) => r.id === room), moderator: false, admin: false, pinned: null, notify: demoChatState.notify?.[room] || 'all', others_read: 0, now: Date.now() / 1000,
    messages: after != null ? list.filter((m) => m.id > Number(after)) : list.slice(-50),
    changed: [],
  });
}
