"""Миниапп: поиск и темы только владельцу, в ответах нет школьных данных."""
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app import access
from app.domain.models import School
from tests.test_admin_screen import client, app_context  # noqa: F401
from tests.test_repositories import make_user


@pytest.fixture
async def people(app_context, users):
    await users.save(make_user(1))
    await users.save(make_user(2, display_name='SECRET FULL NAME', student_name='SECRET STUDENT', school=School('https://sgo.example.ru', 'SECRET SCHOOL')))
    await users.remember_telegram_username(2, 'alice_test')
    await users.save(make_user(3))
    await users.remember_telegram_username(3, 'aliceXtest')
    return {'owner': await app_context.miniapp.issue_token(1), 'other': await app_context.miniapp.issue_token(2)}


def assert_minimal(payload):
    rendered = str(payload)
    for forbidden in ('SECRET FULL NAME', 'SECRET STUDENT', 'SECRET SCHOOL', 'secret', 'ivan', 'email', 'student_name', 'school', 'credentials', 'display_name'):
        assert forbidden not in rendered


async def test_owner_only_search_details_and_actions(client, people):
    params = {'token': people['other']}
    for path in ('/mini/api/admin/users?q=alice', '/mini/api/admin/users/2'):
        assert (await client.get(path, params=params)).status == 403
    for path in ('theme', 'manage'):
        response = await client.post(f'/mini/api/admin/users/2/{path}', params=params,
                                     json={'kind': 'force', 'name': 'unity', 'value': 'on'})
        assert response.status == 403


async def test_search_by_username_id_and_account_number_is_minimal(client, people, app_context, monkeypatch):
    monkeypatch.setattr("app.access.UNITY_ENABLED", True)
    params = {'token': people['owner']}
    assert (await (await client.get('/mini/api/admin/users', params=params)).json())['data']['items'] == []
    number = (await app_context.db.fetch_one('SELECT account_no FROM users WHERE telegram_id=2'))['account_no']
    for query in ('@ALICE_', '2', f'#{number}'):
        data = (await (await client.get('/mini/api/admin/users', params={**params, 'q': query})).json())['data']
        assert [item['id'] for item in data['items']] == [2]
        assert_minimal(data)
    for query in ('x' * 65, '@bad%name', 'tg:99999999999999999999999999'):
        assert (await client.get('/mini/api/admin/users', params={**params, 'q': query})).status == 400
    response = await client.get('/mini/api/admin/users/2', params=params)
    data = (await response.json())['data']
    assert data['username'] == 'alice_test'
    assert [theme['key'] for theme in data['themes']] == ['unity', 'halloween']
    assert_minimal(data)


async def test_themes_grant_force_and_release_from_miniapp(client, people, app_context, monkeypatch):
    monkeypatch.setattr("app.access.UNITY_ENABLED", True)
    params = {'token': people['owner']}
    url = '/mini/api/admin/users/2/theme'
    async def post(kind, name, value):
        response = await client.post(url, params=params, json={'kind': kind, 'name': name, 'value': value})
        assert response.status == 200
        data = (await response.json())['data']
        assert_minimal(data)
        return {item['key']: item for item in data['themes']}
    themes = await post('grant', 'unity', 'allow')
    assert themes['unity']['available'] and themes['unity']['active']
    themes = await post('force', 'unity', 'on')
    assert themes['unity']['active'] and themes['unity']['forced'] == 'on'
    themes = await post('force', 'halloween', 'on')
    assert themes['halloween']['active'] and not themes['unity']['active']
    themes = await post('force', 'halloween', 'free')
    assert themes['halloween']['forced'] == 'free'
    themes = await post('grant', 'halloween', 'deny')
    assert not themes['halloween']['available'] and not themes['halloween']['active']
    assert (await app_context.miniapp.prefs(2))['halloween'] == 'off'


async def test_invalid_operations_and_missing_users(client, people):
    params = {'token': people['owner']}
    for body in ({}, {'kind': 'force', 'name': [], 'value': 'on'}, {'kind': 'force', 'name': 'unity', 'value': True},
                 {'kind': 'grant', 'name': 'admin', 'value': 'allow'}, {'kind': 'force', 'name': 'unity', 'value': 'bad'}):
        assert (await client.post('/mini/api/admin/users/2/theme', params=params, json=body)).status == 400
    assert (await client.get('/mini/api/admin/users/9999', params=params)).status == 404
    assert (await client.post('/mini/api/admin/users/9999/theme', params=params,
                              json={'kind': 'force', 'name': 'unity', 'value': 'on'})).status == 404


async def test_security_confirmation_is_bound_to_user_and_one_time(client, people, app_context, users):
    params = {'token': people['owner']}
    async def request(uid, kind, name, value):
        return await client.post(f'/mini/api/admin/users/{uid}/manage', params=params,
                                 json={'kind':kind, 'name':name, 'value':value})
    token = await app_context.miniapp.issue_token(2)
    response = await request(2, 'execute', 'reset', 'forged')
    assert response.status == 409
    response = await request(2, 'confirm', 'reset', '')
    confirmation = (await response.json())['data']['confirmation']
    assert await app_context.miniapp.resolve_token(token) == 2
    assert (await request(3, 'execute', 'reset', confirmation)).status == 409
    assert (await request(2, 'execute', 'reset', confirmation)).status == 200
    assert await app_context.miniapp.resolve_token(token) is None
    assert (await request(2, 'execute', 'reset', confirmation)).status == 409
    response = await request(2, 'confirm', 'delete', '')
    confirmation = (await response.json())['data']['confirmation']
    assert (await request(2, 'execute', 'delete', confirmation)).status == 200
    assert await users.get(2) is None
    assert (await request(1, 'confirm', 'delete', '')).status == 403


async def test_other_controls_work_without_returning_private_data(client, people, app_context, users, monkeypatch):
    params = {'token': people['owner']}
    monkeypatch.setattr(app_context.watchers, 'start', AsyncMock())
    monkeypatch.setattr(app_context.watchers, 'stop', AsyncMock())
    for kind, name, value in (('checking','enabled',False), ('checking','interval',600), ('notify','grades',False),
                              ('grant','assistant','deny'), ('force','theme','dark'), ('block','access',True)):
        response = await client.post('/mini/api/admin/users/2/manage', params=params,
                                     json={'kind':kind,'name':name,'value':value})
        assert response.status == 200
        assert_minimal((await response.json())['data'])
    assert (await users.get(2)).check_interval == 600
    assert (await users.get(2)).notifications.grades is False
    assert await access.blocked(app_context, 2)
    assert (await client.get('/mini/api/profile', params={'token':people['other']})).status == 403


async def test_selective_solver_access(client, people, app_context, users, monkeypatch):
    from app.solver.textbooks import Book
    from app.solver.permissions import permitted_books, rules_for
    books = [Book('algebra', 'Алгебра', ('Математика',)), Book('physics', 'Физика', ('Физика',))]
    monkeypatch.setattr(app_context.textbooks, 'all_books', lambda: books)
    params = {'token': people['owner']}
    url = '/mini/api/admin/users/2/manage'
    value = {'enabled': True, 'classwide': False, 'subjects': ['Математика'], 'books': ['algebra']}
    async def post(token=params):
        return await client.post(url, params=token, json={'kind': 'solver', 'name': 'access', 'value': value})
    assert (await post({'token': people['other']})).status == 403
    assert (await post()).status == 200
    user = await users.get(2)
    assert await permitted_books(app_context, user, 'Математика') == {'algebra'}
    assert await permitted_books(app_context, user, 'Физика') == set()
    value['books'] = ['missing']
    assert (await post()).status == 400
    value['books'] = ['algebra']
    value['classwide'] = True
    assert (await post()).status == 400
    await app_context.cache.put(2, f'class:{user.selected_student_id}', '9 Б')
    assert (await post()).status == 200
    data = (await (await client.get('/mini/api/admin/users/2', params=params)).json())['data']
    assert_minimal(data)
    other = await users.save(replace(await users.get(3), school=user.school))
    await app_context.cache.put(3, f'class:{other.selected_student_id}', '9б')
    assert await permitted_books(app_context, other, 'Математика') == {'algebra'}
    await app_context.cache.put(3, f'class:{other.selected_student_id}', '9а')
    assert await rules_for(app_context, other) == []
    value['enabled'] = False
    assert (await post()).status == 200
    assert await permitted_books(app_context, user, 'Физика') is None


async def test_retired_unity_theme_ignores_existing_override(client, people, app_context, monkeypatch):
    with monkeypatch.context() as enabled:
        enabled.setattr("app.access.UNITY_ENABLED", True)
        await access.set_control(app_context, 1, force=("unity", "on"))

    params = {"token": people["owner"]}
    profile = await client.get("/mini/api/profile", params=params)
    profile_data = (await profile.json())["data"]
    assert profile_data["unity"] is False
    assert "unity" not in profile_data["managed_prefs"]
    prefs = await client.get("/mini/api/prefs", params=params)
    payload = await prefs.json()
    assert payload["data"]["unity"] == "off"
    assert "unity" not in payload["managed"]
    details = await client.get("/mini/api/admin/users/1", params=params)
    assert [theme["key"] for theme in (await details.json())["data"]["themes"]] == ["halloween"]
    notice = await client.post("/mini/api/notices/unity", params=params)
    assert (await notice.json())["data"]["show"] is False
    with pytest.raises(ValueError):
        await access.set_control(app_context, 1, force=("unity", "on"))
