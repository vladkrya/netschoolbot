import hashlib
import json
from dataclasses import replace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from app.web.android_updates import release_manifest
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context


def publish(root, **changes):
    root.mkdir(parents=True, exist_ok=True)
    apk = b'example signed apk payload'
    (root / 'sgoshka-4.apk').write_bytes(apk)
    data = dict(packageName='ru.ikrya.sgoshka', versionCode=4, versionName='0.4.0',
                file='sgoshka-4.apk', sha256=hashlib.sha256(apk).hexdigest(), size=len(apk), minSdk=26, notes='Новая версия')
    data.update(changes)
    (root / 'update.json').write_text(json.dumps(data), encoding='utf-8')
    return apk


def test_manifest_requires_release_package_safe_filename_and_matching_file(tmp_path):
    publish(tmp_path)
    assert release_manifest(tmp_path)['versionCode'] == 4
    for changes in ({'file': '../secret.apk'}, {'packageName': 'ru.ikrya.sgoshka.debug'}, {'versionCode': True},
                    {'sha256': 'not-a-hash'}, {'size': 999}, {'minSdk': 0}, {'notes': 'x' * 4001}):
        publish(tmp_path, **changes)
        assert release_manifest(tmp_path) is None


def test_symlinks_and_incomplete_publication_are_not_advertised(tmp_path):
    assert release_manifest(tmp_path) is None
    publish(tmp_path)
    apk = tmp_path / 'sgoshka-4.apk'
    apk.unlink()
    apk.symlink_to(tmp_path / 'outside')
    (tmp_path / 'outside').write_bytes(b'example signed apk payload')
    assert release_manifest(tmp_path) is None


@pytest.mark.asyncio
async def test_public_channel_needs_no_session_or_pin_and_serves_apk_without_cache(app_context, tmp_path):
    app_context.settings = replace(app_context.settings, data_dir=tmp_path)
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    try:
        response = await client.get('/mini/android/update.json')
        assert response.status == 200
        assert await response.json() == {'available': False}
        assert response.headers['Cache-Control'] == 'no-store'
        apk = publish(tmp_path / 'android-releases')
        response = await client.get('/mini/android/update.json')
        manifest = await response.json()
        assert manifest['available'] is True
        assert manifest['url'].endswith('/android/sgoshka-4.apk')
        assert response.headers['Cache-Control'] == 'no-store'
        response = await client.get('/mini/android/sgoshka-4.apk')
        assert response.status == 200 and await response.read() == apk
        assert response.headers['Cache-Control'] == 'no-store'
        response = await client.get('/mini/android/update.json.apk')
        assert response.status == 404
        response = await client.get('/mini/android/sgoshka-4.apk', headers={'Range': 'bytes=0-6'})
        assert response.status == 206 and await response.read() == apk[:7]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_owner_can_pause_updates_but_still_download_private_apk(app_context, tmp_path):
    from tests.test_repositories import make_user
    from app.db.repositories import AppFlagRepository
    from app.web.api.base import issue_attachment_ticket
    app_context.settings = replace(app_context.settings, data_dir=tmp_path)
    for uid in (1, 5):
        await app_context.users.save(make_user(uid))
    admin = await app_context.miniapp.issue_token(1)
    other = await app_context.miniapp.issue_token(5)
    apk = publish(tmp_path / 'android-releases')
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    try:
        for endpoint in ('android-updates', 'android-link'):
            response = await client.post('/mini/api/admin/' + endpoint, headers={'X-Netschool-Token': other}, json={'on': False})
            assert response.status == 403
        response = await client.post('/mini/api/admin/android-updates', headers={'X-Netschool-Token': admin}, json={'on': 'off'})
        assert response.status == 400
        response = await client.post('/mini/api/admin/android-updates', headers={'X-Netschool-Token': admin}, json={'on': False})
        assert response.status == 200 and (await response.json())['data']['android_updates']['on'] is False
        assert await AppFlagRepository(app_context.db).get('android_updates_disabled') is True
        assert await (await client.get('/mini/android/update.json')).json() == {'available': False}
        assert (await client.get('/mini/android/sgoshka-4.apk')).status == 403
        assert (await client.get('/mini/api/admin/android.apk', headers={'X-Netschool-Token': other})).status == 403
        response = await client.get('/mini/api/admin/android.apk', headers={'X-Netschool-Token': admin})
        assert response.status == 200 and await response.read() == apk
        response = await client.post('/mini/api/admin/android-link', headers={'X-Netschool-Token': admin})
        assert response.status == 200 and 'ticket=' in (await response.json())['data']['url']
        ticket = issue_attachment_ticket(app_context, 1)
        response = await client.get('/mini/api/admin/android.apk', params={'ticket': ticket})
        assert response.status == 200 and await response.read() == apk
        ticket = issue_attachment_ticket(app_context, 5)
        assert (await client.get('/mini/api/admin/android.apk', params={'ticket': ticket})).status == 403
        response = await client.post('/mini/api/admin/android-updates', headers={'X-Netschool-Token': admin}, json={'on': True})
        assert (await response.json())['data']['android_updates']['on'] is True
        assert (await (await client.get('/mini/android/update.json')).json())['available'] is True
    finally:
        await client.close()


async def test_update_link_stays_on_the_site_that_was_asked(app_context, tmp_path):
    web_settings = replace(app_context.settings.web, public_url='https://new.example', legacy_urls=('https://old.example',))
    app_context.settings = replace(app_context.settings, data_dir=tmp_path, web=web_settings)
    publish(tmp_path / 'android-releases')
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    try:
        for host, expected in (('old.example', 'https://old.example'), ('new.example', 'https://new.example'),
                               ('evil.example', 'https://new.example')):
            response = await client.get('/mini/android/update.json', headers={'Host': host})
            assert (await response.json())['url'] == f'{expected}/mini/android/sgoshka-4.apk'
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_selected_build_can_be_blocked_and_allowed_only_by_owner(app_context, tmp_path):
    from tests.test_repositories import make_user
    app_context.settings = replace(app_context.settings, data_dir=tmp_path)
    for uid in (1, 5):
        await app_context.users.save(make_user(uid))
    admin = {'X-Netschool-Token': await app_context.miniapp.issue_token(1)}
    other = {'X-Netschool-Token': await app_context.miniapp.issue_token(5)}
    root = tmp_path / 'android-releases'
    publish(root)
    (root / 'sgoshka-2.apk').write_bytes(b'old apk')
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    try:
        policy = await client.get('/mini/android/policy.json')
        assert policy.status == 200 and policy.headers['Cache-Control'] == 'no-store'
        assert (await policy.json())['blockedVersionCodes'] == []
        endpoint = '/mini/api/admin/android-version'
        body = {'versionCode': 2, 'blocked': True}
        assert (await client.post(endpoint, headers=other, json=body)).status == 403
        assert (await client.post(endpoint, json=body)).status != 200
        for bad in ({'versionCode': True, 'blocked': True}, {'versionCode': 0, 'blocked': True},
                    {'versionCode': 2, 'blocked': 'yes'}, {'versionCode': 2100000001, 'blocked': True}):
            assert (await client.post(endpoint, headers=admin, json=bad)).status == 400
        # The latest/newer version cannot be blocked: there must be an upgrade path.
        for code in (4, 5):
            assert (await client.post(endpoint, headers=admin, json={'versionCode': code, 'blocked': True})).status == 400
        result = await client.post(endpoint, headers=admin, json=body)
        assert result.status == 200
        assert (await result.json())['data']['android_updates']['blockedVersionCodes'] == [2]
        policy = await (await client.get('/mini/android/policy.json')).json()
        assert policy == {'packageName': 'ru.ikrya.sgoshka', 'blockedVersionCodes': [2]}
        assert (await client.get('/mini/android/sgoshka-2.apk')).status == 410
        assert (await client.get('/mini/android/sgoshka-4.apk')).status == 200
        assert (await client.post('/mini/api/admin/android-updates', headers=admin, json={'on': False})).status == 400
        result = await client.post(endpoint, headers=admin, json={'versionCode': 2, 'blocked': False})
        assert result.status == 200
        assert (await (await client.get('/mini/android/policy.json')).json())['blockedVersionCodes'] == []
        assert (await client.get('/mini/android/sgoshka-2.apk')).status == 200
        assert (await client.post('/mini/api/admin/android-updates', headers=admin, json={'on': False})).status == 200
        assert (await client.post(endpoint, headers=admin, json=body)).status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_policy_is_independent_of_channel_and_missing_release_never_permits_blocking(app_context, tmp_path):
    from tests.test_repositories import make_user
    from app.db.repositories import AppFlagRepository
    app_context.settings = replace(app_context.settings, data_dir=tmp_path)
    await app_context.users.save(make_user(1))
    headers = {'X-Netschool-Token': await app_context.miniapp.issue_token(1)}
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    try:
        endpoint = '/mini/api/admin/android-version'
        assert (await client.post(endpoint, headers=headers, json={'versionCode': 2, 'blocked': True})).status == 400
        await AppFlagRepository(app_context.db).set('android_version_blocked:2', True)
        await AppFlagRepository(app_context.db).set('android_updates_disabled', True)
        assert (await (await client.get('/mini/android/policy.json')).json())['blockedVersionCodes'] == [2]
        # Recovery remains possible even if publication has been removed or the channel was disabled externally.
        assert (await client.post(endpoint, headers=headers, json={'versionCode': 2, 'blocked': False})).status == 200
        assert (await (await client.get('/mini/android/policy.json')).json())['blockedVersionCodes'] == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_concurrent_channel_pause_and_version_block_cannot_strand_clients(app_context, tmp_path):
    import asyncio
    from tests.test_repositories import make_user
    from app.web.android_updates import blocked_versions, updates_enabled
    app_context.settings = replace(app_context.settings, data_dir=tmp_path)
    await app_context.users.save(make_user(1))
    headers = {'X-Netschool-Token': await app_context.miniapp.issue_token(1)}
    publish(tmp_path / 'android-releases')
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    try:
        replies = await asyncio.gather(
            client.post('/mini/api/admin/android-updates', headers=headers, json={'on': False}),
            client.post('/mini/api/admin/android-version', headers=headers, json={'versionCode': 2, 'blocked': True}),
        )
        assert sorted(response.status for response in replies) == [200, 400]
        assert not await blocked_versions(app_context) or await updates_enabled(app_context)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_named_release_uses_primary_domain_and_keeps_legacy_updater_compatible(app_context, tmp_path):
    from app.db.repositories import AppFlagRepository
    web_settings = replace(app_context.settings.web, public_url='https://sgoshka.ru', legacy_urls=('https://ns.ikrya.ru',))
    app_context.settings = replace(app_context.settings, data_dir=tmp_path, web=web_settings)
    root = tmp_path / 'android-releases'
    apk = publish(root)
    data = json.loads((root / 'update.json').read_text())
    data['file'] = 'sgoshka-v.0.4.0.apk'
    (root / data['file']).write_bytes(apk)
    (root / 'sgoshka-4.apk').unlink()
    for name in ('update.json', 'sgoshka-4.json', 'sgoshka-v.0.4.0.json'):
        (root / name).write_text(json.dumps(data))
    client = TestClient(TestServer(create_app(app_context)))
    await client.start_server()
    try:
        response = await client.get('/mini/android/update.json', headers={'Host': 'sgoshka.ru'})
        result = await response.json()
        assert result['url'] == 'https://sgoshka.ru/mini/android/sgoshka-v.0.4.0.apk'
        assert result['file'] == 'sgoshka-v.0.4.0.apk'
        assert '\\u' not in await response.text()
        legacy = await (await client.get('/mini/android/update.json', headers={'Host': 'ns.ikrya.ru'})).json()
        assert legacy['url'] == 'https://ns.ikrya.ru/mini/android/sgoshka-4.apk'
        for name in ('sgoshka-4.apk', 'sgoshka-v.0.4.0.apk'):
            response = await client.get('/mini/android/' + name)
            assert response.status == 200 and await response.read() == apk
            assert 'sgoshka-v.0.4.0.apk' in response.headers['Content-Disposition']
        await AppFlagRepository(app_context.db).set('android_version_blocked:4', True)
        for name in ('sgoshka-4.apk', 'sgoshka-v.0.4.0.apk'):
            assert (await client.get('/mini/android/' + name)).status == 410
        assert (await client.get('/mini/android/sgoshka-v.0.4.1.apk')).status == 404
    finally:
        await client.close()
