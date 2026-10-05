"""Расписание в формате iCalendar."""

import datetime as dt

from app.domain.calendar import build_ics
from app.domain.models import zone
from app.domain.records import DiaryDay, HomeworkRecord, Lesson


def _day():
    homework = HomeworkRecord("Алгебра", dt.date(2026, 10, 6), "ДЗ", "№ 1, 2; стр. 5")
    return DiaryDay(
        day=dt.date(2026, 10, 6),
        lessons=[
            Lesson(number=1, subject="Алгебра", start="08:00", end="08:40", room="21",
                   teacher="Циркулев А. Г.", homework=(homework,)),
            Lesson(number=2, subject="Без времени"),
        ],
    )


def test_lessons_become_events_in_utc():
    text = build_ics([_day()], zone("Europe/Moscow"))
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20261006T050000Z" in text
    assert "SUMMARY:1. Алгебра" in text
    assert "LOCATION:каб. 21" in text
    # Запятые и точки с запятой экранируются.
    assert r"№ 1\, 2\; стр. 5" in text.replace("\r\n ", "")
    # Урок без времени в календарь не попадает.
    assert "Без времени" not in text
    assert text.count("BEGIN:VEVENT") == 1


def test_long_lines_are_folded():
    text = build_ics([_day()], zone("Asia/Novosibirsk"))
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert "DTSTART:20261006T010000Z" in text
