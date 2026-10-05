"""Тесты разбивки на четверти.

Границы приблизительные — школа своих не отдаёт, — но раскладка должна
быть непрерывной и не терять ни одного учебного дня.
"""

import datetime as dt

import pytest

from app.domain.periods import (
    QUARTER_KEYS,
    academic_year_start,
    current_quarter,
    quarter_of,
    quarters,
)


class TestAcademicYear:
    @pytest.mark.parametrize(
        "day,expected",
        [
            (dt.date(2026, 9, 1), 2026),
            (dt.date(2026, 12, 31), 2026),
            (dt.date(2027, 1, 1), 2026),
            (dt.date(2027, 5, 30), 2026),
            (dt.date(2027, 8, 31), 2026),
            (dt.date(2027, 9, 1), 2027),
        ],
    )
    def test_year_boundary_is_september(self, day, expected):
        assert academic_year_start(day) == expected


class TestQuarters:
    def test_four_quarters(self):
        assert [q.key for q in quarters(dt.date(2026, 10, 1))] == list(QUARTER_KEYS)

    def test_ranges_are_continuous(self):
        items = quarters(dt.date(2026, 10, 1))
        for earlier, later in zip(items, items[1:]):
            # Между четвертями не должно быть дыр: иначе оценка,
            # выставленная в этот день, потерялась бы в калькуляторе.
            assert earlier.end + dt.timedelta(days=1) == later.start

    def test_third_quarter_is_in_next_calendar_year(self):
        third = next(q for q in quarters(dt.date(2026, 10, 1)) if q.key == "q3")
        assert third.start == dt.date(2027, 1, 9)


class TestQuarterOf:
    @pytest.mark.parametrize(
        "day,expected",
        [
            (dt.date(2026, 9, 15), "q1"),
            (dt.date(2026, 10, 31), "q1"),
            (dt.date(2026, 11, 1), "q2"),
            (dt.date(2026, 12, 20), "q2"),
            (dt.date(2027, 1, 9), "q3"),
            (dt.date(2027, 3, 20), "q3"),
            (dt.date(2027, 4, 1), "q4"),
            (dt.date(2027, 5, 25), "q4"),
        ],
    )
    def test_mapping(self, day, expected):
        assert quarter_of(day) == expected

    def test_winter_holidays_belong_to_second_quarter(self):
        # 1 января — ещё вторая четверть: третья начинается 9-го.
        assert quarter_of(dt.date(2027, 1, 3)) == "q2"


class TestCurrentQuarter:
    def test_returns_something_even_in_summer(self):
        # Экран калькулятора должен открываться и на каникулах.
        assert current_quarter(dt.date(2027, 7, 15)) in QUARTER_KEYS

    def test_normal_day(self):
        assert current_quarter(dt.date(2026, 11, 20)) == "q2"


class TestSchemes:
    def test_trimesters_cover_year_without_gaps(self):
        from app.domain.periods import PeriodScheme, periods

        year = periods(dt.date(2026, 10, 5), PeriodScheme.TRIMESTERS)
        assert [p.key for p in year] == ["t1", "t2", "t3"]
        assert [p.short for p in year] == ["1/3", "2/3", "3/3"]
        for left, right in zip(year, year[1:]):
            assert left.end + dt.timedelta(days=1) == right.start
        assert year[-1].end == dt.date(2027, 8, 31)

    def test_custom_starts(self):
        from app.domain.periods import PeriodScheme, period_of, periods

        year = periods(dt.date(2026, 10, 5), PeriodScheme.HALVES, [(9, 1), (12, 29)])
        assert year[1].start == dt.date(2026, 12, 29)
        assert period_of(dt.date(2026, 12, 30), year) == "h2"

    def test_wrong_number_of_starts_falls_back(self):
        from app.domain.periods import PeriodScheme, periods

        assert periods(dt.date(2026, 10, 5), PeriodScheme.HALVES, [(9, 1)])[1].start == dt.date(2027, 1, 9)

    def test_term_names(self):
        from app.domain.periods import term_key

        assert term_key("2 триместр") == "t2"
        assert term_key("1-е полугодие") == "h1"
        assert term_key("2-я четверть") == "q2"
        assert term_key("3 полугодие") is None


class TestSchoolTerms:
    TERMS = [
        {"name": "1 четверть", "startDate": "2026-09-01T00:00:00", "endDate": "2026-10-24T00:00:00"},
        {"name": "2 четверть", "startDate": "2026-11-05T00:00:00", "endDate": "2026-12-28T00:00:00"},
        {"name": "3 четверть", "startDate": "2027-01-12T00:00:00", "endDate": "2027-03-20T00:00:00"},
        {"name": "4 четверть", "startDate": "2027-03-30T00:00:00", "endDate": "2027-05-26T00:00:00"},
        {"name": "1 полугодие", "startDate": "2026-09-01T00:00:00", "endDate": "2026-12-28T00:00:00"},
    ]

    def test_most_detailed_scheme_wins_and_holidays_join_previous(self):
        from app.domain.periods import period_of, periods_from_terms

        year = periods_from_terms(self.TERMS, dt.date(2026, 10, 5))
        assert [p.key for p in year] == ["q1", "q2", "q3", "q4"]
        # Осенние каникулы — ещё первая четверть.
        assert period_of(dt.date(2026, 10, 30), year) == "q1"
        assert year[-1].end == dt.date(2027, 5, 26)

    def test_incomplete_terms_give_none(self):
        from app.domain.periods import periods_from_terms

        assert periods_from_terms(self.TERMS[:2], dt.date(2026, 10, 5)) is None
