"""Каникулы и экзамены для отсчёта на «Дне» и тихого режима."""

import datetime as dt

from app.domain.periods import Quarter
from app.domain.vacations import exam_for, grade_of, next_vacation, on_vacation, school_vacations, vacations


def test_default_vacations_of_the_year():
    items = vacations(dt.date(2026, 10, 21))
    autumn = next_vacation(dt.date(2026, 10, 21), items)
    assert autumn.label == "Осенние каникулы" and autumn.start == dt.date(2026, 10, 25)
    assert on_vacation(dt.date(2026, 11, 1), items).label == "Осенние каникулы"
    assert on_vacation(dt.date(2027, 1, 5), items).label == "Зимние каникулы"
    assert on_vacation(dt.date(2026, 11, 10), items) is None
    # Август — ещё летние каникулы прошлого учебного года.
    assert on_vacation(dt.date(2026, 8, 20), vacations(dt.date(2026, 8, 20))).label == "Летние каникулы"


def test_school_terms_gaps_are_vacations():
    terms = [
        Quarter("q1", "1 четверть", dt.date(2026, 9, 1), dt.date(2026, 10, 23)),
        Quarter("q2", "2 четверть", dt.date(2026, 11, 2), dt.date(2026, 12, 27)),
        Quarter("q3", "3 четверть", dt.date(2027, 1, 11), dt.date(2027, 3, 20)),
        Quarter("q4", "4 четверть", dt.date(2027, 3, 30), dt.date(2027, 5, 26)),
    ]
    found = school_vacations(terms)
    assert [(v.label, v.start, v.end) for v in found] == [
        ("Осенние каникулы", dt.date(2026, 10, 24), dt.date(2026, 11, 1)),
        ("Зимние каникулы", dt.date(2026, 12, 28), dt.date(2027, 1, 10)),
        ("Весенние каникулы", dt.date(2027, 3, 21), dt.date(2027, 3, 29)),
        ("Летние каникулы", dt.date(2027, 5, 27), dt.date(2027, 8, 31)),
    ]
    assert vacations(dt.date(2026, 10, 21), terms, "school") == found


def test_exam_only_for_graduating_classes():
    assert grade_of("11 Б") == 11 and grade_of("9А") == 9 and grade_of("") is None
    assert exam_for("10А", dt.date(2026, 10, 21)) is None
    name, start, confirmed = exam_for("9А", dt.date(2026, 10, 21))
    assert name == "ОГЭ" and start.year == 2027 and not confirmed
    name, start, _ = exam_for("11 Б", dt.date(2026, 10, 21))
    assert name == "ЕГЭ" and start == dt.date(2027, 6, 1)
