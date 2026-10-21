"""Каникулы и экзамены для отсчёта на «Дне» и тихого режима."""

import datetime as dt

from app.domain.periods import Quarter
from app.domain.vacations import (
    Vacation, exam_for, grade_of, next_vacation, on_vacation, refine_by_diary, school_vacations, vacations,
)


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


def test_half_years_without_gaps_still_have_autumn_and_spring():
    """Полугодия встык: школа не показывает осенние и весенние каникулы,
    отсчёт всё равно ведёт к ближайшим, а не к летним."""
    terms = [
        Quarter("h1", "1 полугодие", dt.date(2026, 9, 1), dt.date(2026, 12, 28)),
        Quarter("h2", "2 полугодие", dt.date(2027, 1, 11), dt.date(2027, 5, 26)),
    ]
    items = vacations(dt.date(2026, 10, 21), terms, "school")
    autumn = next_vacation(dt.date(2026, 10, 21), items)
    assert autumn.label == "Осенние каникулы" and not autumn.exact
    winter = on_vacation(dt.date(2027, 1, 5), items)
    assert winter.label == "Зимние каникулы" and winter.exact and winter.start == dt.date(2026, 12, 29)
    assert on_vacation(dt.date(2027, 4, 1), items).label == "Весенние каникулы"
    assert [item.label for item in items].count("Зимние каникулы") == 1


def test_weekend_between_periods_is_not_vacation():
    terms = [
        Quarter("t1", "1 триместр", dt.date(2026, 9, 1), dt.date(2026, 11, 27)),
        Quarter("t2", "2 триместр", dt.date(2026, 11, 30), dt.date(2027, 2, 26)),
    ]
    assert all(item.label != "Осенние каникулы" or item.start.month == 10
               for item in school_vacations(terms))
    assert not any(item.start == dt.date(2026, 11, 28) for item in school_vacations(terms))


def _diary(first: dt.date, last: dt.date, free: tuple[dt.date, dt.date]) -> dict[dt.date, list[bool]]:
    """Будни с уроками; в дни `free` — только внеурочка."""
    days, day = {}, first
    while day <= last:
        if day.weekday() < 5:
            days[day] = [True] if free[0] <= day <= free[1] else [False, False, True]
        day += dt.timedelta(days=1)
    return days


def test_diary_with_only_electives_gives_exact_vacation_dates():
    guess = Vacation("Осенние каникулы", dt.date(2026, 10, 25), dt.date(2026, 11, 3), exact=False)
    # Школа ушла на каникулы со 2 по 8 ноября, а не с 25 октября.
    diary = _diary(dt.date(2026, 10, 18), dt.date(2026, 11, 10), (dt.date(2026, 11, 2), dt.date(2026, 11, 6)))
    found = refine_by_diary(guess, diary)
    assert (found.start, found.end, found.exact) == (dt.date(2026, 10, 31), dt.date(2026, 11, 8), True)


def test_diary_without_electives_only_days_keeps_guess():
    guess = Vacation("Осенние каникулы", dt.date(2026, 10, 25), dt.date(2026, 11, 3), exact=False)
    assert refine_by_diary(guess, {}) == guess
    # Дневник заполнен только до каникул — даты не трогаем.
    diary = _diary(dt.date(2026, 10, 18), dt.date(2026, 10, 23), (dt.date(2000, 1, 1), dt.date(2000, 1, 1)))
    assert refine_by_diary(guess, diary) == guess


def test_diary_filled_only_into_vacation_keeps_guessed_end():
    guess = Vacation("Осенние каникулы", dt.date(2026, 10, 25), dt.date(2026, 11, 3), exact=False)
    diary = _diary(dt.date(2026, 10, 18), dt.date(2026, 10, 28), (dt.date(2026, 10, 26), dt.date(2026, 10, 28)))
    found = refine_by_diary(guess, diary)
    assert (found.start, found.end, found.exact) == (dt.date(2026, 10, 24), dt.date(2026, 11, 3), False)


def test_saturday_club_is_not_vacation():
    guess = Vacation("Осенние каникулы", dt.date(2026, 10, 25), dt.date(2026, 11, 3), exact=False)
    diary = _diary(dt.date(2026, 10, 18), dt.date(2026, 11, 10), (dt.date(2000, 1, 1), dt.date(2000, 1, 1)))
    diary[dt.date(2026, 10, 24)] = [True]
    assert refine_by_diary(guess, diary) == guess


def test_exam_only_for_graduating_classes():
    assert grade_of("11 Б") == 11 and grade_of("9А") == 9 and grade_of("") is None
    assert exam_for("10А", dt.date(2026, 10, 21)) is None
    name, start, confirmed = exam_for("9А", dt.date(2026, 10, 21))
    assert name == "ОГЭ" and start.year == 2027 and not confirmed
    name, start, _ = exam_for("11 Б", dt.date(2026, 10, 21))
    assert name == "ЕГЭ" and start == dt.date(2027, 6, 1)
