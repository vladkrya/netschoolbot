"""Тесты текстов. Раньше формулировки жили внутри функций, ходивших в сеть,
и проверить их можно было только вручную через живого бота."""

import datetime as dt
from types import SimpleNamespace

import pytest

from app.domain.formatting import (
    MAX_MESSAGE_LENGTH,
    esc,
    files_label,
    homework_digest,
    homework_list,
    mark_event,
    mark_events_digest,
    marks_by_subject,
    plural,
    profile,
    schedule,
    split_message,
    statistics,
    weekly_summary,
    weighted_average,
)
from app.domain.models import MarkKind
from app.domain.records import Attachment, HomeworkRecord, MarkEvent, MarkRecord
from tests.test_repositories import make_user


def record(subject="Алгебра", value="5", weight=1, date=dt.date(2026, 3, 2)):
    return MarkRecord(
        subject=subject,
        date=date,
        assignment_type="Контрольная работа",
        content="Дроби",
        mark=value,
        weight=weight,
    )


class TestEscaping:
    def test_dangerous_characters(self):
        assert esc("<b>&") == "&lt;b&gt;&amp;"

    def test_subject_with_angle_brackets_is_escaped(self):
        # Такой предмет ломал отправку сообщения целиком.
        text = mark_event(MarkEvent(MarkKind.NEW, record(subject="Алгебра <8а>")))
        assert "&lt;8а&gt;" in text
        assert "<8а>" not in text

    def test_none_becomes_empty(self):
        assert esc(None) == ""


class TestPlural:
    @pytest.mark.parametrize(
        "count,expected",
        [(1, "оценка"), (2, "оценки"), (5, "оценок"), (11, "оценок"),
         (21, "оценка"), (22, "оценки"), (25, "оценок"), (101, "оценка"), (114, "оценок")],
    )
    def test_russian_plural(self, count, expected):
        assert plural(count, "оценка", "оценки", "оценок") == expected

    def test_files(self):
        assert files_label(1) == "1 файл"
        assert files_label(3) == "3 файла"
        assert files_label(7) == "7 файлов"


class TestMarkEvent:
    def test_new(self):
        text = mark_event(MarkEvent(MarkKind.NEW, record()))
        assert "Новая оценка" in text
        assert "Алгебра" in text
        assert "02.03.2026" in text

    def test_mark_comes_before_title(self):
        # На часах видно только начало уведомления — оценка должна быть наверху.
        text = mark_event(MarkEvent(MarkKind.NEW, record(weight=15)))
        order = [text.index(label) for label in ("Предмет:", "Оценка:", "Вес:", "Дата:", "Работа:")]
        assert order == sorted(order)

    def test_changed_shows_both_values(self):
        text = mark_event(MarkEvent(MarkKind.CHANGED, record(value="5"), old_mark="3", new_mark="5"))
        assert "3 → " in text
        assert "Оценка изменена" in text

    def test_deleted_shows_previous_value(self):
        text = mark_event(MarkEvent(MarkKind.DELETED, record(value="2")))
        assert "Оценка удалена" in text
        assert "Была" in text

    def test_weight_shown_only_when_meaningful(self):
        assert "Вес" not in mark_event(MarkEvent(MarkKind.NEW, record(weight=1)))
        assert "Вес" in mark_event(MarkEvent(MarkKind.NEW, record(weight=3)))


class TestDigests:
    def test_counts_each_kind(self):
        events = [
            MarkEvent(MarkKind.NEW, record()),
            MarkEvent(MarkKind.NEW, record(subject="Химия")),
            MarkEvent(MarkKind.CHANGED, record(), old_mark="3", new_mark="4"),
            MarkEvent(MarkKind.DELETED, record(subject="Физика")),
        ]
        text = mark_events_digest(events)
        assert "2 новые" in text
        assert "изменено: 1" in text
        assert "удалено: 1" in text

    def test_homework_digest_marks_attachments(self):
        items = [
            HomeworkRecord(
                subject="Алгебра",
                due_date=dt.date(2026, 3, 3),
                assignment_type="ДЗ",
                text="Упр. 15",
                attachments=(Attachment(1, "лист.pdf"),),
            )
        ]
        assert "1 файл" in homework_digest(items)


class TestHomeworkList:
    def test_grouped_by_date(self):
        items = [
            HomeworkRecord("Алгебра", dt.date(2026, 3, 3), "ДЗ", "Упр. 1"),
            HomeworkRecord("Химия", dt.date(2026, 3, 3), "ДЗ", "Параграф 5"),
            HomeworkRecord("Физика", dt.date(2026, 3, 4), "ДЗ", "Задача 7"),
        ]
        text = homework_list(items, "📚 Задания")
        assert text.count("📅") == 2

    def test_empty(self):
        assert "Заданий нет" in homework_list([], "📚 Задания")


class TestAverages:
    def test_weighted(self):
        records = [record(value="5", weight=3), record(value="2", weight=1)]
        # (5*3 + 2*1) / 4 = 4.25
        assert weighted_average(records) == pytest.approx(4.25)

    def test_non_numeric_marks_are_ignored(self):
        assert weighted_average([record(value="зачтено"), record(value="4")]) == 4.0

    def test_all_non_numeric_gives_none(self):
        assert weighted_average([record(value="зачтено")]) is None

    def test_zero_weight_counts_as_one(self):
        assert weighted_average([record(value="4", weight=0)]) == 4.0

    def test_empty(self):
        assert weighted_average([]) is None


class TestSubjectMarks:
    def test_sorted_by_date_with_average(self):
        records = [
            record(value="4", date=dt.date(2026, 3, 5)),
            record(value="5", date=dt.date(2026, 3, 2)),
        ]
        text = marks_by_subject("Алгебра", records)
        assert text.index("02.03") < text.index("05.03")
        assert "Средний балл" in text

    def test_empty(self):
        assert "оценок нет" in marks_by_subject("Алгебра", [])


class TestStatistics:
    def test_basic(self):
        user = make_user(student_name="Иванов И.")
        records = [record(value="5"), record(value="4", subject="Химия"), record(value="3")]
        text = statistics(user, records)
        assert "Иванов И." in text
        assert "Всего оценок" in text

    def test_no_numeric_marks(self):
        assert "нет" in statistics(make_user(), [record(value="зачтено")])


class TestWeeklySummary:
    def test_empty_week(self):
        text = weekly_summary([], dt.date(2026, 3, 2), dt.date(2026, 3, 8))
        assert "Новых оценок за неделю нет" in text

    def test_with_marks(self):
        text = weekly_summary([record(), record(subject="Химия")], dt.date(2026, 3, 2), dt.date(2026, 3, 8))
        assert "Оценок" in text
        assert "Химия" in text


class TestSchedule:
    def test_lessons_are_numbered_in_order(self):
        day = SimpleNamespace(
            day=dt.date(2026, 3, 2),
            lessons=[
                SimpleNamespace(subject="Химия", number=2, start=dt.time(10), end=dt.time(10, 45), room="21"),
                SimpleNamespace(subject="Алгебра", number=1, start=dt.time(9), end=dt.time(9, 45), room=None),
            ],
        )
        text = schedule([day], "🗓 Расписание")
        assert text.index("Алгебра") < text.index("Химия")
        assert "каб. 21" in text

    def test_empty(self):
        assert "Уроков нет" in schedule([], "🗓 Расписание")

    def test_days_without_lessons_are_skipped(self):
        day = SimpleNamespace(day=dt.date(2026, 3, 7), lessons=[])
        assert "Уроков нет" in schedule([day], "🗓 Расписание")


class TestProfile:
    def test_contains_key_settings(self):
        text = profile(make_user(check_interval=600))
        assert "10 мин" in text
        assert "Школа №1" in text


class TestSplitMessage:
    def test_short_text_is_one_part(self):
        assert split_message("привет") == ["привет"]

    def test_long_text_is_split_within_limit(self):
        text = "\n".join(f"строка номер {i}" for i in range(1000))
        parts = split_message(text)
        assert len(parts) > 1
        assert all(len(p) <= MAX_MESSAGE_LENGTH for p in parts)

    def test_lines_are_not_broken(self):
        text = "\n".join(f"строка-{i}" for i in range(1000))
        parts = split_message(text)
        assert all(line.startswith("строка-") for part in parts for line in part.split("\n"))

    def test_single_overlong_line_is_force_split(self):
        # Иначе цикл не смог бы её разместить и выдавал бы пустые куски.
        parts = split_message("я" * (MAX_MESSAGE_LENGTH * 3))
        assert len(parts) == 3
        assert all(len(p) <= MAX_MESSAGE_LENGTH for p in parts)
        assert "".join(parts) == "я" * (MAX_MESSAGE_LENGTH * 3)

    def test_nothing_is_lost(self):
        text = "\n".join(f"строка {i}" for i in range(500))
        assert "\n".join(split_message(text)) == text


@pytest.mark.parametrize(("raw", "label"), [
    ("9б", "9 «Б» класс"),
    ("9 Б", "9 «Б» класс"),
    ("10-А", "10 «А» класс"),
    ("11 «В»", "11 «В» класс"),
    ("5", "5 класс"),
    ("", ""),
    ("Группа ИЗО", "Группа ИЗО"),
])
def test_class_label(raw, label):
    from app.domain.formatting import class_label
    assert class_label(raw) == label
