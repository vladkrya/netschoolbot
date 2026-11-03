"""Замены уроков не должны менять школьный кэш или открывать чужой класс."""
import base64
import copy
import datetime as dt
import io
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.domain.models import School
from app.web import schedule_import as module
from tests.test_bot_handlers import app as app_context  # noqa: F401
from tests.test_repositories import make_user
from tests.test_web import BASE, client, token  # noqa: F401


def lesson(**kw):
    return {'number': 1, 'subject': 'Алгебра', 'start': '', 'end': '', 'room': '', **kw}


def days(date=None):
    return [{'date': date or make_user().today().isoformat(), 'lessons': [lesson()]}]


def auth(token):
    return {'X-Netschool-Token': token}


@pytest.fixture(autouse=True)
def clear_limits():
    module._last.clear()
    module._running.clear()


@pytest.fixture
async def known_class(app_context, monkeypatch):
    fetch = AsyncMock(return_value='9 «Б»')
    monkeypatch.setattr(app_context.diary, 'fetch_class_name', fetch, raising=False)
    return fetch


@pytest.mark.parametrize('bad', [[], None, [None], [{'date': 'bad', 'lessons': []}],
    days()[0:1] * 2, days()[0:1] * 8,
    [{'date': '2026-01-01', 'lessons': [lesson(number=True)]}],
    [{'date': '2026-01-01', 'lessons': [lesson(), lesson()]}],
    [{'date': '2026-01-01', 'lessons': [lesson(start='25:00', end='26:00')]}],
    [{'date': '2026-01-01', 'lessons': [lesson(start='08:00')]}],
    [{'date': '2026-01-01', 'lessons': [lesson(start='08:00', end='07:00')]}],
    [{'date': '2026-01-01', 'lessons': [lesson(subject=' ')]}],
])
def test_validation(bad):
    with pytest.raises((ValueError, TypeError, KeyError)):
        module.clean_days(bad)


def test_overlay_keeps_marks_and_homework_without_duplicates():
    homework = [{'text': '№ 12'}]
    original = {'days': [{'date': '2026-11-03', 'marks': [5], 'homework': homework,
                         'lessons': [lesson(homework=homework, marks=[5])]}]}
    before = copy.deepcopy(original)
    # overlay получает раскодированный JSON, школьный кэш хранится отдельно.
    result = module.overlay(copy.deepcopy(original), [{'date': '2026-11-03', 'lessons': [
        lesson(number=2), lesson(number=3)]}])
    day = result['days'][0]
    assert day['marks'] == [5] and day['homework'] == homework
    assert day['lessons'][0]['homework'] == homework
    assert day['lessons'][1]['homework'] == []
    assert original == before
    empty = module.overlay(result, [{'date': '2026-11-03', 'lessons': []}])['days'][0]
    assert empty['lessons'] == [] and empty['homework'] == homework


def test_overlay_keeps_lesson_marks_and_homework_when_lessons_are_reordered():
    algebra_homework = [{'text': '№ 12'}]
    physics_homework = [{'text': 'Лабораторная'}]
    data = {'days': [{'date': '2026-11-03', 'marks': [], 'homework': [], 'lessons': [
        lesson(number=1, subject='Алгебра', marks=[{'mark': '5'}], homework=algebra_homework),
        lesson(number=2, subject='Физика', marks=[{'mark': '4'}], homework=physics_homework),
    ]}]}
    imported = [{'date': '2026-11-03', 'lessons': [
        lesson(number=1, subject='Физика'), lesson(number=2, subject='Алгебра'),
    ]}]
    lessons = module.overlay(data, imported)['days'][0]['lessons']
    assert lessons[0]['subject'] == 'Физика'
    assert lessons[0]['marks'] == [{'mark': '4'}] and lessons[0]['homework'] == physics_homework
    assert lessons[1]['subject'] == 'Алгебра'
    assert lessons[1]['marks'] == [{'mark': '5'}] and lessons[1]['homework'] == algebra_homework


async def test_save_overlay_delete_and_school_cache_unchanged(client, token, app_context, known_class):
    user = await app_context.users.get(1)
    date = user.today().isoformat()
    original = {'days': [{'date': date, 'lessons': [lesson(subject='Физика')], 'marks': [], 'homework': []}], 'week': 0}
    await app_context.cache.put(1, 'diary:0:current', original)
    url = f'{BASE}/api/schedule/imports'
    response = await client.post(url, headers=auth(token), json={'days': days(date)})
    assert response.status == 200, await response.text()
    response = await client.get(f'{BASE}/api/diary?window=current', headers=auth(token))
    day = (await response.json())['data']['days'][0]
    assert day['imported_schedule'] and day['lessons'][0]['subject'] == 'Алгебра'
    assert (await app_context.cache.get(1, 'diary:0:current'))[0] == original
    response = await client.post(url, headers=auth(token), json={'action': 'delete', 'days': days(date)})
    assert response.status == 200
    response = await client.get(f'{BASE}/api/diary?window=current', headers=auth(token))
    assert (await response.json())['data'] == original


async def test_child_and_school_isolation(client, token, app_context, known_class):
    url = f'{BASE}/api/schedule/imports'
    assert (await client.post(url, headers=auth(token), json={'days': days()})).status == 200
    user = await app_context.users.get(1)
    await app_context.users.save(replace(user, selected_student_id=99))
    response = await client.get(url, headers=auth(token))
    assert (await response.json())['data']['days'] == []
    await app_context.users.save(replace(user, school=School(url=user.school.url, name='Другая школа')))
    response = await client.get(url, headers=auth(token))
    assert (await response.json())['data']['days'] == []
    await app_context.users.save(user)
    response = await client.get(url, headers=auth(token))
    assert len((await response.json())['data']['days']) == 1


async def test_sharing_requires_same_school_and_class(client, token, app_context, known_class):
    url = f'{BASE}/api/schedule/imports'
    assert (await client.post(url, headers=auth(token), json={'action': 'share', 'days': days()})).status == 200
    # Публикация сама по себе не заменяет расписание автора.
    response = await client.get(url, headers=auth(token))
    data = (await response.json())['data']
    assert data['days'] == [] and len(data['shared']) == 1
    publication = data['shared'][0]['id']
    for number, school, klass, allowed in [
        (2, make_user().school, '9б', True),
        (3, School(url='https://other.example', name='Школа №1'), '9б', False),
        (4, make_user().school, '9в', False),
        (5, make_user().school, '', False),
        (6, School(url=make_user().school.url, name='Школа №2'), '9б', False),
    ]:
        await app_context.users.save(make_user(number, school=school))
        await app_context.cache.put(number, 'class:None', klass)
        # Неизвестный класс нельзя заменить произвольным классом со стороны клиента.
        known_class.return_value = klass
        other = await app_context.miniapp.issue_token(number)
        response = await client.get(url, headers=auth(other))
        assert bool((await response.json())['data']['shared']) is allowed
        response = await client.post(url, headers=auth(other), json={'action': 'accept', 'id': publication})
        assert (response.status == 200) is allowed


async def test_reject_invalid_without_partial_save(client, token, app_context):
    response = await client.post(f'{BASE}/api/schedule/imports', headers=auth(token), json={
        'days': [*days(), {'date': 'bad', 'lessons': []}]})
    assert response.status == 400
    assert await module.private_days(app_context, await app_context.users.get(1)) == []


async def test_recognize_preview_no_save_and_period_validation(client, token, app_context, monkeypatch, known_class):
    app_context.settings = replace(app_context.settings, solver=replace(app_context.settings.solver, gemini_key='test'))
    output = io.BytesIO()
    Image.new('RGB', (10, 10)).save(output, format='PNG')
    image = base64.b64encode(output.getvalue()).decode()
    date = (await app_context.users.get(1)).today()
    reader = AsyncMock(return_value=days(date.isoformat()))
    monkeypatch.setattr(module, 'read_photo', reader)
    url = f'{BASE}/api/schedule/recognize'
    response = await client.post(url, headers=auth(token), json={'date': date.isoformat(), 'mode': 'day', 'image': image})
    assert response.status == 200, await response.text()
    assert (await response.json())['data']['days'] == days(date.isoformat())
    assert await module.private_days(app_context, await app_context.users.get(1)) == []
    assert reader.call_args.args[1][:2] == b'\xff\xd8'  # Сервер декодировал и перекодировал фото.
    response = await client.post(url, headers=auth(token), json={'date': date.isoformat(), 'mode': 'day', 'image': image})
    assert response.status == 429
    module._last.clear()
    reader.return_value = days((date + dt.timedelta(days=1)).isoformat())
    response = await client.post(url, headers=auth(token), json={'date': date.isoformat(), 'mode': 'day', 'image': image})
    assert response.status == 502
    module._last.clear()
    response = await client.post(url, headers=auth(token), json={'date': date.isoformat(), 'mode': 'day', 'image': 'not-a-photo'})
    assert response.status == 400


async def test_auth_required(client):
    for route in ('imports', 'recognize'):
        response = await client.post(f'{BASE}/api/schedule/{route}', json={})
        assert response.status == 401


async def test_import_routes_are_registered(client):
    """404 с HTML браузер показывал как «некорректный ответ сервера»."""
    response = await client.get(f'{BASE}/api/schedule/imports')
    assert response.status == 401
    assert response.content_type == 'application/json'


async def test_import_available_when_school_unreachable(client, token, app_context, monkeypatch):
    from app.netschool.errors import NetSchoolError, Reason
    user = await app_context.users.get(1)
    response = await client.post(f'{BASE}/api/schedule/imports', headers=auth(token), json={'days': days(user.today().isoformat())})
    assert response.status == 200
    monkeypatch.setattr(app_context.diary, 'fetch_diary', AsyncMock(side_effect=NetSchoolError(Reason.SERVER_UNAVAILABLE, 'Школа недоступна')), raising=False)
    response = await client.get(f'{BASE}/api/diary?window=current', headers=auth(token))
    assert response.status == 200
    data = await response.json()
    assert data['stale'] is True and data['data']['days'][0]['lessons'][0]['subject'] == 'Алгебра'


def test_missing_times_use_school_slot_not_subject_time():
    data = {'days': [{'date': '2026-11-03', 'lessons': [
        lesson(number=1, subject='Физика', start='08:00', end='08:40'),
        lesson(number=2, start='09:00', end='09:40')]}]}
    result = module.overlay(data, days('2026-11-03'))
    assert result['days'][0]['lessons'][0]['start'] == '08:00'
    assert result['days'][0]['lessons'][0]['subject'] == 'Алгебра'
