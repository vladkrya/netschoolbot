import datetime as dt

from app.domain.records import Lesson
from app.web.api import _lesson_over


def test_today_lesson_counts_only_after_bell():
    day = dt.date(2026, 10, 15)
    lesson = Lesson(3, "Биология", start="10:00", end="10:40")
    assert not _lesson_over(day, lesson, dt.datetime(2026, 10, 15, 4, 9))
    assert not _lesson_over(day, lesson, dt.datetime(2026, 10, 15, 10, 39))
    assert _lesson_over(day, lesson, dt.datetime(2026, 10, 15, 10, 40))


def test_past_future_and_untimed_lessons():
    now = dt.datetime(2026, 10, 15, 12, 0)
    assert _lesson_over(dt.date(2026, 10, 14), Lesson(1, "Биология"), now)
    assert not _lesson_over(dt.date(2026, 10, 16), Lesson(1, "Биология", end="08:00"), now)
    assert not _lesson_over(dt.date(2026, 10, 15), Lesson(1, "Биология"), now)
