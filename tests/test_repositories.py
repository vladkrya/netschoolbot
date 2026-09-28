"""Тесты хранилища.

Отдельное внимание — параллельной записи: в старой версии бот и веб писали
общий словарь и JSON-файл из двух потоков, и настройки регулярно терялись.
"""

import asyncio
import datetime as dt
from dataclasses import replace

import pytest

from app.db.repositories import UserRepository
from app.domain.models import (
    Credentials,
    Filters,
    LoginType,
    NotificationPrefs,
    QuietHours,
    School,
    Student,
    User,
    normalize,
)
from app.domain.records import MarkRecord, TrackedMark


def make_user(telegram_id=1, **kwargs) -> User:
    defaults = dict(
        telegram_id=telegram_id,
        school=School(url="https://sgo.example.ru", name="Школа №1"),
        credentials=Credentials(login_type=LoginType.PASSWORD, login="ivan", password="secret"),
        enabled=True,
    )
    defaults.update(kwargs)
    return User(**defaults)


class TestUserRepository:
    async def test_missing_user_is_none(self, users):
        assert await users.get(999) is None

    async def test_get_or_create_is_idempotent(self, users):
        first = await users.get_or_create(1, display_name="Иван")
        second = await users.get_or_create(1, display_name="Другое имя")
        assert first.telegram_id == second.telegram_id
        assert second.display_name == "Иван"

    async def test_roundtrip_preserves_everything(self, users):
        user = make_user(
            student_name="Петров Пётр",
            selected_student_id=42,
            available_students=(Student(42, "Петров Пётр"), Student(43, "Петрова Анна")),
            check_interval=600,
            notifications=NotificationPrefs(
                grades=True, changes=False, deletes=True, homework=False, mail=True,
                weekly_summary=True,
            ),
            filters=Filters(
                exclude_titles=frozenset({normalize("Ответ на уроке")}),
                include_subjects=frozenset({normalize("Алгебра"), normalize("Химия")}),
            ),
            quiet_hours=QuietHours(start=dt.time(22, 0), end=dt.time(7, 0)),
        )
        await users.save(user)
        loaded = await users.get(1)

        assert loaded is not None
        assert loaded.school == user.school
        assert loaded.credentials == user.credentials
        assert loaded.available_students == user.available_students
        assert loaded.notifications == user.notifications
        assert loaded.filters == user.filters
        assert loaded.quiet_hours == user.quiet_hours
        assert loaded.check_interval == 600

    async def test_interval_is_clamped_on_save(self, users):
        await users.save(make_user(check_interval=5))
        loaded = await users.get(1)
        assert loaded.check_interval == 180

    async def test_all_active_skips_unconfigured(self, users):
        await users.save(make_user(1))
        await users.save(make_user(2, enabled=False))
        await users.save(make_user(3, school=School(url="", name="")))
        assert [u.telegram_id for u in await users.all_active()] == [1]

    async def test_students_are_replaced_not_appended(self, users):
        user = make_user(available_students=(Student(1, "А"), Student(2, "Б")))
        await users.save(user)
        await users.save(replace(user, available_students=(Student(3, "В"),)))
        loaded = await users.get(1)
        assert loaded.available_students == (Student(3, "В"),)

    async def test_filters_are_replaced_not_appended(self, users):
        user = make_user(filters=Filters(exclude_titles=frozenset({"а", "б"})))
        await users.save(user)
        await users.save(replace(user, filters=Filters(exclude_titles=frozenset({"в"}))))
        loaded = await users.get(1)
        assert loaded.filters.exclude_titles == frozenset({"в"})

    async def test_delete_cascades(self, users, marks, db):
        await users.save(make_user())
        await marks.replace_homework(1, {"hw-1"})
        await users.delete(1)
        assert await marks.load_homework(1) == set()

    async def test_concurrent_writes_do_not_lose_data(self, db):
        """Ровно та ситуация, в которой старый код терял настройки."""
        repo = UserRepository(db)
        await repo.save(make_user())

        async def set_interval(seconds: int) -> None:
            user = await repo.get(1)
            await repo.save(replace(user, check_interval=seconds))

        # 20 конкурентных обновлений: последнее должно победить целиком,
        # а база — остаться консистентной (не смесь двух состояний).
        await asyncio.gather(*(set_interval(180 + i * 60) for i in range(20)))
        loaded = await repo.get(1)
        assert loaded is not None
        assert 180 <= loaded.check_interval <= 180 + 19 * 60

    async def test_concurrent_distinct_users(self, db):
        repo = UserRepository(db)
        await asyncio.gather(*(repo.save(make_user(i)) for i in range(1, 31)))
        assert len(await repo.all_active()) == 30


class TestMarkState:
    def record(self, index=0, value="5"):
        return MarkRecord(
            subject="Алгебра",
            date=dt.date(2026, 3, 2),
            assignment_type="Контрольная",
            content="Дроби",
            mark=value,
            assignment_index=index,
        )

    async def test_history_flag(self, users, marks):
        await users.save(make_user())
        assert await marks.has_history(1) is False
        await marks.replace_marks(1, {self.record().identity: TrackedMark(self.record())})
        assert await marks.has_history(1) is True

    async def test_roundtrip(self, users, marks):
        await users.save(make_user())
        record = self.record()
        await marks.replace_marks(1, {record.identity: TrackedMark(record, missing_streak=2)})
        loaded = await marks.load_marks(1)
        assert loaded[record.identity].record == record
        assert loaded[record.identity].missing_streak == 2

    async def test_replace_removes_absent(self, users, marks):
        await users.save(make_user())
        a, b = self.record(0), self.record(1)
        await marks.replace_marks(1, {a.identity: TrackedMark(a), b.identity: TrackedMark(b)})
        await marks.replace_marks(1, {a.identity: TrackedMark(a)})
        assert set(await marks.load_marks(1)) == {a.identity}

    async def test_forget_all(self, users, marks):
        await users.save(make_user())
        record = self.record()
        await marks.replace_marks(1, {record.identity: TrackedMark(record)})
        await marks.replace_homework(1, {"hw"})
        await marks.mark_mail_seen(1, {7})
        await marks.forget_all(1)
        assert await marks.load_marks(1) == {}
        assert await marks.load_homework(1) == set()
        assert await marks.seen_mail_ids(1) == set()

    async def test_homework_observation_is_preserved(self, users, marks):
        await users.save(make_user())
        await marks.replace_homework(1, {"old"})
        assert await marks.observed_homework(1) == {}
        await marks.replace_homework(1, {"old", "new"}, {"new"})
        first = (await marks.observed_homework(1))["new"]
        assert first.endswith("Z")
        await marks.replace_homework(1, {"new"})
        assert await marks.observed_homework(1) == {"new": first}

    async def test_mail_ids_deduplicate(self, users, marks):
        await users.save(make_user())
        await marks.mark_mail_seen(1, {1, 2})
        await marks.mark_mail_seen(1, {2, 3})
        assert await marks.seen_mail_ids(1) == {1, 2, 3}


class TestMiniapp:
    async def test_token_is_reused(self, users, miniapp):
        await users.save(make_user())
        first = await miniapp.issue_token(1)
        assert await miniapp.issue_token(1) == first

    async def test_revoke_issues_new_token(self, users, miniapp):
        await users.save(make_user())
        first = await miniapp.issue_token(1)
        second = await miniapp.issue_token(1, revoke_existing=True)
        assert first != second
        assert await miniapp.resolve_token(first) is None
        assert await miniapp.resolve_token(second) == 1

    async def test_unknown_token_resolves_to_none(self, miniapp):
        assert await miniapp.resolve_token("нет-такого") is None
        assert await miniapp.resolve_token("") is None

    async def test_login_code_is_single_use(self, users, miniapp):
        await users.save(make_user())
        code = await miniapp.issue_login_code(1)
        assert await miniapp.consume_login_code(code) == 1
        assert await miniapp.consume_login_code(code) is None

    async def test_new_code_invalidates_previous(self, users, miniapp):
        await users.save(make_user())
        old = await miniapp.issue_login_code(1)
        await miniapp.issue_login_code(1)
        assert await miniapp.consume_login_code(old) is None

    async def test_push_subscription_upsert(self, users, miniapp):
        await users.save(make_user())
        await miniapp.add_push_subscription(1, "https://push/1", "key", "auth")
        await miniapp.add_push_subscription(1, "https://push/1", "key2", "auth2")
        subs = await miniapp.push_subscriptions(1)
        assert len(subs) == 1
        assert subs[0]["p256dh"] == "key2"

    async def test_dead_subscription_is_dropped_after_failures(self, users, miniapp):
        await users.save(make_user())
        await miniapp.add_push_subscription(1, "https://push/1", "key", "auth")
        for _ in range(5):
            await miniapp.note_push_failure("https://push/1", drop_after=5)
        assert await miniapp.push_subscriptions(1) == []


class TestCache:
    async def test_roundtrip(self, users, cache):
        await users.save(make_user())
        await cache.put(1, "diary", {"days": [1, 2, 3]})
        payload, updated = await cache.get(1, "diary")
        assert payload == {"days": [1, 2, 3]}
        assert isinstance(updated, dt.datetime)

    async def test_missing_section(self, users, cache):
        await users.save(make_user())
        assert await cache.get(1, "нет") is None

    async def test_overwrite(self, users, cache):
        await users.save(make_user())
        await cache.put(1, "diary", {"v": 1})
        await cache.put(1, "diary", {"v": 2})
        payload, _ = await cache.get(1, "diary")
        assert payload == {"v": 2}


class TestSessions:
    async def test_roundtrip_and_drop(self, users, sessions):
        await users.save(make_user())
        await sessions.save(1, '{"cookies": "..."}')
        assert await sessions.load(1) == '{"cookies": "..."}'
        await sessions.drop(1)
        assert await sessions.load(1) is None
