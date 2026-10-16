"""Повторный вход в «Сетевой город» не стирает отметки ученика."""
from __future__ import annotations

from dataclasses import replace

from app.domain.models import School
from app.netschool import login as login_flow
from tests.test_bot_handlers import app  # noqa: F401 — фикстура
from tests.test_repositories import make_user


async def _relogin(app, users, candidate):
    async def sync_students(user):
        return user

    app.diary.sync_students = sync_students
    try:
        return await login_flow.finish_login(app, candidate, None)
    finally:
        await app.watchers.stop(candidate.telegram_id)


async def test_same_student_keeps_done_homework(app, users):
    user = await users.save(make_user(selected_student_id=5))
    await app.state.set_homework_done(user.telegram_id, "hw:1", True)
    await _relogin(app, users, user)
    assert await app.state.done_homework(user.telegram_id) == {"hw:1"}
    # Накопившееся за время без входа — молча, без потока уведомлений.
    assert await app.state.is_baseline_pending(user.telegram_id)


async def test_other_student_or_school_starts_clean(app, users):
    user = await users.save(make_user(selected_student_id=5))
    for candidate in (replace(user, selected_student_id=6),
                      replace(user, school=School(url="https://other.example.ru", name="Школа №2"))):
        await app.state.set_homework_done(user.telegram_id, "hw:1", True)
        await users.save(user)
        await _relogin(app, users, candidate)
        assert await app.state.done_homework(user.telegram_id) == set()
