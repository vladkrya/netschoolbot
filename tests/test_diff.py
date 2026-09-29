"""Тесты сравнения оценок — логика, из-за которой приходили ложные
уведомления «оценка удалена» и терялись реальные изменения."""

import datetime as dt

import pytest

from app.domain.models import Filters, MarkKind, normalize
from app.domain.records import MarkRecord, TrackedMark
from app.notifications.diff import (
    MISSING_STREAK_FOR_DELETE,
    confirm_deletions,
    diff_marks,
    response_is_complete,
)


def mark(subject="Алгебра", date=dt.date(2026, 3, 2), value="5", **kwargs):
    return MarkRecord(
        subject=subject,
        date=date,
        assignment_type=kwargs.pop("assignment_type", "Контрольная работа"),
        content=kwargs.pop("content", "Дроби"),
        mark=value,
        lesson_number=kwargs.pop("lesson_number", 3),
        **kwargs,
    )


def tracked(records, streak=0):
    return {r.identity: TrackedMark(record=r, missing_streak=streak) for r in records}


class TestIdentity:
    def test_identical_records_share_key(self):
        assert mark().identity == mark().identity

    def test_different_mark_keeps_same_identity(self):
        # Идентичность работы не зависит от оценки — иначе исправление
        # тройки на пятёрку выглядело бы как удаление плюс новая оценка.
        assert mark(value="3").identity == mark(value="5").identity

    def test_subject_with_separator_does_not_collide(self):
        # Старый код склеивал поля через "_", поэтому предмет с подчёркиванием
        # мог дать тот же ключ, что и другая работа.
        a = mark(subject="Физика_1", assignment_type="Тест")
        b = mark(subject="Физика", assignment_type="1_Тест")
        assert a.identity != b.identity

    def test_index_distinguishes_lessons(self):
        assert mark(assignment_index=0).identity != mark(assignment_index=1).identity

    def test_loose_identity_ignores_index(self):
        assert mark(assignment_index=0).loose_identity == mark(assignment_index=1).loose_identity


class TestFirstRun:
    def test_nothing_is_announced_on_first_run(self):
        # Иначе при подключении человек получает весь журнал за год.
        current = [mark(value="5"), mark(subject="Химия", value="4")]
        result = diff_marks({}, current, filters=Filters(), first_run=True)
        assert result.events == []
        assert len(result.tracked) == 2

    def test_first_run_remembers_everything(self):
        current = [mark()]
        result = diff_marks({}, current, filters=Filters(), first_run=True)
        assert mark().identity in result.tracked


class TestNewMarks:
    def test_new_mark_is_reported(self):
        result = diff_marks({}, [mark()], filters=Filters(), first_run=False)
        assert [e.kind for e in result.events] == [MarkKind.NEW]

    def test_known_mark_is_silent(self):
        known = tracked([mark()])
        result = diff_marks(known, [mark()], filters=Filters(), first_run=False)
        assert result.events == []

    def test_new_marks_are_capped(self):
        current = [mark(assignment_index=i) for i in range(200)]
        # Известна одна оценка, чтобы прогон не считался первым и ответ не
        # выглядел обрезанным.
        known = tracked([current[0]])
        result = diff_marks(known, current, filters=Filters(), first_run=False)
        assert len(result.events) <= 50


class TestChangedMarks:
    def test_changed_mark_reports_both_values(self):
        known = tracked([mark(value="3")])
        result = diff_marks(known, [mark(value="5")], filters=Filters(), first_run=False)
        assert len(result.events) == 1
        event = result.events[0]
        assert event.kind is MarkKind.CHANGED
        assert (event.old_mark, event.new_mark) == ("3", "5")

    def test_changes_can_be_disabled(self):
        known = tracked([mark(value="3")])
        result = diff_marks(
            known, [mark(value="5")], filters=Filters(), first_run=False, notify_changes=False
        )
        assert result.events == []
        # Но новое значение всё равно запоминается, иначе изменение всплывёт позже.
        assert result.tracked[mark().identity].record.mark == "5"


class TestDeletions:
    def test_single_disappearance_is_not_a_deletion(self):
        # Главный источник ложных «оценка удалена» в старой версии.
        known = tracked([mark(assignment_index=i) for i in range(10)])
        current = [mark(assignment_index=i) for i in range(1, 10)]
        result = diff_marks(known, current, filters=Filters(), first_run=False)
        assert result.pending_deletes == []

    def test_deletion_after_streak(self):
        missing = mark(assignment_index=0)
        others = [mark(assignment_index=i) for i in range(1, 10)]
        known = tracked(others)
        known[missing.identity] = TrackedMark(
            record=missing, missing_streak=MISSING_STREAK_FOR_DELETE - 1
        )
        result = diff_marks(known, others, filters=Filters(), first_run=False)
        assert [r.identity for r in result.pending_deletes] == [missing.identity]

    def test_truncated_response_never_deletes(self):
        # Сервер отдал два дня вместо тридцати: это сбой, а не чистка журнала.
        known = tracked([mark(assignment_index=i) for i in range(40)], streak=5)
        current = [mark(assignment_index=0)]
        result = diff_marks(known, current, filters=Filters(), first_run=False)
        assert result.response_looked_truncated is True
        assert result.pending_deletes == []

    def test_truncated_response_preserves_streaks(self):
        known = tracked([mark(assignment_index=i) for i in range(40)], streak=2)
        current = [mark(assignment_index=0)]
        result = diff_marks(known, current, filters=Filters(), first_run=False)
        # Счётчики не растут на обрезанном ответе.
        assert all(t.missing_streak <= 2 for t in result.tracked.values())

    def test_unconfirmed_deletion_resets_streak(self):
        missing = mark(assignment_index=0)
        others = [mark(assignment_index=i) for i in range(1, 10)]
        known = tracked(others)
        known[missing.identity] = TrackedMark(
            record=missing, missing_streak=MISSING_STREAK_FOR_DELETE - 1
        )
        result = diff_marks(known, others, filters=Filters(), first_run=False)
        events = confirm_deletions(result, confirmed_keys=set())
        assert events == []
        assert result.tracked[missing.identity].missing_streak == 0

    def test_confirmed_deletion_is_forgotten(self):
        missing = mark(assignment_index=0)
        others = [mark(assignment_index=i) for i in range(1, 10)]
        known = tracked(others)
        known[missing.identity] = TrackedMark(
            record=missing, missing_streak=MISSING_STREAK_FOR_DELETE - 1
        )
        result = diff_marks(known, others, filters=Filters(), first_run=False)
        events = confirm_deletions(result, confirmed_keys={missing.identity})
        assert [e.kind for e in events] == [MarkKind.DELETED]
        assert missing.identity not in result.tracked

    def test_deletes_are_capped(self):
        gone = [mark(assignment_index=i) for i in range(30)]
        stay = [mark(subject="Химия", assignment_index=i) for i in range(100)]
        known = tracked(gone + stay, streak=MISSING_STREAK_FOR_DELETE - 1)
        result = diff_marks(known, stay, filters=Filters(), first_run=False)
        assert len(result.pending_deletes) <= 10


class TestFilters:
    def test_excluded_title_is_ignored(self):
        filters = Filters(exclude_titles=frozenset({normalize("Ответ на уроке")}))
        record = mark(assignment_type="Ответ на уроке", content="")
        result = diff_marks({}, [record], filters=filters, first_run=False)
        assert result.events == []
        assert result.tracked == {}

    def test_subject_whitelist(self):
        filters = Filters(include_subjects=frozenset({normalize("Алгебра")}))
        current = [mark(subject="Алгебра"), mark(subject="Химия")]
        result = diff_marks({}, current, filters=filters, first_run=False)
        assert {e.record.subject for e in result.events} == {"Алгебра"}

    def test_empty_whitelist_allows_everything(self):
        current = [mark(subject="Алгебра"), mark(subject="Химия")]
        result = diff_marks({}, current, filters=Filters(), first_run=False)
        assert len(result.events) == 2


class TestCompleteness:
    @pytest.mark.parametrize(
        "known,current,expected",
        [
            (0, 0, True),      # нет истории — сравнивать не с чем
            (100, 100, True),
            (100, 70, True),   # ровно на границе
            (100, 69, False),
            (4, 1, False),     # маленькие журналы защищены абсолютным минимумом
            (0, 50, True),
        ],
    )
    def test_completeness(self, known, current, expected):
        assert response_is_complete(known, current) is expected


class TestMarkParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [("5", 5), ("4", 4), ("зачтено", None), ("", None), ("н/а", None), ("11", None), ("10", 10)],
    )
    def test_numeric_mark(self, raw, expected):
        assert mark(value=raw).numeric_mark == expected


def test_first_run_drops_state_that_is_gone_from_the_new_answer():
    """Смена API: ключи оценок другие, старые записи не должны дожить до «удалений»."""
    legacy = {
        "web-key": TrackedMark(record=mark("Химия", value="4"), missing_streak=2),
    }
    current = [mark("Физика", value="5")]

    result = diff_marks(legacy, current, filters=Filters(), first_run=True)

    assert result.events == [] and result.pending_deletes == []
    assert set(result.tracked) == {current[0].identity}
