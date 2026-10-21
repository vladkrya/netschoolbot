"""Факты для разбора недели: числа считает код, а не модель."""

import datetime as dt

from app.domain.records import HomeworkRecord
from app.domain.review import clean_review, review_facts
from tests.test_watcher import record


def test_facts_name_weak_subjects_and_tests():
    week = [record(0, "2", dt.date(2026, 3, 4), "Алгебра"), record(1, "5", dt.date(2026, 3, 5), "Физика")]
    period = week + [record(2, "3", dt.date(2026, 2, 20), "Алгебра"), record(3, "5", dt.date(2026, 2, 21), "Физика")]
    tests = [HomeworkRecord("Химия", dt.date(2026, 3, 11), "Контрольная работа", "", weight=3)]
    facts = review_facts(week, period, None, tests, period_label="3 четверть")
    assert "- Алгебра: 2" in facts
    assert "Алгебра: средний 2,50, выходит «3», за неделю упал на 0,50" in facts
    assert "до «4» нужно ещё 2 пятёрки" in facts
    assert "Физика" not in facts.split("внимания:")[1].split("Проверочные")[0]
    assert "11.03, Химия: Контрольная работа (вес 3)" in facts


def test_nothing_to_review():
    assert review_facts([], [], None, []) is None


def test_clean_review_strips_markdown_and_length():
    assert clean_review("**Важно**: учить.") == "Важно: учить."
    long = "Предложение. " * 200
    assert len(clean_review(long)) <= 900
