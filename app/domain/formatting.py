"""Тексты, которые видит человек.

Отделено от логики намеренно: раньше формулировки жили внутри тех же
функций, что ходили в сеть, поэтому проверить их можно было только вручную
через живого бота.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections import Counter

from .models import DEFAULT_TIMEZONE, ApiMode, MarkKind, User, timezone_label, zone
from .records import DUTY_MARK, AbsenceRecord, HomeworkRecord, MarkEvent, MarkRecord, parse_mark_float

MSK = zone(DEFAULT_TIMEZONE)

WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

# Telegram режет сообщения длиннее 4096 символов; берём с запасом на разметку.
MAX_MESSAGE_LENGTH = 3500


def esc(value: object) -> str:
    """Экранирование для HTML-разметки Telegram.

    В старом коде часть текстов экранировалась, часть нет, поэтому предмет
    с «<» или «&» в названии ломал отправку сообщения целиком.
    """
    return html.escape(str(value if value is not None else ""))


def date_label(day: dt.date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day:%d.%m}"


def plural(count: int, one: str, few: str, many: str) -> str:
    """Русское склонение: 1 оценка, 2 оценки, 5 оценок."""
    if 11 <= count % 100 <= 14:
        return many
    last = count % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def files_label(count: int) -> str:
    return f"{count} {plural(count, 'файл', 'файла', 'файлов')}"


def mark_text(mark: str) -> str:
    """Оценка для текста уведомления: точку подписываем, иначе её не понять.

    Цвета у текста в Telegram нет, поэтому красный — кружком 🔴.
    """
    return "🔴 точка (долг, считается как 2)" if mark == DUTY_MARK else esc(mark)


def mark_event(event: MarkEvent) -> str:
    """Одно уведомление об оценке."""
    record = event.record
    head = {
        MarkKind.NEW: "📊 <b>Новая оценка</b>",
        MarkKind.CHANGED: "✏️ <b>Оценка изменена</b>",
        MarkKind.DELETED: "🗑 <b>Оценка удалена</b>",
    }[event.kind]

    # Оценка и вес — сразу под предметом: на часах уведомление обрезается,
    # и до конца длинного сообщения с названием работы не доскроллить.
    lines = [
        head,
        "",
        f"<b>Предмет:</b> {esc(record.subject)}",
    ]
    if event.kind is MarkKind.CHANGED:
        lines.append(f"<b>Оценка:</b> {mark_text(event.old_mark)} → <b>{mark_text(event.new_mark)}</b>")
    elif event.kind is MarkKind.DELETED:
        lines.append(f"<b>Была:</b> {mark_text(record.mark)}")
    else:
        lines.append(f"<b>Оценка:</b> <b>{mark_text(record.mark)}</b>")
    if record.weight and record.weight != 1:
        lines.append(f"<b>Вес:</b> {record.weight}")
    lines.append(f"<b>Дата:</b> {record.date:%d.%m.%Y}")
    lines.append(f"<b>Работа:</b> {esc(record.title)}")
    return "\n".join(lines)


def mark_events_digest(events: list[MarkEvent]) -> str:
    """Сводка, когда событий слишком много для отдельных сообщений."""
    by_kind = Counter(event.kind for event in events)
    header_parts = []
    if by_kind[MarkKind.NEW]:
        count = by_kind[MarkKind.NEW]
        header_parts.append(f"{count} {plural(count, 'новая', 'новые', 'новых')}")
    if by_kind[MarkKind.CHANGED]:
        header_parts.append(f"изменено: {by_kind[MarkKind.CHANGED]}")
    if by_kind[MarkKind.DELETED]:
        header_parts.append(f"удалено: {by_kind[MarkKind.DELETED]}")

    lines = [f"📊 <b>Изменения в оценках</b> ({', '.join(header_parts)})", ""]
    for event in sorted(events, key=lambda e: (e.record.date, e.record.subject)):
        record = event.record
        icon = {MarkKind.NEW: "•", MarkKind.CHANGED: "✏️", MarkKind.DELETED: "🗑"}[event.kind]
        value = (
            f"{mark_text(event.old_mark)} → {mark_text(event.new_mark)}"
            if event.kind is MarkKind.CHANGED
            else mark_text(record.mark)
        )
        lines.append(f"{icon} {record.date:%d.%m} {esc(record.subject)}: {value}")
    return "\n".join(lines)


def homework_digest(items: list[HomeworkRecord], *, tz: dt.tzinfo = MSK) -> str:
    lines = [f"📚 <b>Новые домашние задания</b> ({len(items)})", ""]
    for item in sorted(items, key=lambda h: (h.due_date, h.subject)):
        suffix = f" ({files_label(len(item.attachments))})" if item.attachments else ""
        text = esc(item.text) or "—"
        lines.append(
            f"• {item.due_date:%d.%m} — {esc(item.subject)}: {text}{suffix}{published_label(item, tz)}"
        )
    return "\n".join(lines)


def absences_digest(items: list[AbsenceRecord]) -> str:
    lines = [f"🚫 <b>Новые отметки посещаемости</b> ({len(items)})", ""]
    for item in sorted(items, key=lambda a: (a.date, a.lesson_number or 0, a.subject)):
        lesson = f", {item.lesson_number} урок" if item.lesson_number else ""
        lines.append(f"• {item.date:%d.%m} — {esc(item.subject)}{lesson}: {esc(item.label)}")
    return "\n".join(lines)


def token_expiring(expires_at: dt.datetime, *, tz: dt.tzinfo = MSK) -> str:
    """Предупреждение: мобильный доступ скоро истечёт, автоматически продлить не вышло."""
    moment = expires_at
    if moment.tzinfo is not None:
        moment = moment.astimezone(tz)
    return "\n".join(
        [
            "⏳ <b>Мобильный доступ скоро истечёт</b>",
            "",
            f"Он действует до {moment:%d.%m.%Y}, продлить его автоматически не получилось. "
            "Чтобы уведомления не прервались, войдите заново: /login.",
            "",
            "Или переключитесь на веб-API: /changeapi.",
        ]
    )


def published_label(item: HomeworkRecord, tz: dt.tzinfo = MSK) -> str:
    """` · опубл. 29.09 09:34` — только в мобильном API, где время известно."""
    if item.published_at is None:
        return ""
    moment = item.published_at
    if moment.tzinfo is not None:
        moment = moment.astimezone(tz)
    return f" <i>· опубл. {moment:%d.%m %H:%M}</i>"


def homework_list(
    items: list[HomeworkRecord], header: str, *, tz: dt.tzinfo = MSK, done: set[str] | None = None
) -> str:
    if not items:
        return f"{header}\n\nЗаданий нет."
    lines = [header, ""]
    current_date: dt.date | None = None
    for item in sorted(items, key=lambda h: (h.due_date, h.lesson_number or 0, h.subject)):
        if item.due_date != current_date:
            current_date = item.due_date
            lines.append(f"\n📅 <b>{date_label(item.due_date)}</b>")
        suffix = f" ({files_label(len(item.attachments))})" if item.attachments else ""
        bullet = "✅" if item.nothing_to_do or (done and item.key in done) else "•"
        lines.append(f"{bullet} {esc(item.subject)}: {esc(item.text) or '—'}{suffix}{published_label(item, tz)}")
    return "\n".join(lines)


def marks_by_subject(subject: str, records: list[MarkRecord]) -> str:
    """Список оценок по предмету со средним баллом с учётом веса."""
    if not records:
        return f"📋 По предмету {esc(subject)} оценок нет."

    ordered = sorted(records, key=lambda r: r.date)
    lines = [f"📋 <b>Оценки по предмету {esc(subject)}</b>", ""]
    for record in ordered:
        weight = f" (вес {record.weight})" if record.weight and record.weight != 1 else ""
        lines.append(f"• {record.date:%d.%m} {esc(record.title)} — <b>{esc(record.mark)}</b>{weight}")

    average = weighted_average(ordered)
    lines.append("")
    lines.append(f"Всего: {len(ordered)} {plural(len(ordered), 'оценка', 'оценки', 'оценок')}")
    if average is not None:
        lines.append(f"Средний балл: <b>{average:.2f}</b>")
    return "\n".join(lines)


def weighted_average(records: list[MarkRecord]) -> float | None:
    """Средний балл с учётом веса работы. None — числовых оценок нет."""
    total = 0.0
    weights = 0.0
    for record in records:
        value = parse_mark_float(record.mark)
        if value is None:
            continue
        weight = float(record.weight) if record.weight and record.weight > 0 else 1.0
        total += value * weight
        weights += weight
    return total / weights if weights else None


def statistics(user: User, records: list[MarkRecord]) -> str:
    numeric = [r for r in records if r.numeric_mark is not None]
    if not numeric:
        return "📊 <b>Статистика</b>\n\nЧисловых оценок за период нет."

    counts = Counter(r.numeric_mark for r in numeric)
    by_subject: dict[str, list[MarkRecord]] = {}
    for record in numeric:
        by_subject.setdefault(record.subject, []).append(record)

    ranked = sorted(
        ((subject, weighted_average(items) or 0.0, len(items))
         for subject, items in by_subject.items()),
        key=lambda item: item[1],
        reverse=True,
    )

    average = weighted_average(numeric)
    lines = ["📊 <b>Статистика</b>", ""]
    if user.student_name:
        lines.append(f"👤 {esc(user.student_name)}")
    lines.extend(
        [
            f"• Средний балл: <b>{average:.2f}</b>" if average else "• Средний балл: —",
            f"• Всего оценок: <b>{len(numeric)}</b>",
            f"• 5: {counts.get(5, 0)} | 4: {counts.get(4, 0)} | "
            f"3: {counts.get(3, 0)} | 2: {counts.get(2, 0)}",
            "",
            "<b>Лучшие предметы:</b>",
        ]
    )
    lines.extend(
        f"• {esc(subject)} — {avg:.2f} ({count})" for subject, avg, count in ranked[:6]
    )
    if len(ranked) > 6:
        lines.extend(["", "<b>Требуют внимания:</b>"])
        lines.extend(
            f"• {esc(subject)} — {avg:.2f} ({count})" for subject, avg, count in ranked[-3:]
        )
    return "\n".join(lines)


def weekly_summary(records: list[MarkRecord], start: dt.date, end: dt.date) -> str:
    header = f"🗓 <b>Сводка за неделю</b>\n{start:%d.%m} — {end:%d.%m}"
    if not records:
        return f"{header}\n\nНовых оценок за неделю нет."

    numeric = [r for r in records if r.numeric_mark is not None]
    by_subject = Counter(r.subject for r in records)
    lines = [header, "", f"• Оценок: <b>{len(records)}</b>"]
    average = weighted_average(numeric)
    if average is not None:
        counts = Counter(r.numeric_mark for r in numeric)
        lines.append(f"• Средний балл: <b>{average:.2f}</b>")
        lines.append(
            f"• 5: {counts.get(5, 0)} | 4: {counts.get(4, 0)} | "
            f"3: {counts.get(3, 0)} | 2: {counts.get(2, 0)}"
        )
    lines.extend(["", "<b>По предметам:</b>"])
    lines.extend(f"• {esc(subject)} — {count}" for subject, count in by_subject.most_common(8))
    return "\n".join(lines)


def _clock(value: object) -> str:
    """Время урока: в записях оно строкой «09:00», в старых данных — `time`."""
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value or "")


def _lesson_lines(lessons: list) -> list[str]:
    ordered = sorted(
        lessons, key=lambda l: (getattr(l, "number", None) or 0, _clock(getattr(l, "start", "")))
    )
    lines = []
    for index, lesson in enumerate(ordered, start=1):
        number = getattr(lesson, "number", None) or index
        start, end = _clock(getattr(lesson, "start", "")), _clock(getattr(lesson, "end", ""))
        when = f"{start}–{end} " if start and end else ""
        room = getattr(lesson, "room", None)
        where = f" (каб. {esc(room)})" if room else ""
        lines.append(f"{number}. {when}{esc(getattr(lesson, 'subject', '—'))}{where}")
    return lines


def schedule(days: list, header: str) -> str:
    """Расписание уроков. `days` — объекты с полями day и lessons."""
    filled = [day for day in days if getattr(day, "lessons", None)]
    if not filled:
        return f"{header}\n\nУроков нет."

    lines = [header]
    for day in sorted(filled, key=lambda d: d.day):
        lines.append(f"\n📅 <b>{date_label(day.day)}</b>")
        lines.extend(_lesson_lines(day.lessons))
    return "\n".join(lines)


def evening_summary(day: object | None, when: dt.date, *, tz: dt.tzinfo = MSK) -> str | None:
    """Сводка на завтра: уроки и задания. None — сказать нечего (каникулы)."""
    lessons = list(getattr(day, "lessons", None) or [])
    homework = list(getattr(day, "homework", None) or [])
    if not lessons and not homework:
        return None
    lines = [f"🌙 <b>Завтра, {date_label(when)}</b>"]
    if lessons:
        lines.append("")
        lines.extend(_lesson_lines(lessons))
    lines.append("")
    if homework:
        lines.append("📚 <b>Задано</b>")
        for item in sorted(homework, key=lambda h: (h.lesson_number or 0, h.subject)):
            suffix = f" ({files_label(len(item.attachments))})" if item.attachments else ""
            lines.append(f"• {esc(item.subject)}: {esc(item.text) or '—'}{suffix}")
    else:
        lines.append("📚 Заданий на завтра нет.")
    return "\n".join(lines)


def _local(moment: dt.datetime | None, tz: dt.tzinfo) -> str:
    """UTC из базы — в местное время пользователя."""
    if moment is None:
        return "—"
    return moment.replace(tzinfo=dt.timezone.utc).astimezone(tz).strftime("%d.%m %H:%M")


def watch_status(user: User, status: object, *, running: bool, tracked: int) -> str:
    """Текст /status: что с проверкой на самом деле, а не только «задача есть»."""
    tz = user.tz
    last_ok = getattr(status, "last_ok_at", None)
    last_error_at = getattr(status, "last_error_at", None)
    failing = last_error_at is not None and (last_ok is None or last_error_at > last_ok)
    if not user.enabled:
        state = "выключена 🔕"
    elif not running:
        state = "не запущена ⚠️"
    elif failing:
        state = "ошибка ⚠️"
    else:
        state = "идёт ✅"
    lines = [
        "📡 <b>Состояние</b>",
        "",
        f"• Проверка оценок: {state}",
        f"• Последняя успешная: {_local(last_ok, tz)}",
    ]
    if failing:
        lines.append(f"• Последняя ошибка: {_local(last_error_at, tz)} — {esc(status.last_error)}")
    if running and getattr(status, "next_check_at", None):
        lines.append(f"• Следующая попытка: {_local(status.next_check_at, tz)}")
    queued = getattr(status, "queued", 0)
    if queued:
        lines.append(f"• Ждут отправки: {queued} (тихие часы или сбой Telegram)")
    lines += [
        f"• Интервал: {user.check_interval // 60} мин",
        f"• Оценок под наблюдением: {tracked}",
        f"• Школа: {esc(user.school.name or '—')}",
        f"• Часовой пояс: {esc(timezone_label(user.timezone))}",
    ]
    return "\n".join(lines)


def schedule_changes(changes: list) -> str:
    """Замены и отмены уроков, по дням."""
    lines = ["🔁 <b>Изменения в расписании</b>"]
    current = None
    for change in sorted(changes, key=lambda c: (c.day, c.number)):
        if change.day != current:
            current = change.day
            lines.append(f"\n📅 <b>{date_label(change.day)}</b>")
        lesson = f"{change.number} урок: " if change.number else ""
        if change.kind == "replaced":
            lines.append(f"• {lesson}{esc(change.old)} → <b>{esc(change.new)}</b>")
        elif change.kind == "cancelled":
            lines.append(f"• {lesson}<s>{esc(change.old)}</s> — отменён")
        elif change.kind == "added":
            lines.append(f"• {lesson}<b>{esc(change.new)}</b> — добавлен")
        elif change.kind == "room":
            lines.append(f"• {lesson}кабинет {esc(change.old) or '—'} → <b>{esc(change.new)}</b>")
        else:
            lines.append("• расписание дня перестроено — загляните в дневник")
    return "\n".join(lines)


def schedule_preview(changes: list) -> str:
    """Строка для push: даты с изменениями, без HTML."""
    days = sorted({change.day for change in changes})
    labels = [date_label(day) for day in days[:3]]
    if len(days) > 3:
        labels.append(f"и ещё {len(days) - 3}")
    return ", ".join(labels)


HISTORY_ICONS = {MarkKind.NEW: "🆕", MarkKind.CHANGED: "✏️", MarkKind.DELETED: "🗑"}


def mark_history(entries: list[tuple[MarkKind, MarkRecord, str, str, dt.datetime]], *, tz: dt.tzinfo = MSK) -> str:
    """История изменений оценок: (вид, оценка, было, стало, когда замечено в UTC)."""
    if not entries:
        return "🕘 <b>История оценок</b>\n\nПока пусто: изменения появятся после следующих проверок."
    lines = ["🕘 <b>История оценок</b>", ""]
    for kind, record, old, new, seen in entries:
        if kind is MarkKind.CHANGED:
            value = f"{esc(old)} → <b>{esc(new)}</b>"
        elif kind is MarkKind.DELETED:
            value = f"<s>{esc(record.mark)}</s>"
        else:
            value = f"<b>{esc(record.mark)}</b>"
        lines.append(
            f"{HISTORY_ICONS[kind]} {_local(seen, tz)} · {esc(record.subject)}: {value}"
            f" <i>(за {record.date:%d.%m})</i>"
        )
    return "\n".join(lines)


def profile(user: User) -> str:
    notifications = user.notifications
    enabled = lambda flag: "включены ✅" if flag else "выключены 🔕"  # noqa: E731
    return "\n".join(
        [
            "👤 <b>Профиль</b>",
            "",
            f"• Ученик: {esc(user.label)}",
            f"• Детей в аккаунте: {len(user.available_students)}",
            f"• Школа: {esc(user.school.name or '—')}",
            f"• Адрес: {esc(user.school.url or '—')}",
            f"• Вход: {user.credentials.login_type.value}",
            f"• API: {user.effective_api_mode.title} (/changeapi)",
            "",
            f"• Уведомления: {enabled(user.enabled)}",
            f"• Интервал проверки: {user.check_interval // 60} мин",
            f"• Изменения оценок: {enabled(notifications.changes)}",
            f"• Удаления оценок: {enabled(notifications.deletes)}",
            f"• Домашние задания: {enabled(notifications.homework)}",
            f"• Школьная почта: {enabled(notifications.mail)}",
            f"• Сводка по понедельникам: {enabled(notifications.weekly_summary)}",
            *(
                [f"• Новые пропуски: {enabled(notifications.absences)}"]
                if user.effective_api_mode is ApiMode.MOBILE
                else []
            ),
            f"• Напоминание о Д/З: {enabled(notifications.homework_reminder)}"
            f" ({notifications.reminder_hour}:00)",
            f"• Контрольные заранее: {enabled(notifications.test_reminder)}",
            f"• Итоговая под угрозой: {enabled(notifications.grade_risk)}",
            f"• Итоги месяца и четверти: {enabled(notifications.monthly_summary)}",
            f"• Разбор недели от помощника: {enabled(notifications.weekly_review)}",
            f"• Тихий режим на каникулы: {enabled(notifications.vacation_quiet)}",
            *(
                [f"• Все дети: {enabled(notifications.all_children)}"]
                if len(user.available_students) > 1
                else []
            ),
            "• Новые оценки: "
            + ("все" if not notifications.marks_filter else "только " + ", ".join(map(str, notifications.marks_filter))),
            f"• Тихие часы: {user.quiet_hours.as_text()}",
            "",
            f"• Фильтр работ: {', '.join(sorted(user.filters.exclude_titles)) or 'нет'}",
            f"• Фильтр предметов: "
            f"{', '.join(sorted(user.filters.include_subjects)) or 'все предметы'}",
        ]
    )


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Разбить длинный текст по строкам, не разрывая их посередине."""
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.split("\n"):
        # Строка длиннее лимита сама по себе — режем принудительно, иначе
        # она никогда не поместится и цикл выдаст пустые куски.
        while len(line) > limit:
            if current:
                parts.append("\n".join(current))
                current, length = [], 0
            parts.append(line[:limit])
            line = line[limit:]
        if current and length + len(line) + 1 > limit:
            parts.append("\n".join(current))
            current, length = [line], len(line)
        else:
            current.append(line)
            length += len(line) + 1
    if current:
        parts.append("\n".join(current))
    return parts


def class_label(class_name: str) -> str:
    """«9б», «9 Б», «10-А» → «9 «Б» класс»; что не разобрать — как есть."""
    text = (class_name or "").strip()
    match = re.fullmatch(r"(\d{1,2})\s*[-–—]?\s*[«\"']?([А-ЯЁа-яёA-Za-z])[»\"']?(?:\s*класс)?", text, flags=re.I)
    if match:
        return f"{match.group(1)} «{match.group(2).upper()}» класс"
    if re.fullmatch(r"\d{1,2}", text):
        return f"{text} класс"
    return text


MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


def _number(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def student_header(name: str) -> str:
    """Подпись ребёнка над уведомлением, когда следят за несколькими."""
    return f"👤 <b>{esc(name)}</b>"


def reminder(
    day: dt.date,
    homework: list[HomeworkRecord],
    notes: list[dict],
    tests: list[HomeworkRecord],
) -> str | None:
    """Вечернее напоминание: несделанное на завтра и ближайшие проверочные.

    None — напоминать не о чем.
    """
    if not homework and not notes and not tests:
        return None
    lines = []
    if homework or notes:
        lines.append(f"⏰ <b>На {date_label(day)} не сделано</b>")
        for item in sorted(homework, key=lambda h: (h.lesson_number or 0, h.subject)):
            text = esc(item.text)
            if len(text) > 120:
                text = text[:120] + "…"
            lines.append(f"⬜ {esc(item.subject)}: {text or '—'}")
        for note in notes:
            subject = f"{esc(note.get('subject'))}: " if note.get("subject") else ""
            at = f"{note['time']} " if note.get("time") else ""
            lines.append(f"⬜ {at}{subject}{esc(note.get('text'))} <i>(своя задача)</i>")
        lines.append("\nОтметить сделанное — /dz или в приложении.")
    if tests:
        if lines:
            lines.append("")
        lines.append("📝 <b>Скоро проверочные</b>")
        for item in tests:
            when = "завтра" if item.due_date == day else date_label(item.due_date)
            weight = f", вес {item.weight}" if item.weight and item.weight > 1 else ""
            lines.append(f"• {when} — {esc(item.subject)}: {esc(item.assignment_type)}{weight}")
    return "\n".join(lines)


def risk_alerts(alerts: list) -> str:
    lines = ["⚠️ <b>Итоговая оценка под угрозой</b>", ""]
    for alert in alerts:
        line = (f"• {esc(alert.subject)}: средний {_number(alert.average)} — "
                f"сейчас выходит «{alert.grade}» вместо «{alert.previous_grade}»")
        if alert.fives:
            line += (f". Чтобы вернуть «{alert.previous_grade}», нужно "
                     f"{alert.fives} {plural(alert.fives, 'пятёрка', 'пятёрки', 'пятёрок')}")
        lines.append(line)
    return "\n".join(lines)


def risk_preview(alerts: list) -> str:
    return ", ".join(f"{a.subject}: «{a.previous_grade}» → «{a.grade}»" for a in alerts[:3])


def edge_lines(alerts: list) -> list[str]:
    """Строки «на грани» для недельной сводки."""
    if not alerts:
        return []
    lines = ["", "⚠️ <b>На грани</b> (одна плохая оценка — и итог ниже):"]
    lines.extend(
        f"• {esc(a.subject)} — {_number(a.average)}, пока «{a.grade}»" for a in alerts[:6]
    )
    return lines


def _counts_line(counts: dict[int, int]) -> str:
    return " | ".join(f"{mark}: {counts.get(mark, 0)}" for mark in (5, 4, 3, 2))


def _delta(now: float | None, before: float | None) -> str:
    if now is None or before is None:
        return ""
    diff = now - before
    if abs(diff) < 0.005:
        return " (как в прошлый раз)"
    arrow = "↑" if diff > 0 else "↓"
    return f" ({arrow} {_number(abs(diff))})"


def monthly_summary(month: dt.date, stats, previous=None) -> str:
    """Сводка за месяц: число оценок, средний, лучшие и слабые предметы."""
    header = f"📅 <b>Итоги месяца: {MONTHS[month.month - 1]} {month.year}</b>"
    if not stats.count:
        return f"{header}\n\nОценок за месяц нет."
    lines = [header, "", f"• Оценок: <b>{stats.count}</b>"]
    if stats.average is not None:
        lines.append(f"• Средний балл: <b>{_number(stats.average)}</b>"
                     f"{_delta(stats.average, previous.average if previous else None)}")
        lines.append(f"• {_counts_line(stats.counts)}")
    if stats.absences:
        lines.append(f"• Пропусков уроков: {stats.absences}")
    if stats.subjects:
        lines.extend(["", "<b>Лучше всего:</b>"])
        lines.extend(f"• {esc(s)} — {_number(avg)} ({n})" for s, avg, n in stats.subjects[:3])
        weak = [item for item in stats.subjects[3:][-3:]]
        if weak:
            lines.extend(["", "<b>Подтянуть:</b>"])
            lines.extend(f"• {esc(s)} — {_number(avg)} ({n})" for s, avg, n in reversed(weak))
    return "\n".join(lines)


def term_summary(label: str, grades: list, stats, previous_average: float | None = None) -> str:
    """Итоги учебного периода: какая оценка выходит по каждому предмету."""
    header = f"🏁 <b>Итоги: {esc(label)}</b>"
    if not grades:
        return f"{header}\n\nОценок за период нет."
    lines = [header, ""]
    if stats.average is not None:
        lines.append(f"Средний балл: <b>{_number(stats.average)}</b>{_delta(stats.average, previous_average)}")
        lines.append(_counts_line(stats.counts))
    if stats.absences:
        lines.append(f"Пропусков уроков: {stats.absences}")
    lines.extend(["", "<b>Выходит по предметам</b> (по среднему и порогам):"])
    icons = {5: "🟢", 4: "🔵", 3: "🟡", 2: "🔴"}
    for subject, average, grade, count in sorted(grades, key=lambda g: (-g[2], -g[1], g[0])):
        lines.append(f"{icons.get(grade, '•')} {esc(subject)} — «{grade}» ({_number(average)}, {count})")
    lines.append("\n<i>Официальные итоговые ставит учитель — они могут отличаться.</i>")
    return "\n".join(lines)


def day_brief(day: object | None, when: dt.date, title: str, *, done: set[str] | None = None) -> str:
    """День одним сообщением: уроки с оценками и заданием — для /today и /tomorrow."""
    lessons = list(getattr(day, "lessons", None) or [])
    header = f"📅 <b>{title}, {date_label(when)}</b>"
    if not lessons:
        return f"{header}\n\nУроков нет."
    lines = [header, ""]
    for index, lesson in enumerate(
        sorted(lessons, key=lambda l: (l.number or 0, _clock(l.start))), start=1
    ):
        when_text = f"{lesson.start}–{lesson.end} " if lesson.start and lesson.end else ""
        room = f" (каб. {esc(lesson.room)})" if lesson.room else ""
        marks = " ".join(f"<b>{esc(m.mark)}</b>" for m in lesson.marks)
        lines.append(f"{lesson.number or index}. {when_text}{esc(lesson.subject)}{room}"
                     + (f" — {marks}" if marks else ""))
        if lesson.absence:
            lines.append(f"   🚫 {esc(lesson.absence)}")
        for item in lesson.homework:
            mark = "✅" if item.nothing_to_do or (done and item.key in done) else "📚"
            text = esc(item.text)
            if len(text) > 160:
                text = text[:160] + "…"
            lines.append(f"   {mark} {text or '—'}")
    return "\n".join(lines)


def last_marks(records: list[MarkRecord], limit: int = 15) -> str:
    """Последние выставленные оценки — для /last."""
    if not records:
        return "🆕 <b>Последние оценки</b>\n\nОценок за последние недели нет."

    def moment(record: MarkRecord) -> tuple:
        stamp = record.marked_at or record.published_at
        return (record.date, stamp.replace(tzinfo=None) if stamp else dt.datetime.min, record.lesson_number or 0)

    lines = ["🆕 <b>Последние оценки</b>", ""]
    for record in sorted(records, key=moment, reverse=True)[:limit]:
        weight = f" ×{record.weight}" if record.weight and record.weight != 1 else ""
        lines.append(f"• {date_label(record.date)} · {esc(record.subject)}: <b>{esc(record.mark)}</b>{weight}"
                     f" <i>{esc(record.assignment_type)}</i>")
    return "\n".join(lines)
