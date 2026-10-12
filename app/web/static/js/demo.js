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
        }]
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

  if (route === '/api/pwa-link') {
    return ok({ url: location.href });
  }

  const error = new Error('В демо это недоступно');
  error.reason = 'demo';
  throw error;
}
