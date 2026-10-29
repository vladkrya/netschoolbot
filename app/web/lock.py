"""Server-enforced app lock: password and verified platform passkeys."""
from __future__ import annotations

import asyncio
from base64 import urlsafe_b64encode
import hashlib
import hmac
import json
import math
import secrets
import re
import time
from urllib.parse import urlsplit

from aiohttp import web
from webauthn import (generate_registration_options, generate_authentication_options,
                      verify_registration_response, verify_authentication_response)
from webauthn.helpers import options_to_json
from webauthn.helpers.structs import (AuthenticatorAttachment, AuthenticatorSelectionCriteria,
                                     PublicKeyCredentialDescriptor, UserVerificationRequirement)
from webauthn.helpers.exceptions import WebAuthnException

from .api import context, current_user, json_error

routes = web.RouteTableDef()
PUBLIC = {'status', 'unlock', 'authenticate/options', 'authenticate/verify', 'biometric/unlock', 'password-reset'}


# Клиент шлёт не цифры PIN, а их SHA-256 (pin_key): в запросе, логах и инструментах
# разработчика PIN не виден. Защиты это не добавляет — вариантов всего 10 000, —
# поэтому настоящая защита по-прежнему в паузах и блокировке после ошибок.
# Старые клиенты шлют `pin` открытым текстом: сервер принимает оба вида, а
# хранится по-прежнему scrypt от самих цифр.
PIN_KEYS = {hashlib.sha256(f'sgoshka-pin-v1:{i:04d}'.encode()).hexdigest(): f'{i:04d}' for i in range(10000)}


def secret(data, field='pin', fallback=''):
    """PIN из `<field>_key` (хеш) или из `<field>` открытым текстом."""
    if f'{field}_key' in data:
        key = data[f'{field}_key']
        return PIN_KEYS.get(key, '') if isinstance(key, str) else ''
    return data.get(field, fallback)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


async def enabled(db, uid):
    return await db.fetch_one('SELECT * FROM app_locks WHERE telegram_id = ?', (uid,))


async def unlocked(request):
    token = request.headers.get('X-Netschool-Unlock', '')
    return bool(token and await context(request).db.fetch_one(
        'SELECT 1 FROM app_unlocks WHERE token_hash = ? AND telegram_id = ? AND expires_at > ?',
        (digest(token), current_user(request).telegram_id, time.time())))


async def guard(request):
    uid = current_user(request).telegram_id
    row = await enabled(context(request).db, uid)
    if not row or (request.get('verified_telegram') and not row['require_telegram']):
        return None
    suffix = request.path.split('/api/lock/', 1)
    if len(suffix) == 2 and suffix[1] in PUBLIC:
        return None
    # «Выйти из аккаунта» с экрана PIN: выход только отзывает токен этого
    # устройства и ничего из дневника не отдаёт.
    if request.path.rstrip('/').endswith('/api/account/logout'):
        return None
    if not await unlocked(request):
        return json_error('Разблокируйте дневник', status=423, reason='locked')
    return None


async def session(request):
    token = secrets.token_urlsafe(32)
    db = context(request).db
    await db.execute('DELETE FROM app_unlocks WHERE expires_at < ?', (time.time(),))
    await db.execute('INSERT INTO app_unlocks VALUES (?, ?, ?)',
                     (digest(token), current_user(request).telegram_id, time.time() + 12 * 3600))
    # Владелец подтвердился (PIN, ключ доступа, биометрия) — счётчик ошибок с нуля.
    await db.execute('UPDATE app_locks SET failures=0, retry_at=0 WHERE telegram_id=?', (current_user(request).telegram_id,))
    return web.json_response({'ok': True, 'data': {'unlock': token}})


# Опечатки не наказываем: первые попытки без паузы, дальше пауза растёт до
# 5 минут. Раньше пауза была уже после первой ошибки, и верный PIN, введённый
# сразу следом, получал «Слишком много попыток».
FREE_ATTEMPTS = 5
# После MAX_ATTEMPTS ошибок подряд PIN не принимается, пока владелец не
# войдёт по биометрии или не сбросит защиту (/resetpin, пароль от аккаунта).
MAX_ATTEMPTS = 10
BLOCKED = ('Вход по PIN заблокирован после {} ошибок подряд. Войдите по Face ID / Touch ID '
           'или сбросьте PIN: «Забыли PIN?» или /resetpin в чате с ботом.'.format(MAX_ATTEMPTS))


def _backoff(failures: int) -> int:
    return 0 if failures < FREE_ATTEMPTS else min(300, 30 * 2 ** (failures - FREE_ATTEMPTS))


def _duration(seconds: int) -> str:
    return f'{seconds} с' if seconds < 60 else f'{math.ceil(seconds / 60)} мин'


@routes.get('/api/lock/status')
async def status(request):
    db, uid = context(request).db, current_user(request).telegram_id
    origin = request.query.get('origin', '')
    allowed = context(request).settings.web.all_urls
    rp = urlsplit(origin).hostname if origin and origin in [url.rstrip('/') for url in allowed if url] else None
    keys = await db.fetch_one('SELECT COUNT(*) AS n FROM app_passkeys WHERE telegram_id = ?' +
                             (' AND rp_id = ?' if rp else ''), (uid, rp) if rp else (uid,))
    # Ключи этого сайта: без сети приложение пускает только по ним, а список
    # на устройстве стирается вместе с офлайн-данными. Онлайн-проверка
    # защиты каждый раз его восстанавливает.
    ids = [urlsafe_b64encode(row['credential_id']).rstrip(b'=').decode() for row in await db.fetch_all(
        'SELECT credential_id FROM app_passkeys WHERE telegram_id=? AND rp_id=?', (uid, rp))] if rp else []
    row = await enabled(db, uid)
    device = request.query.get('device', '')
    biometric = await db.fetch_one('SELECT 1 FROM app_biometrics WHERE telegram_id=? AND device_id=?', (uid, device)) if device else None
    return web.json_response({'ok': True, 'data': {
        'enabled': bool(row), 'required': bool(row and (not request.get('verified_telegram') or row['require_telegram'])),
        'require_telegram': bool(row and row['require_telegram']),
        'pin': bool(row and row['is_pin']), 'passkeys': keys['n'], 'passkey_ids': ids, 'biometric': bool(biometric),
        # Забытый PIN можно сбросить паролем, только если аккаунт с паролем есть.
        'password_reset': bool(await context(request).accounts.by_user(uid))}})


@routes.post('/api/lock/settings')
async def lock_settings(request):
    data = await request.json()
    wanted = data.get('require_telegram')
    if not isinstance(wanted, bool):
        return json_error('Укажите, нужен ли PIN в Telegram')
    db, uid = context(request).db, current_user(request).telegram_id
    if not await enabled(db, uid):
        return json_error('Сначала задайте PIN')
    await db.execute('UPDATE app_locks SET require_telegram=? WHERE telegram_id=?', (int(wanted), uid))
    # При включении текущий подтверждённый вход не обрывается; следующий требует PIN.
    return await session(request)


async def _check(conn, row, value, uid):
    """Сверить PIN (или старый пароль защиты) со счётчиком ошибок и паузами.

    Возвращает ответ с ошибкой или None, если код верный. Вызывать внутри
    транзакции: параллельные попытки не обходят паузу.
    """
    wrong = 'Неверный PIN' if row['is_pin'] else 'Неверный пароль'
    if not isinstance(value, str) or len(value) > 128:
        return json_error(wrong, status=403)
    if row['is_pin'] and not re.fullmatch(r'[0-9]{4}', value):
        return json_error('Введите 4 цифры PIN', status=403)
    if row['failures'] >= MAX_ATTEMPTS:
        return json_error(BLOCKED, status=429, reason='pin_blocked')
    wait = math.ceil(row['retry_at'] - time.time())
    if wait > 0:
        return json_error(f'Слишком много попыток. Попробуйте через {_duration(wait)}.', status=429)
    hashed = await asyncio.to_thread(hashlib.scrypt, value.encode(), salt=row['salt'], n=16384, r=8, p=1)
    if hmac.compare_digest(hashed, row['password_hash']):
        return None
    failures = row['failures'] + 1
    pause = _backoff(failures)
    await conn.execute('UPDATE app_locks SET failures=?, retry_at=? WHERE telegram_id=?',
                       (failures, time.time() + pause, uid))
    if failures >= MAX_ATTEMPTS:
        return json_error(f'{wrong}. {BLOCKED}', status=403, reason='pin_blocked')
    if pause:
        return json_error(f'{wrong}. Следующая попытка через {_duration(pause)}.', status=403)
    left = FREE_ATTEMPTS - failures
    return json_error(f'{wrong}. Осталось попыток без паузы: {left}.' if left <= 2 else wrong, status=403)


async def _current(conn, uid):
    cursor = await conn.execute('SELECT * FROM app_locks WHERE telegram_id = ?', (uid,))
    return await cursor.fetchone()


@routes.post('/api/lock/pin')
@routes.post('/api/lock/password')
async def password(request):
    data = await request.json()
    value = secret(data, 'pin', data.get('password', ''))
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}', value):
        return json_error('PIN должен состоять ровно из 4 цифр')
    salt = secrets.token_bytes(16)
    hashed = await asyncio.to_thread(hashlib.scrypt, value.encode(), salt=salt, n=16384, r=8, p=1)
    db, uid = context(request).db, current_user(request).telegram_id
    async with db.transaction() as conn:
        # Сменить PIN можно, только зная прежний: открытый на минуту телефон
        # не даёт чужому задать свой PIN. Забытый PIN сбрасывают /resetpin
        # или паролем аккаунта — после сброса защиты старый не нужен.
        row = await _current(conn, uid)
        if row and (problem := await _check(conn, row, secret(data, 'current'), uid)):
            return problem
        await conn.execute('INSERT INTO app_locks (telegram_id, salt, password_hash, is_pin) VALUES (?, ?, ?, 1) '
                           'ON CONFLICT(telegram_id) DO UPDATE SET salt=excluded.salt, password_hash=excluded.password_hash, failures=0, retry_at=0, is_pin=1',
                           (uid, salt, hashed))
        await conn.execute('DELETE FROM app_unlocks WHERE telegram_id = ?', (uid,))
    return await session(request)


@routes.post('/api/lock/unlock')
async def unlock(request):
    db, uid = context(request).db, current_user(request).telegram_id
    if not await enabled(db, uid):
        return json_error('Блокировка не включена')
    data = await request.json()
    value = secret(data, 'pin', data.get('password', ''))
    # Serialize attempts, including hashing: concurrent requests cannot bypass backoff.
    async with db.transaction() as conn:
        row = await _current(conn, uid)
        if not row:
            return json_error('Блокировка не включена')
        if problem := await _check(conn, row, value, uid):
            return problem
    return await session(request)


@routes.post('/api/lock/disable')
async def disable(request):
    db, uid = context(request).db, current_user(request).telegram_id
    data = await request.json() if request.can_read_body else {}
    async with db.transaction() as conn:
        # Выключить защиту — тоже только с текущим PIN.
        row = await _current(conn, uid)
        if row and (problem := await _check(conn, row, secret(data, 'pin'), uid)):
            return problem
        for table in ('app_locks', 'app_unlocks', 'app_passkeys', 'app_biometrics', 'app_lock_challenges'):
            await conn.execute(f'DELETE FROM {table} WHERE telegram_id = ?', (uid,))
    return web.json_response({'ok': True, 'data': {}})


@routes.post('/api/lock/logout')
async def logout(request):
    await context(request).db.execute('DELETE FROM app_unlocks WHERE token_hash=? AND telegram_id=?',
                                     (digest(request.headers.get('X-Netschool-Unlock', '')), current_user(request).telegram_id))
    return web.json_response({'ok': True, 'data': {}})


def site(request):
    # Use configured public URLs, never trust Host/forwarded headers for WebAuthn.
    settings = context(request).settings.web
    allowed = [urlsplit(url) for url in settings.all_urls]
    origin = request.headers.get('Origin', '')
    for parsed in allowed:
        expected = f'{parsed.scheme}://{parsed.netloc}'
        if origin == expected and parsed.scheme == 'https':
            return expected, parsed.hostname
    raise web.HTTPForbidden(text='Недопустимый адрес для биометрии')


async def options(request, kind):
    origin, rp = site(request)
    db, uid = context(request).db, current_user(request).telegram_id
    if not await enabled(db, uid):
        return json_error('Сначала установите PIN')
    keys = await db.fetch_all('SELECT credential_id FROM app_passkeys WHERE telegram_id=? AND rp_id=?', (uid, rp))
    descriptors = [PublicKeyCredentialDescriptor(id=row['credential_id']) for row in keys]
    if kind == 'register':
        # В ключе — номер аккаунта в приложении, а не Telegram ID: имя ключа
        # видно в списке паролей на телефоне. Уже созданные ключи сервер
        # узнаёт по их credential_id, поэтому они продолжают работать.
        number = await context(request).accounts.number(uid)
        label = f'СГОшка · ID {number}' if number else 'СГОшка'
        opts = generate_registration_options(rp_id=rp, rp_name='Школьный дневник',
            user_id=f'account-{number}'.encode() if number else secrets.token_bytes(16),
            user_name=label, user_display_name=label, exclude_credentials=descriptors,
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=AuthenticatorAttachment.PLATFORM,
                user_verification=UserVerificationRequirement.REQUIRED))
    else:
        if not keys:
            return json_error('Вход с биометрией ещё не настроен')
        opts = generate_authentication_options(rp_id=rp, allow_credentials=descriptors,
                                               user_verification=UserVerificationRequirement.REQUIRED)
    cid = secrets.token_urlsafe(32)
    await db.execute('DELETE FROM app_lock_challenges WHERE expires_at < ?', (time.time(),))
    await db.execute('INSERT INTO app_lock_challenges VALUES (?, ?, ?, ?, ?, ?, ?)',
                     (cid, uid, kind, opts.challenge, origin, rp, time.time()+120))
    return web.json_response({'ok': True, 'data': {'id': cid, 'options': json.loads(options_to_json(opts))}})


@routes.post('/api/lock/register/options')
async def register_options(request):
    return await options(request, 'register')


@routes.post('/api/lock/authenticate/options')
async def authentication_options(request):
    return await options(request, 'authenticate')


async def verify(request, kind):
    origin, rp = site(request)
    db, uid = context(request).db, current_user(request).telegram_id
    data = await request.json()
    async with db.transaction() as conn:
        cursor = await conn.execute('SELECT * FROM app_lock_challenges WHERE id=? AND telegram_id=? AND kind=?',
                                    (data.get('id', ''), uid, kind))
        row = await cursor.fetchone()
        if row:
            await conn.execute('DELETE FROM app_lock_challenges WHERE id=?', (row['id'],))
    if not row or row['expires_at'] < time.time() or row['origin'] != origin or row['rp_id'] != rp:
        return json_error('Запрос биометрии истёк. Попробуйте снова.', status=403)
    try:
        if kind == 'register':
            result = verify_registration_response(credential=data['credential'], expected_challenge=row['challenge'],
                        expected_rp_id=rp, expected_origin=origin, require_user_verification=True)
            await db.execute('INSERT INTO app_passkeys VALUES (?, ?, ?, ?, ?)',
                             (result.credential_id, uid, result.credential_public_key, result.sign_count, rp))
            return web.json_response({'ok': True, 'data': {}})
        from webauthn import base64url_to_bytes
        credential = data['credential']
        key = await db.fetch_one('SELECT * FROM app_passkeys WHERE credential_id=? AND telegram_id=? AND rp_id=?',
                                (base64url_to_bytes(credential['id']), uid, rp))
        if not key:
            return json_error('Неизвестный ключ', status=403)
        # Signature counter check and update are atomic.
        async with db.transaction() as conn:
            cursor = await conn.execute('SELECT sign_count FROM app_passkeys WHERE credential_id=?', (key['credential_id'],))
            count = (await cursor.fetchone())[0]
            result = verify_authentication_response(credential=credential, expected_challenge=row['challenge'],
                expected_rp_id=rp, expected_origin=origin, credential_public_key=key['public_key'],
                credential_current_sign_count=count, require_user_verification=True)
            await conn.execute('UPDATE app_passkeys SET sign_count=? WHERE credential_id=?',
                               (result.new_sign_count, key['credential_id']))
        return await session(request)
    except (WebAuthnException, ValueError, TypeError, KeyError):
        return json_error('Биометрия не подтверждена', status=403)


@routes.post('/api/lock/register/verify')
async def register_verify(request):
    return await verify(request, 'register')


@routes.post('/api/lock/authenticate/verify')
async def authentication_verify(request):
    return await verify(request, 'authenticate')


@routes.post('/api/lock/biometric/register')
async def biometric_register(request):
    """Random unlock secret held by Telegram's native biometric storage."""
    db, uid = context(request).db, current_user(request).telegram_id
    if not await enabled(db, uid):
        return json_error('Сначала установите PIN')
    device = (await request.json()).get('device', '')
    if not isinstance(device, str) or not 1 <= len(device) <= 256:
        return json_error('Неизвестное устройство')
    token = secrets.token_urlsafe(32)
    await db.execute('INSERT INTO app_biometrics VALUES (?, ?, ?) '
                     'ON CONFLICT(telegram_id, device_id) DO UPDATE SET token_hash=excluded.token_hash',
                     (uid, device, digest(token)))
    return web.json_response({'ok': True, 'data': {'token': token}})


@routes.post('/api/lock/biometric/unlock')
async def biometric_unlock(request):
    db, uid = context(request).db, current_user(request).telegram_id
    data = await request.json()
    device, token = data.get('device'), data.get('token')
    if not isinstance(device, str) or not isinstance(token, str) or not 1 <= len(token) <= 128:
        return json_error('Биометрия не подтверждена', status=403)
    row = await db.fetch_one('SELECT token_hash FROM app_biometrics WHERE telegram_id=? AND device_id=?', (uid, device))
    if not row or not await enabled(db, uid) or not hmac.compare_digest(row['token_hash'], digest(token)):
        return json_error('Биометрия не подтверждена. Введите PIN.', status=403)
    return await session(request)


@routes.post('/api/lock/biometric/disable')
async def biometric_disable(request):
    device = (await request.json()).get('device', '')
    if not isinstance(device, str) or not 1 <= len(device) <= 256:
        return json_error('Неизвестное устройство')
    await context(request).db.execute('DELETE FROM app_biometrics WHERE telegram_id=? AND device_id=?',
                                     (current_user(request).telegram_id, device))
    return web.json_response({'ok': True, 'data': {}})


@routes.post('/api/lock/passkey/disable')
async def passkey_disable(request):
    _, rp = site(request)
    uid = current_user(request).telegram_id
    async with context(request).db.transaction() as conn:
        await conn.execute('DELETE FROM app_passkeys WHERE telegram_id=? AND rp_id=?', (uid, rp))
        await conn.execute('DELETE FROM app_lock_challenges WHERE telegram_id=? AND rp_id=?', (uid, rp))
    return web.json_response({'ok': True, 'data': {}})
