"""Изоляция школ, минимизация расписания и общие правки."""
import datetime as dt
import json
from dataclasses import replace
from types import SimpleNamespace

from app import moderation as mod
from app.domain.records import DiaryDay, Lesson
from tests.test_admin_screen import client, app_context  # noqa: F401
from tests.test_repositories import make_user


async def test_roles_announcements_and_partial_bells(client, app_context, users, monkeypatch):
    for user_id in range(1, 6):
        await users.save(make_user(user_id))
    headers = {i: {"X-Netschool-Token": await app_context.miniapp.issue_token(i)} for i in range(1, 6)}
    actor = await users.get(1)
    date = actor.today()
    year = mod.school_year(date)
    school = {"installation": "sgo.example.ru", "school_id": 17}
    eight = mod.Scope(*school.values(), year, "id:8", "8А")
    nine = mod.Scope(*school.values(), year, "id:9", "9Б")
    other = replace(eight, school_id=18)

    async def scope_for(app, user):
        return {2: eight, 3: eight, 4: nine, 5: other}.get(user.telegram_id)

    monkeypatch.setattr(mod, "scope_for", scope_for)
    root = "/mini/api/moderation"
    enable = {**school, "title": "Школа"}
    assert (await client.post(root + "/schools", headers=headers[2], json=enable)).status == 403
    assert (await client.post(root + "/schools", headers=headers[1], json=enable)).status == 200
    assert (await client.post(root + "/schools", headers=headers[1], json={**enable, "school_id": 18})).status == 200

    async def grant(identity, permissions, class_key=""):
        account_no = (await app_context.db.fetch_one(
            "SELECT account_no FROM users WHERE telegram_id=?", (identity,),
        ))["account_no"]
        return await client.post(root + "/roles", headers=headers[1], json={
            **school, "account_no": account_no, "permissions": permissions, "class_key": class_key,
        })

    assert (await grant(5, ["read"])).status == 400
    assert (await grant(2, ["bells"], eight.class_key)).status == 400
    assert (await grant(2, ["read", "lessons", "announce"], eight.class_key)).status == 200
    assert (await grant(4, ["read", "bells", "reports"])).status == 200

    physics = Lesson(3, "Физика", "10:30", "11:15", "12", teacher="Личное ФИО", topic="Личная тема")
    await mod.observe(app_context, eight, [DiaryDay(date, [physics])], date)
    await mod.observe(app_context, eight, [DiaryDay(date, [Lesson(3, "Английский", "10:30", "11:15", "16")])], date)
    await mod.observe(app_context, nine, [DiaryDay(date, [Lesson(4, "Химия", "11:25", "12:10", "14")])], date)
    dashboard = await client.get(root, headers=headers[2])
    data = (await dashboard.json())["data"]
    assert {lesson["subject"] for lesson in data["lessons"]} == {"Физика", "Английский"}
    assert {item["class_key"] for item in data["classes"]} == {eight.class_key}
    ten = replace(nine, class_key="name:10а", class_label="10А")
    await mod.observe(app_context, ten, [], date)
    school_dashboard = await client.get(root, headers=headers[4])
    school_data = (await school_dashboard.json())["data"]
    assert any(item["class_key"] == ten.class_key for item in school_data["classes"])
    assert all(lesson["class_key"] != ten.class_key for lesson in school_data["lessons"])
    assert "Личн" not in str(data)

    bells = {"kind": "bells", "date": date.isoformat(), "shift": 1, "limit": 3,
             "slots": {"3": ["10:00", "10:40"]}}
    assert (await client.post(root + "/changes", headers=headers[2], json=bells)).status == 403
    assert (await client.post(root + "/shift", headers=headers[1], json={**school, "class_key": eight.class_key, "shift": 1})).status == 200
    response = await client.post(root + "/changes", headers=headers[4], json=bells)
    assert response.status == 200
    bells_id = (await response.json())["data"]["id"]
    repeated = await client.post(root + "/changes", headers=headers[4], json=bells)
    assert (await repeated.json())["data"]["id"] == bells_id
    lesson = {"kind": "lesson", "date": date.isoformat(), "class_key": eight.class_key,
              "lesson_key": mod.lesson_key(mod.lesson_fields(physics)), "room": "42"}
    assert (await client.post(root + "/changes", headers=headers[2], json={**lesson, "class_key": nine.class_key})).status == 403
    assert (await client.post(root + "/changes", headers=headers[2], json=lesson)).status == 200
    assert (await client.post(root + "/changes", headers=headers[2],
                              json={"kind": "announce", "date": date.isoformat(), "text": "Всем"})).status == 403

    def day():
        return {"date": date.isoformat(), "lessons": [
            {**mod.lesson_fields(physics), "homework": ["Прочитать"], "marks": [5]},
            {**mod.lesson_fields(Lesson(4, "Химия", "11:25", "12:10", "14"))},
        ]}

    changes = await mod.changes_for(app_context, eight, date.isoformat(), date.isoformat())
    result = day()
    mod.apply_day(result, changes)
    assert result["lessons"][0]["room"] == "42"
    assert result["lessons"][0]["start"] == "10:00"
    assert result["lessons"][0]["marks"] == [5]
    assert result["lessons"][1]["moderation_cancelled"]
    bad_time = {**lesson, "start": "11:00", "end": "10:55"}
    assert (await client.post(root + "/changes", headers=headers[2], json=bad_time)).status == 400
    fixed_time = {**lesson, "number": 3, "start": "10:05", "end": "10:45"}
    assert (await client.post(root + "/changes", headers=headers[2], json=fixed_time)).status == 200
    result = day()
    mod.apply_day(result, await mod.changes_for(app_context, eight, date.isoformat(), date.isoformat()))
    assert result["lessons"][0]["number"] == 3
    assert result["lessons"][0]["time"] == "10:05–10:45"
    assert result["lessons"][0]["homework"] == ["Прочитать"]
    assert "author_id" not in str(result["moderation_notices"])
    unaffected = day()
    mod.apply_day(unaffected, await mod.changes_for(app_context, nine, date.isoformat(), date.isoformat()))
    assert unaffected == day()

    report = f"{root}/changes/{bells_id}/report"
    assert (await client.post(report, headers=headers[5], json={"reason": "time"})).status == 404
    assert (await client.post(report, headers=headers[3], json={"reason": "time"})).status == 200
    assert (await client.post(report, headers=headers[3], json={"reason": "time"})).status == 200
    assert (await app_context.db.fetch_one("SELECT count(*) AS n FROM moderation_reports"))["n"] == 1
    public = {"kind": "announce", "date": date.isoformat(), "class_key": eight.class_key,
              "text": "Возьмите форму"}
    assert (await client.post(root + "/changes", headers=headers[2], json=public)).status == 200

    new_lesson = {"kind": "lesson", "action": "add", "date": date.isoformat(),
                  "end_date": (date + dt.timedelta(days=7)).isoformat(),
                  "class_key": eight.class_key, "number": 5, "room": "42",
                  "subject": "История", "start": "12:20", "end": "13:00"}
    assert (await client.post(root + "/changes", headers=headers[2], json=new_lesson)).status == 200
    following_week = (date + dt.timedelta(days=7)).isoformat()
    next_day = {"date": following_week, "lessons": []}
    mod.apply_day(next_day, await mod.changes_for(app_context, eight, following_week, following_week))
    assert next_day["lessons"][0]["subject"] == "История"
    next_day = {"date": (date + dt.timedelta(days=1)).isoformat(), "lessons": []}
    mod.apply_day(next_day, await mod.changes_for(app_context, eight, next_day["date"], next_day["date"]))
    assert next_day["lessons"] == []

    assert (await grant(4, ["read"])).status == 200
    assert (await client.delete(f"{root}/changes/{bells_id}", headers=headers[4])).status == 403
    assert (await client.delete(f"{root}/changes/{bells_id}", headers=headers[1])).status == 200
    assert not [change for change in await mod.changes_for(app_context, eight, date.isoformat(), date.isoformat())
                if change["id"] == bells_id]


def test_bells_only_change_specified_slots():
    date = "2025-10-03"
    rule = mod.validate_bells({"slots": {"2": ["09:00", "09:40"]}})
    day = {"date": date, "lessons": [
        {"number": 1, "start": "08:00", "end": "08:45"},
        {"number": 2, "start": "09:05", "end": "09:50"},
    ]}
    mod.apply_day(day, [{"id": 1, "date": date, "kind": "bells", "payload": json.dumps(rule)}])
    assert day["lessons"] == [
        {"number": 1, "start": "08:00", "end": "08:45"},
        {"number": 2, "start": "09:00", "end": "09:40", "time": "09:00–09:40"},
    ]



async def test_diary_response_projects_bells_and_reuses_verified_scope(client, app_context, users, monkeypatch):
    user = await users.save(make_user(1, selected_student_id=23))
    date = user.today()
    scope = mod.Scope("sgo.example.ru", 17, mod.school_year(date), "name:8а", "8А")
    await app_context.db.execute(
        "INSERT INTO moderation_schools VALUES (?, ?, ?, 1)", (*scope.school, "Школа"),
    )

    async def init():
        return {"students": [{"studentId": 23, "className": "8А", "classId": 17}]}

    async def with_client(user, call):
        return await call(SimpleNamespace(_school_id=17, _student_id=23, diary_init=init))

    async def fetch_diary(user, **kwargs):
        return [DiaryDay(date, [Lesson(4, "Физика", "11:25", "12:10", "14", teacher="Учитель")])]

    monkeypatch.setattr(app_context.diary, "with_client", with_client, raising=False)
    monkeypatch.setattr(app_context.diary, "fetch_diary", fetch_diary)
    headers = {"X-Netschool-Token": await app_context.miniapp.issue_token(1)}
    result = await client.post("/mini/api/moderation/changes", headers=headers, json={
        "kind": "bells", "date": date.isoformat(), "limit": 3,
    })
    assert result.status == 200
    response = await client.get("/mini/api/diary", headers=headers)
    assert response.status == 200
    day = (await response.json())["data"]["days"][0]
    assert day["lessons"][0]["moderation_cancelled"]
    assert (day["lessons"][0]["start"], day["lessons"][0]["time"]) == ("", "Отменён")
    assert day["lessons"][0]["teacher"] == "Учитель"
    assert day["moderation_bells"]
    shared = await app_context.db.fetch_one("SELECT payload FROM moderation_lessons")
    assert set(json.loads(shared["payload"])) == {"number", "subject", "start", "end", "room"}
    cached = await app_context.cache.get(user.telegram_id, "diary:0")
    assert not cached[0]["days"][0]["lessons"][0].get("moderation_cancelled")

    async def unavailable(*args):
        raise AssertionError("Чтение готового дневника не должно заново запрашивать СГО")

    monkeypatch.setattr(app_context.diary, "with_client", unavailable)
    response = await client.get("/mini/api/diary", headers=headers)
    assert (await response.json())["data"]["days"][0]["moderation_bells"]


async def test_parent_school_is_taken_from_selected_child(app_context, users, monkeypatch):
    user = await users.save(make_user(2, selected_student_id=42))
    client = SimpleNamespace(_school_id=17, _student_id=42, _user={"children": [{"id": 42}]})

    async def init():
        return {"students": [{"studentId": 42, "className": "8А"}, {"studentId": 43, "className": "9А"}]}

    async def use_client(user, call):
        return await call(client)

    client.diary_init = init
    monkeypatch.setattr(app_context.diary, "with_client", use_client, raising=False)
    assert await mod.scope_for(app_context, user, require_enabled=False) is None
    client._user["children"][0]["schoolId"] = 18
    scope = await mod.scope_for(app_context, user, require_enabled=False)
    assert scope.school_id == 18
    assert scope.class_key == "name:8а"


async def test_owner_can_grant_school_moderation_by_account_number(client, app_context, users, monkeypatch):
    owner = await users.save(make_user(1))
    target = await users.save(make_user(2))
    scope = mod.Scope("sgo.example.ru", 17, mod.school_year(owner.today()), "name:8а", "8А")

    async def resolve(app, user, *, require_enabled=True):
        assert user.telegram_id == target.telegram_id
        assert not require_enabled
        return scope

    monkeypatch.setattr(mod, "scope_for", resolve)
    account_no = (await app_context.db.fetch_one(
        "SELECT account_no FROM users WHERE telegram_id=?", (target.telegram_id,),
    ))["account_no"]
    path = "/mini/api/moderation/quick-grant"
    headers = {"X-Netschool-Token": await app_context.miniapp.issue_token(target.telegram_id)}
    assert (await client.post(path, headers=headers, json={"account_no": account_no})).status == 403
    headers["X-Netschool-Token"] = await app_context.miniapp.issue_token(owner.telegram_id)
    for _ in range(2):
        assert (await client.post(path, headers=headers, json={"account_no": account_no})).status == 200
    school_scope = replace(scope, class_key="")
    assert await mod.allowed(app_context, target.telegram_id, school_scope, "bells")
    assert not await mod.allowed(app_context, target.telegram_id, school_scope, "lessons")
    row = await app_context.db.fetch_one("SELECT count(*) AS total FROM moderation_roles")
    assert row["total"] == 1
