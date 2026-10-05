"""Реальный Radicale: обнаружение, чтение, обновления и изоляция аккаунтов."""
import asyncio
import datetime as dt
from dataclasses import replace
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import pytest
from aiohttp import BasicAuth
from aiohttp.test_utils import TestClient, TestServer

from app.caldav.events import snapshot
from app.caldav.service import CaldavService
from app.db.repositories import CaldavRepository
from app.domain.records import DiaryDay, Lesson
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context
from tests.test_repositories import make_user

D = '{DAV:}'
C = '{urn:ietf:params:xml:ns:caldav}'
BASE = '/mini'


def days(user, subject='Алгебра'):
    return [DiaryDay(day=user.today() + dt.timedelta(days=1), lessons=[
        Lesson(number=1, subject=subject, start='08:00', end='08:40', room='21'),
        Lesson(number=2, subject='История', start='08:50', end='09:30'),
    ])]


def test_replacement_keeps_uid_and_unchanged_event():
    user = make_user()
    original = snapshot(days(user), user, {})
    assert snapshot(days(user), user, original) == original
    changed = snapshot(days(user, 'Физика'), user, original)
    assert set(changed['events']) == set(original['events'])
    modified = [event for uid, event in changed['events'].items() if event != original['events'][uid]]
    assert len(modified) == 1
    assert modified[0]['sequence'] == 1
    assert 'Физика' in modified[0]['ics']


def test_cancellation_partial_data_and_empty_confirmation():
    user = make_user()
    initial = days(user)
    original = snapshot(initial, user, {})
    initial[0].lessons.pop()
    cancelled = snapshot(initial, user, original)
    assert len(cancelled['events']) == 1
    broken = days(user)
    broken[0].lessons[0] = replace(broken[0].lessons[0], start='')
    assert snapshot(broken, user, original)['events'] == original['events']
    with pytest.raises(ValueError):
        snapshot([], user, original)
    empty_day = DiaryDay(day=initial[0].day)
    another_day = days(user)[0]
    another_day.day += dt.timedelta(days=1)
    once = snapshot([empty_day, another_day], user, original)
    assert len(once['events']) == 4
    twice = snapshot([empty_day, another_day], user, once)
    assert len(twice['events']) == 2


def test_other_child_gets_distinct_uids():
    user = make_user(selected_student_id=1)
    first = snapshot(days(user), user, {})
    other = replace(user, selected_student_id=2)
    second = snapshot(days(other), other, first)
    assert set(first['events']).isdisjoint(second['events'])


@pytest.fixture
async def calendar_client(app_context, tmp_path):
    app_context.settings = replace(app_context.settings, data_dir=tmp_path, caldav_enabled=True)
    user = await app_context.users.save(make_user())
    await app_context.users.save(make_user(2))
    app_context.diary.fetch_diary = AsyncMock(return_value=days(user))
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    token = await app_context.miniapp.issue_token(1)
    response = await client.post(BASE + '/api/caldav-account', headers={'X-Netschool-Token': token})
    assert response.status == 200, await response.text()
    account = (await response.json())['data']
    pending = list(app_context.caldav.jobs.values())
    await asyncio.gather(*pending)
    yield client, app_context, token, BasicAuth(account['login'], account['password'])
    await client.close()


async def query(client, auth):
    body = '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop><D:getetag/><C:calendar-data/></D:prop><C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT"/></C:comp-filter></C:filter></C:calendar-query>'
    response = await client.request('REPORT', BASE + '/caldav/ns1/schedule/', auth=auth, data=body,
                                    headers={'Depth': '1', 'Content-Type': 'application/xml'})
    assert response.status == 207, await response.text()
    return ET.fromstring(await response.text())


def objects(root):
    return {node.find(D + 'href').text: (node.find('.//' + D + 'getetag').text,
                                       node.find('.//' + C + 'calendar-data').text)
            for node in root.findall(D + 'response')}


async def test_discovery_and_real_calendar_query(calendar_client):
    client, app, token, auth = calendar_client
    response = await client.request('PROPFIND', '/.well-known/caldav', allow_redirects=False)
    assert response.status == 308
    assert response.headers['Location'] == BASE + '/caldav/'
    body = '<D:propfind xmlns:D="DAV:"><D:prop><D:current-user-principal/></D:prop></D:propfind>'
    response = await client.request('PROPFIND', BASE + '/caldav/', auth=auth, data=body, headers={'Depth': '0'})
    assert response.status == 207, await response.text()
    assert BASE + '/caldav/ns1/' in await response.text()
    root = await query(client, auth)
    assert len(objects(root)) == 2
    assert 'DTSTART:' in next(iter(objects(root).values()))[1]
    status = await client.get(BASE + '/api/caldav-account', headers={'X-Netschool-Token': token})
    data = (await status.json())['data']
    assert data['last_sync_at'] and not data['last_error']
    assert 'password' not in data
    assert status.headers['Cache-Control'] == 'no-store'


async def test_etags_replacement_and_cancel_without_duplicates(calendar_client):
    client, app, _, auth = calendar_client
    before = objects(await query(client, auth))
    await app.caldav.sync(1)
    assert objects(await query(client, auth)) == before
    user = await app.users.get(1)
    app.diary.fetch_diary.return_value = days(user, 'Физика')
    await app.caldav.sync(1)
    after = objects(await query(client, auth))
    assert set(before) == set(after)
    assert sum(before[key][0] != after[key][0] for key in before) == 1
    app.diary.fetch_diary.return_value[0].lessons.pop()
    await app.caldav.sync(1)
    assert len(objects(await query(client, auth))) == 1


async def test_multiget_conditional_get_and_sync_report(calendar_client):
    client, _, _, auth = calendar_client
    entries = objects(await query(client, auth))
    href, (etag, _) = next(iter(entries.items()))
    response = await client.get(href, auth=auth, headers={'If-None-Match': etag})
    assert response.status == 304
    body = f'<C:calendar-multiget xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop><D:getetag/><C:calendar-data/></D:prop><D:href>{href}</D:href></C:calendar-multiget>'
    response = await client.request('REPORT', BASE + '/caldav/ns1/schedule/', auth=auth, data=body)
    assert response.status == 207
    assert len(objects(ET.fromstring(await response.text()))) == 1
    body = '<D:sync-collection xmlns:D="DAV:"><D:sync-token/><D:sync-level>1</D:sync-level><D:prop><D:getetag/></D:prop></D:sync-collection>'
    response = await client.request('REPORT', BASE + '/caldav/ns1/schedule/', auth=auth, data=body)
    assert response.status == 207, await response.text()
    assert 'sync-token' in await response.text()


async def test_read_only_and_no_cross_user_access(calendar_client):
    client, app, _, auth = calendar_client
    await app.caldav.accounts.create(2)
    await app.caldav.sync(2)
    response = await client.request('PROPFIND', BASE + '/caldav/ns2/schedule/', auth=auth, headers={'Depth': '1'})
    assert response.status in (403, 404)
    for method in ['PUT', 'DELETE', 'PROPPATCH', 'MKCALENDAR', 'MOVE', 'COPY']:
        response = await client.request(method, BASE + '/caldav/ns1/schedule/', auth=auth)
        assert response.status == 403
    response = await client.request('PROPFIND', BASE + '/caldav/ns2/schedule/', auth=auth,
                                   headers={'X-Remote-User': '__netschool_publisher__', 'X-Script-Name': '/evil', 'Depth': '0'})
    assert response.status in (403, 404)


async def test_failure_retains_calendar_and_credentials_can_be_revoked(calendar_client):
    client, app, token, auth = calendar_client
    before = objects(await query(client, auth))
    app.diary.fetch_diary.side_effect = RuntimeError('school offline')
    await app.caldav.sync(1)
    assert objects(await query(client, auth)) == before
    assert (await app.caldav.accounts.get(1))['last_error']
    response = await client.delete(BASE + '/api/caldav-account', headers={'X-Netschool-Token': token})
    assert response.status == 200
    response = await client.request('PROPFIND', BASE + '/caldav/', auth=auth)
    assert response.status == 401
    assert await app.miniapp.resolve_token(token) == 1


async def test_password_rotation_and_logout(calendar_client):
    client, app, token, old_auth = calendar_client
    password = await app.caldav.accounts.create(1)
    assert await app.caldav.accounts.authenticate('ns1', old_auth.password) is None
    assert await app.caldav.accounts.authenticate('ns1', password) == 1
    from app.bot.handlers.settings import logout
    from tests.test_bot_handlers import FakeMessage
    await logout(FakeMessage(), await app.users.get(1), app)
    assert await app.caldav.accounts.get(1) is None
    response = await client.request('PROPFIND', BASE + '/caldav/', auth=BasicAuth('ns1', password))
    assert response.status == 401


async def test_snapshot_and_calendar_recover_after_storage_loss(calendar_client, tmp_path):
    client, app, _, auth = calendar_client
    before = objects(await query(client, auth))
    # Снимок в SQLite достаточен для пересоздания файлов календаря.
    await app.caldav.close()
    app.settings = replace(app.settings, data_dir=tmp_path / 'restored')
    app.caldav = CaldavService(app)
    await app.caldav.sync(1)
    assert objects(await query(client, auth)) == before


async def test_disabled_calendar_keeps_ics_available(app_context):
    await app_context.users.save(make_user())
    token = await app_context.miniapp.issue_token(1)
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    try:
        headers = {'X-Netschool-Token': token}
        response = await client.get(BASE + '/api/caldav-account', headers=headers)
        assert response.status == 200
        assert (await response.json())['data']['available'] is False
        response = await client.get(BASE + '/api/calendar-link', headers=headers)
        assert response.status == 200
        response = await client.request('PROPFIND', BASE + '/caldav/')
        assert response.status == 503
    finally:
        await client.close()


async def test_root_path_discovery_and_reading(app_context, tmp_path):
    app_context.settings = replace(app_context.settings, data_dir=tmp_path, caldav_enabled=True,
                                   web=replace(app_context.settings.web, miniapp_path=''))
    user = await app_context.users.save(make_user())
    app_context.diary.fetch_diary = AsyncMock(return_value=days(user))
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    try:
        password = await app_context.caldav.accounts.create(1)
        await app_context.caldav.sync(1)
        response = await client.request('PROPFIND', '/.well-known/caldav', auth=BasicAuth('ns1', password),
                                       data='<D:propfind xmlns:D="DAV:"><D:prop><D:current-user-principal/></D:prop></D:propfind>',
                                       headers={'Depth': '0'})
        assert response.status == 207
        assert '/caldav/ns1/' in await response.text()
        response = await client.get('/caldav/ns1/schedule/', auth=BasicAuth('ns1', password))
        assert response.status == 200
        assert 'BEGIN:VEVENT' in await response.text()
    finally:
        await client.close()


async def test_child_switch_during_school_request_does_not_publish_old_data(calendar_client):
    client, app, _, auth = calendar_client
    before = objects(await query(client, auth))
    async def switch_and_return(user, **kwargs):
        await app.users.save(replace(user, selected_student_id=2, student_name='Другой ребёнок'))
        return days(user, 'Ответ прежнего ребёнка')
    app.diary.fetch_diary.side_effect = switch_and_return
    await app.caldav.sync(1)
    assert objects(await query(client, auth)) == before
