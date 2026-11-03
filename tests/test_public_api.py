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


async def test_lifecycle_and_hash(client, token, db):
    data = await issue(client, token)
    key = data['token']
    row = await db.fetch_one('SELECT * FROM api_tokens')
    assert row['token_hash'] == digest(key)
    assert key not in str(dict(row))
    headers = {'Authorization': 'Bearer ' + key}
    response = await client.get(BASE + '/api/v1/profile', headers=headers)
    assert response.status == 200
    assert (await response.json())['data']['name']
    assert response.headers['Cache-Control'] == 'no-store'
    response = await client.get(BASE + '/api/integration-tokens', headers={'X-Netschool-Token': token})
    assert 'nsro_' not in await response.text()
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


@pytest.mark.parametrize('payload', [None, [], {'name': ''}, {'name': 'x', 'days': True}, {'name': 'x', 'days': 366}])
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
