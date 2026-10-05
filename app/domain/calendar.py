"""Расписание в формате iCalendar (RFC 5545) для подписки в календаре телефона.

Каждый урок — событие со временем, кабинетом, учителем и заданием в
описании. Чистая функция без сети: на вход дни дневника, на выход текст.
"""

from __future__ import annotations

import datetime as dt
import hashlib

from .records import DiaryDay, Lesson, clean_content

PRODID = "-//netschoolbot//Дневник//RU"


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> list[str]:
    """Строки длиннее 75 байт переносятся с пробелом в начале продолжения."""
    raw = line.encode()
    if len(raw) <= 75:
        return [line]
    parts: list[str] = []
    current = b""
    limit = 75
    for char in line:
        encoded = char.encode()
        if len(current) + len(encoded) > limit:
            parts.append(current.decode())
            current = b" "
            limit = 75
        current += encoded
    parts.append(current.decode())
    return parts


def _utc(day: dt.date, clock: str, tz: dt.tzinfo) -> str | None:
    try:
        moment = dt.datetime.strptime(f"{day.isoformat()} {clock}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return moment.replace(tzinfo=tz).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _event(day: dt.date, lesson: Lesson, tz: dt.tzinfo, stamp: str) -> list[str] | None:
    start, end = _utc(day, lesson.start, tz), _utc(day, lesson.end, tz)
    if not start or not end:
        # Без времени урок в календаре не поставить.
        return None
    uid = hashlib.sha1(f"{day}|{lesson.number}|{lesson.subject}".encode()).hexdigest()[:20]
    details = []
    if lesson.topic:
        details.append(f"Тема: {lesson.topic}")
    if lesson.teacher:
        details.append(f"Учитель: {lesson.teacher}")
    for item in lesson.homework:
        text = clean_content(item.text) or "—"
        files = f" (файлов: {len(item.attachments)})" if item.attachments else ""
        details.append(f"Д/З: {text}{files}")
    number = f"{lesson.number}. " if lesson.number else ""
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}@netschoolbot",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start}",
        f"DTEND:{end}",
        f"SUMMARY:{_escape(number + lesson.subject)}",
    ]
    if lesson.room:
        lines.append(f"LOCATION:{_escape('каб. ' + lesson.room)}")
    if details:
        lines.append(f"DESCRIPTION:{_escape(chr(10).join(details))}")
    lines.append("END:VEVENT")
    return lines


def build_ics(days: list[DiaryDay], tz: dt.tzinfo, *, name: str = "Расписание") -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
        # Календарь сам переспрашивает раз в несколько часов.
        "REFRESH-INTERVAL;VALUE=DURATION:PT3H",
        "X-PUBLISHED-TTL:PT3H",
    ]
    for day in sorted(days, key=lambda d: d.day):
        for lesson in day.lessons:
            event = _event(day.day, lesson, tz, stamp)
            if event:
                lines.extend(event)
    lines.append("END:VCALENDAR")
    folded = [part for line in lines for part in _fold(line)]
    return "\r\n".join(folded) + "\r\n"
