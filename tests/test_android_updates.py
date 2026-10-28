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
