"""Тесты разбора ответов netschoolpy.

Библиотека отдаёт оценку то объектом с textMark, то словарём, то числом, а
список детей — то словарём, то списком. Именно здесь раньше расходились
реализации бота и мини-приложения.
"""

import datetime as dt
from types import SimpleNamespace

import pytest

from app.domain.records import extract_mark, mark_to_int, parse_mark_float
from app.netschool.mapping import (
    lessons_from_day,
    diary_days,
    is_placeholder_name,
    homework_from_day,
    marks_from_day,
    students_from_diary_init,
)


def assignment(**kwargs):
    return SimpleNamespace(
        **{
            "kind": "Контрольная работа",
            "content": "Дроби",
            "mark": 5,
            "weight": 2,
            "comment": "",
            "deadline": None,
            "attachments": [],
            **kwargs,
        }
    )


def lesson(assignments, subject="Алгебра", number=3):
    return SimpleNamespace(
        subject=subject,
        number=number,
        start=dt.time(9, 0),
        end=dt.time(9, 45),
        assignments=assignments,
    )


def day(lessons, date=dt.date(2026, 3, 2)):
    return SimpleNamespace(day=date, lessons=lessons)


class TestExtractMark:
    @pytest.mark.parametrize(
        "source,expected",
        [
            (5, "5"),
            ("4", "4"),
            (None, ""),
            ("", ""),
            ({"textMark": "зачтено"}, "зачтено"),
            ({"mark": 3}, "3"),
            ({"value": 4}, "4"),
            (SimpleNamespace(textMark="н/а", mark=None), "н/а"),
            (SimpleNamespace(mark_text="Б", mark=None), "Б"),
            (SimpleNamespace(special_mark_code="НП", mark=None), "НП"),
            (SimpleNamespace(mark=5), "5"),
            (True, ""),
        ],
    )
    def test_forms(self, source, expected):
        assert extract_mark(source) == expected

    def test_text_mark_wins_over_numeric(self):
        # У «зачтено» есть и числовое поле — показывать надо текст.
        assert extract_mark(SimpleNamespace(textMark="зачтено", mark=5)) == "зачтено"

    def test_no_infinite_recursion_on_self_reference(self):
        obj = SimpleNamespace(mark=None)
        obj.mark = obj
        assert extract_mark(obj) == ""

    @pytest.mark.parametrize(
        "raw,expected", [("5", 5), ("зачтено", None), ("4 (хорошо)", 4), ("0", None), ("10", 10)]
    )
    def test_to_int(self, raw, expected):
        assert mark_to_int(raw) == expected

    @pytest.mark.parametrize("raw,expected", [("4,5", 4.5), ("5", 5.0), ("зачтено", None)])
    def test_to_float(self, raw, expected):
        assert parse_mark_float(raw) == expected


class TestMarks:
    def test_basic_extraction(self):
        records = marks_from_day(day([lesson([assignment()])]))
        assert len(records) == 1
        record = records[0]
        assert record.subject == "Алгебра"
        assert record.mark == "5"
        assert record.weight == 2
        assert record.lesson_start == "09:00"
        assert record.title == "Контрольная работа Дроби"

    def test_assignments_without_mark_are_skipped(self):
        records = marks_from_day(day([lesson([assignment(mark=None), assignment(mark=4)])]))
        assert [r.mark for r in records] == ["4"]

    def test_index_distinguishes_assignments_in_one_lesson(self):
        records = marks_from_day(day([lesson([assignment(mark=5), assignment(mark=4)])]))
        assert {r.assignment_index for r in records} == {0, 1}
        assert records[0].identity != records[1].identity

    def test_empty_day_is_safe(self):
        assert marks_from_day(day([])) == []
        assert marks_from_day(SimpleNamespace(day=None, lessons=[])) == []

    def test_missing_lessons_attribute_is_safe(self):
        assert marks_from_day(SimpleNamespace(day=dt.date(2026, 3, 2), lessons=None)) == []

    def test_dict_shaped_input(self):
        raw = {
            "day": "2026-03-02",
            "lessons": [
                {
                    "subject": "Химия",
                    "number": 1,
                    "assignments": [{"kind": "Тест", "content": "Соли", "mark": 4}],
                }
            ],
        }
        records = marks_from_day(raw)
        assert records[0].subject == "Химия"
        assert records[0].mark == "4"


class TestHomework:
    TODAY = dt.date(2026, 3, 2)

    def test_basic(self):
        records = homework_from_day(
            day([lesson([assignment(content="Упр. 15", mark=None)])]), today=self.TODAY
        )
        assert len(records) == 1
        assert records[0].text == "Упр. 15"

    def test_far_future_is_ignored(self):
        far = self.TODAY + dt.timedelta(days=60)
        records = homework_from_day(
            day([lesson([assignment(content="Проект", deadline=far)])], date=far),
            today=self.TODAY,
        )
        assert records == []

    def test_old_homework_is_ignored(self):
        old = self.TODAY - dt.timedelta(days=10)
        records = homework_from_day(
            day([lesson([assignment(content="Старое", deadline=old)])], date=old),
            today=self.TODAY,
        )
        assert records == []

    def test_empty_content_without_attachments_is_skipped(self):
        records = homework_from_day(
            day([lesson([assignment(content="")])]), today=self.TODAY
        )
        assert records == []

    def test_placeholder_content_is_kept(self):
        # «---Не указана---» означает, что задание есть, а текста нет.
        records = homework_from_day(
            day([lesson([assignment(content="---Не указана---")])]), today=self.TODAY
        )
        assert [r.text for r in records] == ["Не указана"]

    def test_attachments_are_collected(self):
        att = SimpleNamespace(id=7, name="задание.pdf")
        records = homework_from_day(
            day([lesson([assignment(content="", attachments=[att])])]), today=self.TODAY
        )
        assert records[0].attachments[0].name == "задание.pdf"

    def test_attachment_without_name_gets_placeholder(self):
        att = SimpleNamespace(id=7, name=None)
        records = homework_from_day(
            day([lesson([assignment(content="", attachments=[att])])]), today=self.TODAY
        )
        assert records[0].attachments[0].name == "file_7"

    def test_identity_changes_with_attachments(self):
        plain = homework_from_day(
            day([lesson([assignment(content="Упр. 1")])]), today=self.TODAY
        )[0]
        with_file = homework_from_day(
            day([lesson([assignment(content="Упр. 1", attachments=[SimpleNamespace(id=1, name="a")])])]),
            today=self.TODAY,
        )[0]
        # Появившийся файл — повод сообщить заново.
        assert plain.identity != with_file.identity


class TestDiaryDays:
    def test_sorted_by_date(self):
        days = diary_days(
            [
                day([lesson([assignment()])], date=dt.date(2026, 3, 5)),
                day([lesson([assignment()])], date=dt.date(2026, 3, 2)),
            ],
            today=dt.date(2026, 3, 2),
        )
        assert [d.day for d in days] == [dt.date(2026, 3, 2), dt.date(2026, 3, 5)]

    def test_days_without_date_are_dropped(self):
        days = diary_days([SimpleNamespace(day=None, lessons=[])], today=dt.date(2026, 3, 2))
        assert days == []


class TestStudents:
    """Разбор `student/diary/init`.

    Формат ответа «Сетевого города» здесь неочевиден дважды: имя лежит в
    nickName, а currentStudentId в списочной форме — это индекс, а не
    идентификатор. Из-за второго профиль показывал «Ученик 583039».
    """

    def test_real_sgo_response(self):
        # Форма, которую отдаёт sgo.e-mordovia.ru: список, имя в nickName,
        # currentStudentId — индекс.
        students, current = students_from_diary_init(
            {
                "students": [{"studentId": 583039, "nickName": "Иванов Иван"}],
                "currentStudentId": 0,
            }
        )
        assert students == [{"id": 583039, "name": "Иванов Иван"}]
        assert current == 583039

    def test_index_selects_right_child(self):
        students, current = students_from_diary_init(
            {
                "students": [
                    {"studentId": 100, "nickName": "Первый"},
                    {"studentId": 200, "nickName": "Второй"},
                ],
                "currentStudentId": 1,
            }
        )
        assert current == 200

    def test_index_zero_is_not_treated_as_id(self):
        # Ноль — валидный индекс, но не идентификатор.
        _, current = students_from_diary_init(
            {"students": [{"studentId": 42, "nickName": "А"}], "currentStudentId": 0}
        )
        assert current == 42

    def test_id_form_wins_over_index_form(self):
        # Если значение совпадает с чьим-то studentId, это идентификатор,
        # а не индекс: иначе выбор уехал бы на другого ребёнка.
        _, current = students_from_diary_init(
            {
                "students": [
                    {"studentId": 1, "nickName": "Первый"},
                    {"studentId": 2, "nickName": "Второй"},
                ],
                "currentStudentId": 1,
            }
        )
        assert current == 1

    def test_out_of_range_index_falls_back_to_first(self):
        _, current = students_from_diary_init(
            {"students": [{"studentId": 42, "nickName": "А"}], "currentStudentId": 99}
        )
        assert current == 42

    @pytest.mark.parametrize(
        "field", ["nickName", "fio", "fullName", "name", "shortName"]
    )
    def test_all_known_name_fields(self, field):
        students, _ = students_from_diary_init(
            {"students": [{"studentId": 7, field: "Петров Пётр"}]}
        )
        assert students[0]["name"] == "Петров Пётр"

    def test_nickname_wins_over_others(self):
        students, _ = students_from_diary_init(
            {"students": [{"studentId": 7, "nickName": "Верное", "name": "Запасное"}]}
        )
        assert students[0]["name"] == "Верное"

    def test_dict_shape(self):
        students, current = students_from_diary_init(
            {"students": {"1": {"studentId": 10, "fio": "Иванов И."}}, "currentStudentId": 10}
        )
        assert students == [{"id": 10, "name": "Иванов И."}]
        assert current == 10

    def test_dict_shape_with_unknown_current(self):
        _, current = students_from_diary_init(
            {"students": {"0": {"studentId": 10, "fio": "А"}}, "currentStudentId": 999}
        )
        assert current == 10

    def test_missing_current_defaults_to_first(self):
        students, current = students_from_diary_init(
            {"students": [{"studentId": 12, "nickName": "А"}, {"studentId": 13, "nickName": "Б"}]}
        )
        assert current == 12

    def test_nameless_student_gets_placeholder(self):
        students, _ = students_from_diary_init({"students": [{"studentId": 14}]})
        assert students[0]["name"] == "Ученик 14"

    def test_blank_name_falls_through(self):
        students, _ = students_from_diary_init(
            {"students": [{"studentId": 14, "nickName": "   ", "fio": "Настоящее"}]}
        )
        assert students[0]["name"] == "Настоящее"

    def test_empty_payload(self):
        assert students_from_diary_init({}) == ([], None)

    def test_garbage_entries_are_skipped(self):
        students, _ = students_from_diary_init(
            {"students": ["мусор", {"studentId": 15, "nickName": "В"}]}
        )
        assert [s["id"] for s in students] == [15]

    def test_student_id_zero_is_kept(self):
        # 0 — допустимый studentId; `or` вместо явной проверки на None
        # молча подменял бы его индексом.
        students, _ = students_from_diary_init(
            {"students": [{"studentId": 0, "nickName": "Нулевой"}]}
        )
        assert students[0]["id"] == 0


class TestPlaceholderName:
    @pytest.mark.parametrize("name", ["Ученик 583039", "Ученик 1", "  Ученик 42  ", "", "   "])
    def test_detected(self, name):
        assert is_placeholder_name(name) is True

    @pytest.mark.parametrize(
        "name", ["Иванов Иван", "Ученик Иванов", "Ученица 5", "Ученик", "Ученик 5А"]
    )
    def test_real_names_untouched(self, name):
        assert is_placeholder_name(name) is False


class TestLessons:
    """Расписание дня.

    Оценки и задания привязываются к уроку сразу при разборе: один предмет
    может стоять в дне дважды, и связать их обратно по названию уже нельзя.
    """

    def test_lesson_fields(self):
        raw = lesson([assignment(mark=None, content="Упр. 5")], subject="Алгебра", number=2)
        raw.room = "301"
        raw.teacher = "Иванова М. П."
        lessons = lessons_from_day(day([raw]), today=dt.date(2026, 3, 2))
        assert len(lessons) == 1
        item = lessons[0]
        assert item.subject == "Алгебра"
        assert item.number == 2
        assert item.time_range == "09:00–09:45"
        assert item.room == "301"
        assert item.teacher == "Иванова М. П."

    def test_marks_attach_to_their_lesson(self):
        first = lesson([assignment(mark=5)], subject="Алгебра", number=1)
        second = lesson([assignment(mark=3)], subject="Химия", number=2)
        lessons = lessons_from_day(day([first, second]), today=dt.date(2026, 3, 2))
        assert [m.mark for m in lessons[0].marks] == ["5"]
        assert [m.mark for m in lessons[1].marks] == ["3"]

    def test_same_subject_twice_keeps_marks_apart(self):
        # Ровно та причина, по которой связывать по названию нельзя.
        first = lesson([assignment(mark=5)], subject="Физкультура", number=1)
        second = lesson([assignment(mark=2)], subject="Физкультура", number=6)
        lessons = lessons_from_day(day([first, second]), today=dt.date(2026, 3, 2))
        assert [m.mark for m in lessons[0].marks] == ["5"]
        assert [m.mark for m in lessons[1].marks] == ["2"]

    def test_homework_attaches_to_lesson(self):
        raw = lesson([assignment(mark=None, content="Упр. 7")], number=3)
        lessons = lessons_from_day(day([raw]), today=dt.date(2026, 3, 2))
        assert [h.text for h in lessons[0].homework] == ["Упр. 7"]

    def test_lessons_sorted_by_number(self):
        raw = [
            lesson([assignment(mark=None, content="")], subject="Химия", number=3),
            lesson([assignment(mark=None, content="")], subject="Алгебра", number=1),
        ]
        lessons = lessons_from_day(day(raw), today=dt.date(2026, 3, 2))
        assert [item.number for item in lessons] == [1, 3]

    def test_missing_number_falls_back_to_position(self):
        raw = lesson([assignment(mark=None, content="")], number=None)
        lessons = lessons_from_day(day([raw]), today=dt.date(2026, 3, 2))
        assert lessons[0].number == 1

    def test_diary_days_include_lessons(self):
        days = diary_days([day([lesson([assignment()])])], today=dt.date(2026, 3, 2))
        assert len(days[0].lessons) == 1
