export type Color = "yellow" | "red" | "violet";

/** Задание урока: из дневника (есть id) или из отчёта StudentGrades (есть result). */
export interface Task {
  id?: number;
  kind: string;
  content: string;
  mark?: string | null;
  result?: string;
  due?: string | null;
}

export interface Lesson {
  day: string;
  start: string;
  end: string;
  number: number;
  subject: string;
  room: string;
  teacher: string;
  topic: string;
  absence?: string;
  distance: boolean;
  elective: boolean;
  /** Группа предмета — только когда неделя собрана из расписания. */
  group?: string;
  sg?: number;
  /** Оценки из отчёта (когда дневник пустой). */
  marks?: string[];
  assignments: Task[];
  /** Демо: подпись и цвет как на макете. */
  kind?: string;
  color?: Color;
}

export interface Week {
  start: string;
  end: string;
  class?: string;
  term?: string;
  source?: "mobile" | "diary" | "schedule" | "demo";
  lessons: Lesson[];
}

export interface Me {
  name: string;
  class: string;
  mode?: "mobile" | "web";
}

export interface LessonDetails {
  due: Task[];
  next_day: string | null;
  next: Task[];
  note?: string;
}

export interface AssignmentCard {
  name: string;
  description: string;
  teacher: string;
  attachments: { id: number; name: string }[];
}

export interface GuestEntry {
  id: string;
  parent: string | null;
  reply_to: string | null;
  name: string;
  text: string;
  at: number;
  mine: boolean;
}

export interface Guestbook {
  entries: GuestEntry[];
  token: string;
  can_post: boolean;
}

export interface MailEntry {
  id: number;
  subject: string;
  author: string;
  to: string;
  sent: string;
  read: boolean;
}

export interface MailPage {
  folder: string;
  page: number;
  pages: number;
  total: number;
  entries: MailEntry[];
}

export interface MailMessage {
  id: number;
  subject: string;
  text: string;
  sent: string;
  author: string;
  to: string;
  folder: string;
  can_reply: boolean;
  attachments: { id: number; name: string }[];
}

export interface Person {
  id: string;
  name: string;
}

export interface PeriodTotal {
  avg: number | null;
  mark: string | number | null;
  code: string | null;
}

export interface Totals {
  periods: { key: string; name: string }[];
  terms: { id: number; name: string; current: boolean }[];
  current: string | null;
  subjects: { id: number; name: string; cells: Record<string, PeriodTotal> }[];
}

export interface Performance {
  subject: string;
  term: string;
  avg: number | null;
  class_avg: number | null;
  max: number;
  lessons: { passed: number | null; scheduled: number | null };
  teachers: string[];
  results: {
    date: string;
    mark: number | null;
    duty: boolean;
    weight: number | null;
    kind: string;
    abbr: string;
    comment: string;
  }[];
  stats: { mark: number; count: number }[];
}
