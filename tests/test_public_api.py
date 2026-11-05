"""Ключи интеграций: граница доступа, отзыв, срок и изоляция владельцев."""
import time
import pytest
from tests.test_web import client, token, app_context, BASE  # noqa: F401
from tests.test_repositories import make_user
from app.web.public_api import digest


async def issue(client, token):
    response = await client.post(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token}, json={'name': 'Виджет', 'days': 30})
    assert response.status == 201
    return (await response.json())['data']


async def test_lifecycle_and_hash(client, token, db, app_context):
    data = await issue(client, token)
    key = data['token']
    number = await app_context.accounts.number(1)
    assert key.startswith(f'sgoshka_{number}_')
    row = await db.fetch_one('SELECT * FROM api_tokens')
    assert row['token_hash'] == digest(key)
    assert key not in str(dict(row))
    headers = {'Authorization': 'Bearer ' + key}
    response = await client.get(BASE + '/api/v1/profile', headers=headers)
    assert response.status == 200
    assert (await response.json())['data']['name']
    assert response.headers['Cache-Control'] == 'no-store'
    response = await client.get(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token})
    listed = await response.text()
    assert key not in listed
    assert 'sgoshka_' not in listed
    response = await client.delete(BASE + f"/api/integration-tokens/{row['id']}", headers={'X-Netschool-Token': token})
    assert response.status == 200
    assert (await client.get(BASE + '/api/v1/profile', headers=headers)).status == 401


@pytest.mark.parametrize('method,path', [('post','/api/v1/mail/send'), ('delete','/api/v1/profile'), ('post','/api/v1/integration-tokens'), ('get','/api/profile'), ('get','/api/attachment-ticket'), ('post','/api/integration-tokens')])
async def test_no_write_or_session_access(client, token, method, path):
    key = (await issue(client, token))['token']
    response = await getattr(client, method)(BASE + path, headers={'Authorization': 'Bearer ' + key})
    assert response.status in (403, 405)


async def test_auth_expiry_and_owner_isolation(client, token, app_context, users, db):
    data = await issue(client, token)
    key = data['token']
    assert (await client.get(BASE + '/api/v1/profile', headers={'X-Netschool-Token': token})).status == 401
    assert (await client.get(BASE + '/api/v1/profile', params={'token': key})).status == 401
    await users.save(make_user(telegram_id=2, student_name='Другой ученик'))
    other = await app_context.miniapp.issue_token(2)
    await client.delete(BASE + f"/api/integration-tokens/{data['items'][0]['id']}", headers={'X-Netschool-Token': other})
    headers = {'Authorization': 'Bearer ' + key}
    assert (await client.get(BASE + '/api/v1/profile', headers=headers)).status == 200
    await db.execute('UPDATE api_tokens SET expires_at=?', (int(time.time()) - 1,))
    assert (await client.get(BASE + '/api/v1/profile', headers=headers)).status == 401


async def test_docs_and_unknown_routes(client, token):
    assert (await client.get(BASE + '/api/v1/docs')).status == 200
    spec = await (await client.get(BASE + '/api/v1/openapi.json')).json()
    assert spec['components']['securitySchemes']['bearerAuth']['scheme'] == 'bearer'
    key = (await issue(client, token))['token']
    assert (await client.get(BASE + '/api/v1/admin', headers={'Authorization': 'Bearer ' + key})).status == 404


@pytest.mark.parametrize('payload', [None, [], {'name': ''}, {'name': 'x', 'days': True}, {'name': 'x', 'days': 366}, {'name': 'x', 'days': -1}])
async def test_bad_token_request(client, token, payload):
    response = await client.post(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token}, json=payload)
    assert response.status == 400


@pytest.mark.parametrize('resource,section,payload', [
    ('diary', 'diary:0', {'days': [], 'week': 0}),
    ('schedule', 'diary:0', {'days': [], 'week': 0}),
    ('homework', 'homework', {'items': [{'subject': 'Математика'}]}),
    ('mail', 'mail', {'items': [{'id': 7, 'subject': 'Объявление'}]}),
])
async def test_read_data_uses_owner_cache(client, token, app_context, resource, section, payload):
    key = (await issue(client, token))['token']
    await app_context.cache.put(1, section, payload)
    response = await client.get(BASE + '/api/v1/' + resource, headers={'Authorization': 'Bearer ' + key})
    assert response.status == 200
    assert (await response.json())['data'] == payload


async def test_blocked_account_cannot_use_token(client, token, db, app_context):
    from dataclasses import replace
    app_context.settings = replace(app_context.settings, telegram=replace(app_context.settings.telegram, admin_id=999))
    key = (await issue(client, token))['token']
    await db.execute('INSERT INTO user_controls(telegram_id,blocked) VALUES (?,?)', (1, 1))
    response = await client.get(BASE + '/api/v1/profile', headers={'Authorization': 'Bearer ' + key})
    assert response.status == 403


async def test_mail_message_and_attachment_route(client, token, app_context, monkeypatch):
    from unittest.mock import AsyncMock
    read = AsyncMock(return_value={'id': 7, 'text': 'Письмо'})
    monkeypatch.setattr(app_context.diary, 'fetch_mail_message', read)
    key = (await issue(client, token))['token']
    response = await client.get(BASE + '/api/v1/mail/7', headers={'Authorization': 'Bearer ' + key})
    assert response.status == 200
    assert (await response.json())['data']['text'] == 'Письмо'
    assert read.call_args.args[0].telegram_id == 1
    assert read.call_args.args[1] == 7
    response = await client.get(BASE + '/api/v1/attachment/not-an-id', headers={'Authorization': 'Bearer ' + key})
    assert response.status == 400


async def test_permanent_token_can_be_used_and_revoked(client, token, db):
    response = await client.post(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token}, json={'name': 'Без срока', 'days': 0})
    assert response.status == 201
    data = (await response.json())['data']
    assert data['items'][0]['expires_at'] is None
    row = await db.fetch_one('SELECT * FROM api_tokens')
    assert row['expires_at'] == 0
    headers = {'Authorization': 'Bearer ' + data['token']}
    assert (await client.get(BASE + '/api/v1/profile', headers=headers)).status == 200
    await client.delete(BASE + f"/api/integration-tokens/{row['id']}", headers={'X-Netschool-Token': token})
    assert (await client.get(BASE + '/api/v1/profile', headers=headers)).status == 401


async def test_permanent_tokens_count_towards_limit(client, token, db):
    await db.execute_many('INSERT INTO api_tokens(owner_id,name,token_hash,created_at,expires_at) VALUES (?,?,?,?,?)', [(1, 'Постоянный', digest(f'limit-{i}'), int(time.time()), 0) for i in range(20)])
    response = await client.post(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token}, json={'name': 'Лишний', 'days': 30})
    assert response.status == 409


async def test_legacy_token_still_works_and_cannot_write(client, token, db):
    # Синтетический ключ: не используем секреты реального аккаунта.
    legacy = 'nsro_test_legacy_only'
    await db.execute('INSERT INTO api_tokens(owner_id,name,token_hash,created_at,expires_at) VALUES (?,?,?,?,?)', (1, 'Старый', digest(legacy), int(time.time()), int(time.time()) + 86400))
    headers = {'Authorization': 'Bearer ' + legacy}
    assert (await client.get(BASE + '/api/v1/profile', headers=headers)).status == 200
    assert (await client.post(BASE + '/api/v1/mail/send', headers=headers)).status == 405
    assert (await client.get(BASE + '/api/profile', headers=headers)).status == 403


async def test_json_is_readable_utf8_including_errors(client, token, app_context):
    key = (await issue(client, token))['token']
    await app_context.cache.put(1, 'diary:0', {'days': [{'subject': 'Русский язык'}], 'week': 0})
    response = await client.get(BASE + '/api/v1/diary', headers={'Authorization': 'Bearer ' + key})
    raw = await response.text()
    assert 'Русский язык' in raw
    assert '\\u0420' not in raw
    assert '\n  "ok": true' in raw
    assert (await response.json())['data']['days'][0]['subject'] == 'Русский язык'
    response = await client.get(BASE + '/api/v1/profile')
    assert response.status == 401
    assert 'Передайте действующий токен' in await response.text()


async def test_custom_expiry_uses_account_timezone_and_is_inclusive(client, token, app_context, db):
    import datetime as dt
    user = await app_context.users.get(1)
    end = user.today() + dt.timedelta(days=500)
    response = await client.post(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token}, json={'name': 'До даты', 'expires_on': end.isoformat()})
    assert response.status == 201
    data = (await response.json())['data']
    expected = int(dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), tzinfo=user.tz).timestamp())
    assert data['items'][0]['expires_at'] == expected
    headers = {'Authorization': 'Bearer ' + data['token']}
    assert (await client.get(BASE + '/api/v1/profile', headers=headers)).status == 200
    await db.execute('UPDATE api_tokens SET expires_at=?', (int(time.time()) - 1,))
    assert (await client.get(BASE + '/api/v1/profile', headers=headers)).status == 401


@pytest.mark.parametrize('extra', [
    {'expires_on': '2000-01-01'}, {'expires_on': '2026-02-30'},
    {'expires_on': ''}, {'expires_on': None}, {'expires_on': 123},
    {'expires_on': '9999-12-31'}, {'expires_on': '2030-01-01', 'days': 30},
])
async def test_invalid_custom_expiry(client, token, extra):
    response = await client.post(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token}, json={'name': 'Дата', **extra})
    assert response.status == 400


async def test_previous_sgoshka_prefix_remains_valid(client, token, db):
    old = 'sgoshka-10001_test_only'
    await db.execute('INSERT INTO api_tokens(owner_id,name,token_hash,created_at,expires_at) VALUES (?,?,?,?,?)', (1, 'Старый', digest(old), int(time.time()), 0))
    headers = {'Authorization': 'Bearer ' + old}
    assert (await client.get(BASE + '/api/v1/profile', headers=headers)).status == 200
    assert (await client.get(BASE + '/api/profile', headers=headers)).status == 403


async def issue_status(client, token):
    response = await client.post(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token}, json={'name': 'Мониторинг', 'scope': 'status', 'days': 0})
    assert response.status == 201
    data = (await response.json())['data']
    assert data['items'][0]['scope'] == 'status'
    assert '_status_' in data['token']
    return data


async def test_status_token_is_separate_from_diary(client, token):
    status_key = (await issue_status(client, token))['token']
    diary_key = (await issue(client, token))['token']
    for path in ('/profile', '/diary', '/mail', '/attachment/123', '/homework/done'):
        response = await client.get(BASE + '/api/v1' + path, headers={'Authorization': 'Bearer ' + status_key})
        assert response.status == 403
        assert (await response.json())['reason'] == 'token_scope'
    response = await client.get(BASE + '/api/v1/status', headers={'Authorization': 'Bearer ' + diary_key})
    assert response.status != 403  # Единый токен теперь открывает и статус.
    response = await client.post(BASE + '/api/v1/status/report', headers={'Authorization': 'Bearer ' + status_key}, json={'host': 'example.org'})
    assert response.status == 405


async def test_status_resources_and_validation(client, token, app_context, monkeypatch):
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    snapshot = {'generated_at': 1234, 'range': '7d', 'since': 1000, 'bucket_s': 7200,
        'hosts': [{'host': 'school.example', 'region': 'Регион', 'state': 'up', 'uptime': 100}],
        'overview': {'down': [0]}, 'incidents': [{'host': 'school.example', 'ongoing': False}],
        'versions': [{'host': 'school.example', 'version': '5.0'}], 'reports': [{'kind': 'slow'}]}
    summary = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(app_context, 'status', SimpleNamespace(summary=summary, close_listeners=lambda: None))
    headers = {'Authorization': 'Bearer ' + (await issue_status(client, token))['token']}
    for path, field in (('status', 'hosts'), ('status/hosts', 'hosts'), ('status/hosts/school.example', 'host'), ('status/incidents', 'items'), ('status/versions', 'items'), ('status/reports', 'items')):
        response = await client.get(BASE + '/api/v1/' + path + '?range=7d', headers=headers)
        assert response.status == 200
        assert field in (await response.json())['data']
    summary.assert_awaited_with('7d')
    assert (await client.get(BASE + '/api/v1/status?range=bad', headers=headers)).status == 400
    assert (await client.get(BASE + '/api/v1/status/hosts/not-found.example', headers=headers)).status == 404
    assert (await client.get(BASE + '/api/v1/status')).status == 401


async def test_status_token_revocation_and_expiry(client, token, db):
    data = await issue_status(client, token)
    headers = {'Authorization': 'Bearer ' + data['token']}
    await db.execute('UPDATE api_tokens SET expires_at=?', (int(time.time()) - 1,))
    assert (await client.get(BASE + '/api/v1/status', headers=headers)).status == 401
    data = await issue_status(client, token)
    await client.delete(BASE + f"/api/integration-tokens/{data['items'][0]['id']}", headers={'X-Netschool-Token': token})
    assert (await client.get(BASE + '/api/v1/status', headers={'Authorization': 'Bearer ' + data['token']})).status == 401


async def test_reject_unknown_token_scope(client, token):
    response = await client.post(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token}, json={'name': 'Недопустимый', 'scope': 'admin'})
    assert response.status == 400


async def test_status_openapi_has_own_bearer_and_host_parameter(client):
    spec = await (await client.get(BASE + '/api/v1/openapi.json')).json()
    operation = spec['paths']['/status/hosts/{host}']['get']
    assert operation['security'] == [{'bearerAuth': []}, {'statusBearerAuth': []}]
    host = next(p for p in operation['parameters'] if p['name'] == 'host')
    assert host['schema']['type'] == 'string' and host['required']
    assert 'statusBearerAuth' in spec['components']['securitySchemes']


async def test_student_selection_is_request_local_and_cache_isolated(client, token, app_context, users, monkeypatch):
    import datetime as dt
    from dataclasses import replace
    from app.domain.models import Student
    from app.domain.records import DiaryDay, Lesson
    from unittest.mock import AsyncMock
    user = await users.get(1)
    user = await users.save(replace(user, available_students=(Student(11, 'Первый'), Student(22, 'Второй')), selected_student_id=11, student_name='Первый'))
    seen = []
    async def fetch(owner, **kwargs):
        seen.append(owner.selected_student_id)
        return [DiaryDay(day=dt.date(2026, 11, 5), lessons=[Lesson(number=1, subject=owner.student_name)])]
    monkeypatch.setattr(app_context.diary, 'fetch_diary', fetch)
    monkeypatch.setattr(app_context.diary, 'fetch_teachers', AsyncMock(return_value={}))
    key = (await issue(client, token))['token']
    headers = {'Authorization': 'Bearer ' + key}
    await app_context.cache.put(1, 'diary:0', {'days': [{'sentinel': 'миниапп'}], 'week': 0})
    for sid, name in ((22, 'Второй'), (11, 'Первый'), (22, 'Второй')):
        response = await client.get(BASE + f'/api/v1/diary?student_id={sid}', headers=headers)
        assert response.status == 200
        assert (await response.json())['data']['days'][0]['lessons'][0]['subject'] == name
    assert seen == [22, 11]  # третий запрос берёт отдельный кэш второго ребёнка
    stored = await users.get(1)
    assert stored.selected_student_id == 11 and stored.student_name == 'Первый'
    assert (await app_context.cache.get(1, 'diary:0'))[0]['days'][0]['sentinel'] == 'миниапп'
    profile = await (await client.get(BASE + '/api/v1/profile?student_id=22', headers=headers)).json()
    assert profile['data']['student_id'] == 22
    assert next(s for s in profile['data']['students'] if s['current'])['id'] == 22
    assert (await client.get(BASE + '/api/v1/diary?student_id=99999', headers=headers)).status == 403
    assert (await client.get(BASE + '/api/v1/diary?student_id=x', headers=headers)).status == 400
    assert (await client.get(BASE + '/api/v1/diary?student_id=11&student_id=22', headers=headers)).status == 400
    assert (await client.get(BASE + '/api/v1/mail?student_id=22', headers=headers)).status == 400


async def test_token_permissions_are_immutable_and_enforced(client, token, app_context):
    response = await client.post(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token}, json={'name': 'Только профиль', 'permissions': ['profile']})
    assert response.status == 201
    data = (await response.json())['data']
    assert data['items'][0]['permissions'] == ['profile']
    headers = {'Authorization': 'Bearer ' + data['token']}
    assert (await client.get(BASE + '/api/v1/profile', headers=headers)).status == 200
    for path in ('diary', 'marks', 'homework', 'mail', 'attachment/1', 'status', 'notes', 'history', 'search?q=abc', 'children'):
        assert (await client.get(BASE + '/api/v1/' + path, headers=headers)).status == 403
    token_id = data['items'][0]['id']
    for method in ('patch', 'put', 'post'):
        response = await getattr(client, method)(BASE + f'/api/integration-tokens/{token_id}', headers={'X-Netschool-Token': token}, json={'permissions': ['mail']})
        assert response.status in (404, 405)
    listed = await (await client.get(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token})).json()
    assert listed['data']['items'][0]['permissions'] == ['profile']
    assert 'status' in listed['data']['available_permissions']


@pytest.mark.parametrize('permissions', [[], ['admin'], 'profile', [None], [True]])
async def test_reject_invalid_permissions(client, token, permissions):
    response = await client.post(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token}, json={'name': 'Права', 'permissions': permissions})
    assert response.status == 400


async def test_combined_token_grants_status_and_profile(client, token, app_context, monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(app_context.status, 'summary', AsyncMock(return_value={'hosts': []}))
    response = await client.post(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token}, json={'name': 'Профиль и статус', 'permissions': ['profile', 'status']})
    headers = {'Authorization': 'Bearer ' + (await response.json())['data']['token']}
    assert (await client.get(BASE + '/api/v1/profile', headers=headers)).status == 200
    assert (await client.get(BASE + '/api/v1/status', headers=headers)).status == 200
    assert (await client.get(BASE + '/api/v1/mail', headers=headers)).status == 403
