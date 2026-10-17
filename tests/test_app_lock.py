from tests.test_web import client, token, app_context, BASE  # noqa: F401
import pytest


async def enable(client, token):
    response = await client.post(f'{BASE}/api/lock/password', headers={'X-Netschool-Token': token}, json={'password': '1234'})
    assert response.status == 200
    return (await response.json())['data']['unlock']


async def test_lock_enforces_password_and_revokes_session(client, token):
    session = await enable(client, token)
    headers = {'X-Netschool-Token': token}
    response = await client.get(f'{BASE}/api/profile', headers=headers)
    assert response.status == 423
    assert (await response.json())['reason'] == 'locked'
    assert (await client.post(f'{BASE}/api/lock/disable', headers=headers)).status == 423
    assert (await client.post(f'{BASE}/api/lock/register/options', headers=headers)).status == 423
    headers['X-Netschool-Unlock'] = session
    assert (await client.get(f'{BASE}/api/profile', headers=headers)).status == 200
    await client.post(f'{BASE}/api/lock/logout', headers=headers)
    assert (await client.get(f'{BASE}/api/profile', headers=headers)).status == 423
    response = await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'password': '1234'})
    headers['X-Netschool-Unlock'] = (await response.json())['data']['unlock']
    assert (await client.post(f'{BASE}/api/lock/disable', headers=headers)).status == 200
    headers.pop('X-Netschool-Unlock')
    assert (await client.get(f'{BASE}/api/profile', headers=headers)).status == 200


async def test_wrong_password_rate_limit_and_no_plaintext(client, token, app_context):
    await enable(client, token)
    headers = {'X-Netschool-Token': token}
    # Одна опечатка не мешает сразу ввести верный PIN.
    assert (await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'password': '0000'})).status == 403
    assert (await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'password': '1234'})).status == 200
    for _ in range(4):
        assert (await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'password': '0000'})).status == 403
    response = await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'password': '0000'})
    assert response.status == 403 and 'через 30 с' in (await response.json())['error']
    response = await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'password': '1234'})
    assert response.status == 429 and 'Попробуйте через' in (await response.json())['error']
    row = await app_context.db.fetch_one('SELECT * FROM app_locks WHERE telegram_id=1')
    assert row['password_hash'] != b'1234'


async def test_session_bound_to_user_and_password_change(client, token, app_context, users):
    from tests.test_repositories import make_user
    session = await enable(client, token)
    await users.save(make_user(telegram_id=2))
    token2 = await app_context.miniapp.issue_token(2)
    await enable(client, token2)
    headers = {'X-Netschool-Token': token2, 'X-Netschool-Unlock': session}
    assert (await client.get(f'{BASE}/api/profile', headers=headers)).status == 423
    headers['X-Netschool-Token'] = token
    response = await client.post(f'{BASE}/api/lock/password', headers=headers, json={'password': '5678'})
    assert response.status == 200
    assert (await client.get(f'{BASE}/api/profile', headers=headers)).status == 423


async def test_webauthn_options_and_invalid_assertion(client, token):
    session = await enable(client, token)
    headers = {'X-Netschool-Token': token, 'X-Netschool-Unlock': session, 'Origin': 'https://example.ru'}
    response = await client.post(f'{BASE}/api/lock/register/options', headers=headers, json={})
    assert response.status == 200
    data = (await response.json())['data']
    assert data['options']['authenticatorSelection']['userVerification'] == 'required'
    assert data['options']['rp']['id'] == 'example.ru'
    response = await client.post(f'{BASE}/api/lock/register/verify', headers=headers, json={'id': data['id'], 'credential': {}})
    assert response.status == 403
    response = await client.post(f'{BASE}/api/lock/register/verify', headers=headers, json={'id': data['id'], 'credential': {}})
    assert response.status == 403
    headers['Origin'] = 'https://evil.example'
    assert (await client.post(f'{BASE}/api/lock/register/options', headers=headers, json={})).status == 403


async def test_page_does_not_expose_student_when_locked(client, token):
    await enable(client, token)
    response = await client.get(f'{BASE}/', params={'token': token})
    assert 'Иванов И.' not in await response.text()

async def test_valid_passkey_unlock_and_replay_rejected(client, token):
    import base64
    import hashlib
    import json
    import secrets
    import struct
    import cbor2
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes

    def b64(value): return base64.urlsafe_b64encode(value).rstrip(b'=').decode()
    session = await enable(client, token)
    headers = {'X-Netschool-Token': token, 'X-Netschool-Unlock': session, 'Origin': 'https://example.ru'}
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_numbers()
    cid = secrets.token_bytes(32)
    cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, 'big'), -3: public.y.to_bytes(32, 'big')})
    rp_hash = hashlib.sha256(b'example.ru').digest()
    response = await client.post(f'{BASE}/api/lock/register/options', headers=headers, json={})
    data = (await response.json())['data']
    client_data = json.dumps({'type': 'webauthn.create', 'challenge': data['options']['challenge'], 'origin': headers['Origin']}).encode()
    auth_data = rp_hash + b'\x45' + struct.pack('>I', 0) + b'\0'*16 + struct.pack('>H', len(cid)) + cid + cose
    attestation = cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': auth_data})
    credential = {'id': b64(cid), 'rawId': b64(cid), 'type': 'public-key',
                  'response': {'clientDataJSON': b64(client_data), 'attestationObject': b64(attestation)}}
    response = await client.post(f'{BASE}/api/lock/register/verify', headers=headers, json={'id': data['id'], 'credential': credential})
    assert response.status == 200, await response.text()
    # Список ключей сайта нужен приложению для входа без сети.
    status = await client.get(f'{BASE}/api/lock/status', headers=headers, params={'origin': headers['Origin']})
    assert (await status.json())['data']['passkey_ids'] == [b64(cid)]
    status = await client.get(f'{BASE}/api/lock/status', headers=headers, params={'origin': 'https://evil.example'})
    assert (await status.json())['data']['passkey_ids'] == []
    await client.post(f'{BASE}/api/lock/logout', headers=headers)
    headers.pop('X-Netschool-Unlock')
    response = await client.post(f'{BASE}/api/lock/authenticate/options', headers=headers, json={})
    data = (await response.json())['data']
    client_data = json.dumps({'type': 'webauthn.get', 'challenge': data['options']['challenge'], 'origin': headers['Origin']}).encode()
    auth_data = rp_hash + b'\x05' + struct.pack('>I', 1)
    signature = private.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
    credential['response'] = {'clientDataJSON': b64(client_data), 'authenticatorData': b64(auth_data), 'signature': b64(signature)}
    body = {'id': data['id'], 'credential': credential}
    response = await client.post(f'{BASE}/api/lock/authenticate/verify', headers=headers, json=body)
    assert response.status == 200, await response.text()
    headers['X-Netschool-Unlock'] = (await response.json())['data']['unlock']
    assert (await client.get(f'{BASE}/api/profile', headers=headers)).status == 200
    assert (await client.post(f'{BASE}/api/lock/authenticate/verify', headers=headers, json=body)).status == 403

async def test_bot_reset_removes_password_and_sessions(client, token, app_context, users):
    from app.bot.handlers.settings import reset_app_lock
    from tests.test_bot_handlers import FakeMessage
    await enable(client, token)
    await reset_app_lock(FakeMessage(), await users.get(1), app_context)
    assert not await app_context.db.fetch_one('SELECT 1 FROM app_locks')
    assert not await app_context.db.fetch_one('SELECT 1 FROM app_unlocks')
    assert (await client.get(f'{BASE}/api/profile', headers={'X-Netschool-Token': token})).status == 200

async def test_native_biometric_secret_requires_unlock_and_is_revoked(client, token, app_context):
    session = await enable(client, token)
    headers = {'X-Netschool-Token': token}
    assert (await client.post(f'{BASE}/api/lock/biometric/register', headers=headers, json={'device': 'iphone'})).status == 423
    headers['X-Netschool-Unlock'] = session
    response = await client.post(f'{BASE}/api/lock/biometric/register', headers=headers, json={'device': 'iphone'})
    secret = (await response.json())['data']['token']
    row = await app_context.db.fetch_one('SELECT token_hash FROM app_biometrics')
    assert secret not in row['token_hash']
    await client.post(f'{BASE}/api/lock/logout', headers=headers)
    headers.pop('X-Netschool-Unlock')
    for device, value in [('iphone', 'invalid'), ('other', secret)]:
        response = await client.post(f'{BASE}/api/lock/biometric/unlock', headers=headers, json={'device': device, 'token': value})
        assert response.status == 403
    response = await client.post(f'{BASE}/api/lock/biometric/unlock', headers=headers, json={'device': 'iphone', 'token': secret})
    assert response.status == 200
    headers['X-Netschool-Unlock'] = (await response.json())['data']['unlock']
    assert (await client.get(f'{BASE}/api/profile', headers=headers)).status == 200
    await client.post(f'{BASE}/api/lock/disable', headers=headers)
    response = await client.post(f'{BASE}/api/lock/biometric/unlock', headers=headers, json={'device': 'iphone', 'token': secret})
    assert response.status == 403

async def test_biometric_toggle_revokes_only_current_device(client, token, app_context):
    session = await enable(client, token)
    headers = {'X-Netschool-Token': token, 'X-Netschool-Unlock': session}
    secrets = {}
    for device in ('iphone', 'ipad'):
        response = await client.post(f'{BASE}/api/lock/biometric/register', headers=headers, json={'device': device})
        secrets[device] = (await response.json())['data']['token']
    response = await client.get(f'{BASE}/api/lock/status', headers=headers, params={'device': 'iphone'})
    assert (await response.json())['data']['biometric'] is True
    # This setting cannot be turned off from a locked page.
    assert (await client.post(f'{BASE}/api/lock/biometric/disable', headers={'X-Netschool-Token': token}, json={'device': 'iphone'})).status == 423
    response = await client.post(f'{BASE}/api/lock/biometric/disable', headers=headers, json={'device': 'iphone'})
    assert response.status == 200
    response = await client.get(f'{BASE}/api/lock/status', headers=headers, params={'device': 'iphone'})
    status = (await response.json())['data']
    assert status['biometric'] is False
    assert status['enabled'] is True
    for device, expected in [('iphone', 403), ('ipad', 200)]:
        response = await client.post(f'{BASE}/api/lock/biometric/unlock', headers=headers, json={'device': device, 'token': secrets[device]})
        assert response.status == expected

@pytest.mark.parametrize('pin', ['123', '12345', 'abcd', '１２３４', '12 4', 1234, None])
async def test_pin_requires_exactly_four_ascii_digits(client, token, pin):
    response = await client.post(f'{BASE}/api/lock/pin', headers={'X-Netschool-Token': token}, json={'pin': pin})
    assert response.status == 400


async def test_pin_preserves_leading_zeros(client, token):
    headers = {'X-Netschool-Token': token}
    response = await client.post(f'{BASE}/api/lock/pin', headers=headers, json={'pin': '0042'})
    assert response.status == 200
    status = await client.get(f'{BASE}/api/lock/status', headers=headers)
    assert (await status.json())['data']['pin'] is True
    response = await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'pin': '0042'})
    assert response.status == 200


async def test_legacy_password_can_be_replaced_without_losing_access(client, token, app_context):
    import hashlib
    salt = b'0123456789abcdef'
    hashed = hashlib.scrypt(b'old-password', salt=salt, n=16384, r=8, p=1)
    await app_context.db.execute('INSERT INTO app_locks (telegram_id, salt, password_hash) VALUES (?, ?, ?)', (1, salt, hashed))
    headers = {'X-Netschool-Token': token}
    response = await client.get(f'{BASE}/api/lock/status', headers=headers)
    assert (await response.json())['data']['pin'] is False
    response = await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'password': 'old-password'})
    assert response.status == 200
    headers['X-Netschool-Unlock'] = (await response.json())['data']['unlock']
    response = await client.post(f'{BASE}/api/lock/pin', headers=headers, json={'pin': '0042'})
    assert response.status == 200
    headers.pop('X-Netschool-Unlock')
    assert (await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'pin': '0042'})).status == 200
    assert (await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'password': 'old-password'})).status == 403


async def test_passkey_toggle_revokes_keys_and_pending_registration(client, token, app_context):
    session = await enable(client, token)
    headers = {'X-Netschool-Token': token, 'X-Netschool-Unlock': session, 'Origin': 'https://example.ru'}
    await app_context.db.execute('INSERT INTO app_passkeys VALUES (?, ?, ?, ?, ?)', (b'key1', 1, b'pubkey', 0, 'example.ru'))
    await app_context.db.execute('INSERT INTO app_passkeys VALUES (?, ?, ?, ?, ?)', (b'key2', 1, b'pubkey', 0, 'other.example'))
    response = await client.post(f'{BASE}/api/lock/register/options', headers=headers, json={})
    challenge = (await response.json())['data']['id']
    assert (await client.post(f'{BASE}/api/lock/passkey/disable', headers={'X-Netschool-Token': token, 'Origin': 'https://example.ru'})).status == 423
    assert (await client.post(f'{BASE}/api/lock/passkey/disable', headers=headers)).status == 200
    assert not await app_context.db.fetch_one('SELECT 1 FROM app_passkeys WHERE rp_id=?', ('example.ru',))
    assert await app_context.db.fetch_one('SELECT 1 FROM app_passkeys WHERE rp_id=?', ('other.example',))
    assert not await app_context.db.fetch_one('SELECT 1 FROM app_lock_challenges WHERE id=?', (challenge,))
    response = await client.get(f'{BASE}/api/lock/status', headers=headers, params={'origin': 'https://example.ru'})
    assert (await response.json())['data']['passkeys'] == 0
    assert (await client.post(f'{BASE}/api/lock/authenticate/options', headers=headers, json={})).status == 400


async def test_telegram_pin_toggle_only_bypasses_verified_telegram(client, token):
    from tests.test_web import TestTelegramMiniApp
    session = await enable(client, token)
    web_headers = {'X-Netschool-Token': token}
    tg_headers = {'X-Telegram-Init-Data': TestTelegramMiniApp.init_data(1)}
    assert (await client.get(f'{BASE}/api/profile', headers=tg_headers)).status == 423
    assert (await client.post(f'{BASE}/api/lock/settings', headers=web_headers,
                              json={'require_telegram': False})).status == 423
    confirmed = {**web_headers, 'X-Netschool-Unlock': session}
    assert (await client.post(f'{BASE}/api/lock/settings', headers=confirmed,
                              json={'require_telegram': False})).status == 200
    response = await client.get(f'{BASE}/api/lock/status', headers=tg_headers)
    status = (await response.json())['data']
    assert status['enabled'] and status['pin'] and not status['required'] and not status['require_telegram']
    assert (await client.get(f'{BASE}/api/profile', headers=tg_headers)).status == 200
    assert (await client.get(f'{BASE}/api/profile', headers=web_headers)).status == 423
    forged = {**web_headers, 'X-Telegram-Init-Data': TestTelegramMiniApp.init_data(1, token='forged')}
    assert (await client.get(f'{BASE}/api/profile', headers=forged)).status == 423
    response = await client.post(f'{BASE}/api/lock/settings', headers=tg_headers,
                                 json={'require_telegram': True})
    assert response.status == 200
    # Переключение не обрывает текущий вход, но новый запрос без сессии требует PIN.
    current = {**tg_headers, 'X-Netschool-Unlock': (await response.json())['data']['unlock']}
    assert (await client.get(f'{BASE}/api/profile', headers=current)).status == 200
    assert (await client.get(f'{BASE}/api/profile', headers=tg_headers)).status == 423
    response = await client.get(f'{BASE}/api/lock/status', headers=tg_headers)
    assert (await response.json())['data']['required']


async def test_telegram_pin_toggle_rejects_non_boolean(client, token):
    session = await enable(client, token)
    headers = {'X-Netschool-Token': token, 'X-Netschool-Unlock': session}
    for value in ('false', 0, None):
        response = await client.post(f'{BASE}/api/lock/settings', headers=headers,
                                     json={'require_telegram': value})
        assert response.status == 400


async def test_pin_blocked_after_max_failures_until_biometric_unlock(client, token, app_context):
    session = await enable(client, token)
    headers = {'X-Netschool-Token': token, 'X-Netschool-Unlock': session}
    response = await client.post(f'{BASE}/api/lock/biometric/register', headers=headers, json={'device': 'iphone'})
    secret = (await response.json())['data']['token']
    await client.post(f'{BASE}/api/lock/logout', headers=headers)
    headers.pop('X-Netschool-Unlock')
    # Без пауз: доводим счётчик почти до предела напрямую.
    await app_context.db.execute('UPDATE app_locks SET failures=9, retry_at=0')
    response = await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'pin': '0000'})
    assert response.status == 403 and (await response.json())['reason'] == 'pin_blocked'
    await app_context.db.execute('UPDATE app_locks SET retry_at=0')
    response = await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'pin': '1234'})
    assert response.status == 429 and (await response.json())['reason'] == 'pin_blocked'
    response = await client.post(f'{BASE}/api/lock/biometric/unlock', headers=headers, json={'device': 'iphone', 'token': secret})
    assert response.status == 200
    assert (await client.post(f'{BASE}/api/lock/unlock', headers=headers, json={'pin': '1234'})).status == 200


async def test_sign_out_works_from_locked_screen(client, token, app_context):
    # «Выйти из аккаунта» на экране PIN: дневник закрыт, но выход проходит
    # и отзывает токен этого устройства.
    device = await app_context.miniapp.issue_token(1, device=True)
    await enable(client, device)
    headers = {'X-Netschool-Token': device}
    assert (await client.get(f'{BASE}/api/profile', headers=headers)).status == 423
    response = await client.post(f'{BASE}/api/account/logout', headers=headers, json={})
    assert response.status == 200 and (await response.json())['data']['revoked']
    assert await app_context.miniapp.resolve_token(device) is None
    assert await app_context.miniapp.resolve_token(token) == 1
