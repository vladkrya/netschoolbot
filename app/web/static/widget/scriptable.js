// Виджет «Сетевой город» для Scriptable: «До конца урока 12:34», перемена,
// следующий урок. Работает на экране «Домой» (малый, средний, большой) и на
// экране блокировки (прямоугольный, круглый, строка над часами).
//
// Ключ ниже открывает только расписание. Если скрипт попал в чужие руки,
// выпустите новый ключ в приложении: старый сразу перестанет работать.
const SERVER = "__SERVER__";
const KEY = "__KEY__";

// Расписание переспрашиваем не чаще, чем раз в столько минут; между
// запросами виджет считает урок и перемену сам, без сети.
const REFETCH_MIN = 30;
// Перед первым уроком отсчёт показывается не раньше чем за столько минут.
const BEFORE_FIRST_MIN = 60;

const C = {
  bg: new Color("#16181d"),
  text: new Color("#f2f3f5"),
  muted: new Color("#8b909b"),
  clock: new Color("#c9ccd3"),
  lesson: new Color("#8b909b"),
  brk: new Color("#4cc38a"),
};

const fm = FileManager.local();
const cachePath = fm.joinPath(fm.documentsDirectory(), "netschool-widget.json");

function readCache() {
  try { return fm.fileExists(cachePath) ? JSON.parse(fm.readString(cachePath)) : null; } catch (e) { return null; }
}

async function load() {
  const cached = readCache();
  const now = Date.now() / 1000;
  const today = isoDay(new Date());
  const fresh = cached && now - cached.fetched < REFETCH_MIN * 60 && cached.today === today;
  if (fresh) return { data: cached.data, error: null };
  try {
    const req = new Request(`${SERVER}/widget/schedule`);
    req.headers = { "X-Widget-Key": KEY };
    req.timeoutInterval = 20;
    const body = await req.loadJSON();
    if (!body.ok) throw new Error(body.error || "Сервер не ответил");
    fm.writeString(cachePath, JSON.stringify({ fetched: now, today, data: body.data }));
    return { data: body.data, error: null };
  } catch (e) {
    // Без сети показываем то, что уже знаем: уроки сегодня от этого не меняются.
    return { data: cached ? cached.data : null, error: String(e.message || e) };
  }
}

function isoDay(date) {
  const p = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}`;
}

/** Что сейчас: урок, перемена перед уроком или ничего (с ближайшим уроком). */
function phase(data, now = Date.now() / 1000) {
  const items = [];
  for (const day of data.days || []) for (const l of day.lessons) items.push({ ...l, date: day.date });
  items.sort((a, b) => a.start - b.start);
  for (let i = 0; i < items.length; i += 1) {
    const l = items[i];
    const prev = i && items[i - 1].date === l.date ? items[i - 1] : null;
    const next = items[i + 1] && items[i + 1].date === l.date ? items[i + 1] : null;
    if (now >= l.start && now < l.end) return { kind: "lesson", lesson: l, next, from: l.start, until: l.end };
    if (now < l.start) {
      const from = prev ? prev.end : l.start - BEFORE_FIRST_MIN * 60;
      if (now >= from) return { kind: "break", lesson: l, next: l, first: !prev, from, until: l.start };
      return { kind: "idle", upcoming: l, from: null, until: from };
    }
  }
  return { kind: "idle", upcoming: null };
}

function lessonName(l) {
  return `${l.number ? `${l.number} урок` : "Урок"} · ${l.subject}`;
}

function room(l) {
  return l.room ? ` · каб. ${l.room}` : "";
}

function title(p) {
  if (p.kind === "lesson") return `Идёт ${p.lesson.number ? `${p.lesson.number} урок` : "урок"} · ${p.lesson.subject}`;
  return p.first ? "Скоро уроки" : "Перемена";
}

function label(p) {
  return p.kind === "lesson" ? "До конца урока" : "До начала урока";
}

function nextText(p) {
  if (p.kind === "break") return `${lessonName(p.lesson)}${room(p.lesson)}`;
  if (!p.next) return "Последний урок на сегодня";
  return `Дальше — ${p.next.subject}${room(p.next)}`;
}

function dayWord(date) {
  const today = new Date();
  const tomorrow = new Date(today.getTime() + 86400000);
  if (date === isoDay(today)) return "Сегодня";
  if (date === isoDay(tomorrow)) return "Завтра";
  const names = ["В воскресенье", "В понедельник", "Во вторник", "В среду", "В четверг", "В пятницу", "В субботу"];
  const [y, m, d] = date.split("-").map(Number);
  return names[new Date(y, m - 1, d).getDay()];
}

function idleText(p) {
  if (!p.upcoming) return { head: "Уроков нет", line: "Расписание на ближайшие дни пустое" };
  const u = p.upcoming;
  return {
    head: u.date === isoDay(new Date()) ? "Уроки ещё не начались" : "На сегодня всё 🎉",
    line: `${dayWord(u.date)} в ${u.start_text} — ${u.subject}${room(u)}`,
  };
}

function txt(stack, value, font, color, lines = 1) {
  const t = stack.addText(value);
  t.font = font;
  t.textColor = color;
  t.lineLimit = lines;
  t.minimumScaleFactor = 0.7;
  return t;
}

function timer(stack, until, size, color = C.clock) {
  const d = stack.addDate(new Date(until * 1000));
  d.applyTimerStyle();
  d.font = Font.boldMonospacedSystemFont(size);
  d.textColor = color;
  d.minimumScaleFactor = 0.6;
  return d;
}

function head(w, p, size) {
  const row = w.addStack();
  row.centerAlignContent();
  const dot = row.addText("●");
  dot.font = Font.systemFont(size - 2);
  dot.textColor = p.kind === "lesson" ? C.lesson : C.brk;
  row.addSpacer(6);
  txt(row, title(p), Font.mediumSystemFont(size), C.muted);
}

function homeWidget(w, p, data, family) {
  w.backgroundColor = C.bg;
  w.setPadding(14, 16, 14, 16);
  if (p.kind === "idle") {
    const t = idleText(p);
    txt(w, data.name || "", Font.mediumSystemFont(12), C.muted);
    w.addSpacer();
    txt(w, t.head, Font.boldSystemFont(family === "small" ? 17 : 20), C.text, 2);
    w.addSpacer(4);
    txt(w, t.line, Font.systemFont(13), C.muted, 2);
    w.addSpacer();
    return;
  }
  head(w, p, family === "small" ? 11 : 13);
  w.addSpacer();
  if (family === "small") {
    txt(w, label(p), Font.systemFont(13), C.text);
    timer(w, p.until, 30);
  } else {
    const row = w.addStack();
    row.bottomAlignContent();
    txt(row, label(p), Font.systemFont(17), C.text);
    row.addSpacer();
    const t = timer(row, p.until, 40);
    t.rightAlignText();
  }
  w.addSpacer(4);
  txt(w, `до ${new Date(p.until * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`, Font.systemFont(11), C.muted);
  w.addSpacer();
  txt(w, nextText(p), Font.systemFont(family === "small" ? 11 : 13), C.muted, family === "small" ? 2 : 1);
  if (family === "large") {
    w.addSpacer(10);
    const day = (data.days || []).find((d) => d.date === (p.lesson || {}).date);
    for (const l of (day ? day.lessons : [])) {
      const row = w.addStack();
      const now = Date.now() / 1000;
      const color = l.end <= now ? C.muted : C.text;
      txt(row, `${l.start_text}–${l.end_text}`, Font.mediumMonospacedSystemFont(12), C.muted);
      row.addSpacer(8);
      txt(row, `${l.number ? `${l.number}. ` : ""}${l.subject}${room(l)}`, Font.systemFont(12), color);
      w.addSpacer(3);
    }
    w.addSpacer();
  }
}

function lockWidget(w, p, family) {
  if (family === "accessoryInline") {
    if (p.kind === "idle") {
      const u = p.upcoming;
      w.addText(u ? `${dayWord(u.date)} ${u.start_text} · ${u.subject}` : "Уроков нет");
    } else {
      const end = new Date(p.until * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
      w.addText(`${p.kind === "lesson" ? p.lesson.subject : title(p)} · до ${end}`);
    }
    return;
  }
  if (family === "accessoryCircular") {
    w.addAccessoryWidgetBackground = true;
    if (p.kind === "idle") {
      const t = w.addText(p.upcoming ? p.upcoming.start_text : "—");
      t.font = Font.boldSystemFont(14);
      t.centerAlignText();
      return;
    }
    const t = w.addText(p.kind === "lesson" ? "урок" : p.first ? "скоро" : "перем.");
    t.font = Font.systemFont(10);
    t.centerAlignText();
    const d = timer(w, p.until, 14, Color.white());
    d.centerAlignText();
    return;
  }
  // accessoryRectangular
  if (p.kind === "idle") {
    const t = idleText(p);
    txt(w, t.head, Font.boldSystemFont(13), Color.white());
    txt(w, t.line, Font.systemFont(12), Color.white(), 2);
    return;
  }
  txt(w, p.kind === "lesson" ? p.lesson.subject : title(p), Font.boldSystemFont(13), Color.white());
  timer(w, p.until, 22, Color.white());
  txt(w, p.kind === "lesson" ? nextText(p) : `${p.lesson.subject}${room(p.lesson)}`, Font.systemFont(11), Color.white());
}

async function main() {
  const family = config.widgetFamily || "medium";
  const w = new ListWidget();
  const { data, error } = await load();
  if (!data) {
    if (!family.startsWith("accessory")) { w.backgroundColor = C.bg; w.setPadding(14, 16, 14, 16); }
    txt(w, "Сетевой город", Font.boldSystemFont(13), family.startsWith("accessory") ? Color.white() : C.text);
    txt(w, error || "Нет данных", Font.systemFont(11), family.startsWith("accessory") ? Color.white() : C.muted, 3);
    w.refreshAfterDate = new Date(Date.now() + 15 * 60000);
    return w;
  }
  if (data.app_url) w.url = data.app_url;
  const p = phase(data);
  if (family.startsWith("accessory")) lockWidget(w, p, family);
  else homeWidget(w, p, data, family);
  // Попросить iOS перерисовать виджет к звонку. Точно в срок iOS не обещает:
  // после нуля отсчёт может немного побыть на месте, пока виджет не обновится.
  // Без урока — не реже раза в час, чтобы подхватить замены в расписании.
  const now = Date.now();
  let next = p.until ? p.until * 1000 + 1000 : now + 60 * 60000;
  if (p.kind === "idle") next = Math.min(next, now + 60 * 60000);
  w.refreshAfterDate = new Date(Math.max(next, now + 30000));
  return w;
}

const widget = await main();
if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  // Запуск из самого Scriptable — предпросмотр.
  await widget.presentMedium();
}
Script.complete();
