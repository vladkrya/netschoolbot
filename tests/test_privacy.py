import asyncio
import logging
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from app.bot import keyboards
from app.bot.handlers import auth, settings as handlers
from app.db.crypto import PasswordCipher
from app.db.repositories import SessionRepository
from app.domain.models import Credentials, LoginType
from app.logging_setup import SafeFormatter, TelegramHandler
from tests.test_bot_handlers import app, login_state, FakeCallback, FakeMessage
from tests.test_repositories import make_user


@pytest.mark.parametrize('login_type', [LoginType.PASSWORD, LoginType.ESIA])
async def test_declining_storage_keeps_only_session(app, users, login_state, monkeypatch, login_type):
    user = await users.get_or_create(1)
    await login_state.update_data(login_type=login_type.value, login='sensitive-login', remember=False)
    monkeypatch.setattr(auth, '_perform_login', AsyncMock(return_value='{"token":"sensitive-token"}'))
    await auth.receive_password(FakeMessage('sensitive-password'), login_state, user, app)
    loaded = await users.get(1)
    assert loaded.credentials.login == loaded.credentials.password == ''
    assert loaded.credentials.remember is False
    assert loaded.ready_to_check
    assert not loaded.credentials.can_relogin
    assert await app.sessions.load(1) == '{"token":"sensitive-token"}'
    assert app.watchers.running == {1}
    # Последующее обновление профиля не должно вернуть пароль в базу.
    await users.save(replace(loaded, credentials=replace(loaded.credentials, password='accidental-secret')))
    assert (await users.get(1)).credentials.password == ''


async def test_explicit_storage_and_next_login(app, users, login_state, monkeypatch):
    user = await users.get_or_create(1)
    await login_state.update_data(login_type='password', login='remembered', remember=True)
    login = AsyncMock(return_value='{}')
    monkeypatch.setattr(auth, '_perform_login', login)
    await auth.receive_password(FakeMessage('secret'), login_state, user, app)
    stored = await users.get(1)
    assert stored.credentials.password == 'secret'
    assert stored.credentials.can_relogin
    await login_state.update_data(url=stored.school.url, school=stored.school.name)
    await auth.login_saved(FakeCallback('login:saved'), login_state, stored, app)
    assert login.await_count == 2
    assert login.await_args.args[0].credentials.login == 'remembered'


async def test_storage_choice_and_login_order(app, login_state):
    await auth.ask_save_credentials(FakeCallback('login:password'), login_state, app)
    assert await login_state.get_state() == auth.Login.remember.state
    await auth.choose_save_credentials(FakeCallback('login:remember:no'), login_state, app)
    assert (await login_state.get_data())['remember'] is False
    assert await login_state.get_state() == auth.Login.username.state
    rows = keyboards.login_methods().inline_keyboard
    assert [r[0].callback_data for r in rows] == [
        'login:password', 'login:qr', 'login:esia', 'login:cancel',
    ]
    assert all(r[0].style is None for r in rows)
    assert all('рекомендуется' not in r[0].text for r in rows)


async def test_session_encryption_migration_and_rotation(db, users):
    await users.save(make_user())
    await SessionRepository(db).save(1, '{"token":"secret"}')
    key, next_key = (Fernet.generate_key().decode() for _ in range(2))
    sessions = SessionRepository(db, PasswordCipher(key))
    assert await sessions.encrypt_stored_sessions() == 1
    raw = (await db.fetch_one('SELECT payload FROM netschool_sessions'))['payload']
    assert raw.startswith('enc:v1:') and 'secret' not in raw
    assert await sessions.load(1) == '{"token":"secret"}'
    assert await sessions.encrypt_stored_sessions() == 0
    rotating = SessionRepository(db, PasswordCipher(f'{next_key},{key}'))
    assert await rotating.encrypt_stored_sessions() == 1
    assert await SessionRepository(db, PasswordCipher(next_key)).load(1) == '{"token":"secret"}'
    await rotating.save(1, '{"token":"new"}')
    assert await rotating.load(1) == '{"token":"new"}'


def test_logs_exclude_secrets_in_exception_chain_and_arguments():
    secret = 'PRIVATE-password-cookie-response'
    try:
        try:
            raise ValueError(secret)
        except ValueError as cause:
            raise RuntimeError(secret) from cause
    except RuntimeError:
        import sys
        record = logging.LogRecord('netschoolbot.web', logging.ERROR, __file__, 1,
                                   'Ошибка запроса %s: %s', (secret, secret), sys.exc_info())
    record.exc_text = secret  # Formatter другой библиотеки уже мог кэшировать исключение.
    record.stack_info = secret
    rendered = SafeFormatter().format(record)
    queue = asyncio.Queue()
    TelegramHandler(queue).emit(record)
    admin = queue.get_nowait()
    assert secret not in rendered + admin
    assert 'RuntimeError' in rendered and 'RuntimeError' in admin
    assert record.event_id in rendered and record.event_id in admin
    assert 'test_privacy.py:' in rendered and 'test_privacy.py:' not in admin
    assert record.args == (secret, secret)  # Общая запись не модифицирована.
    external = logging.LogRecord('httpx', logging.ERROR, __file__, 1, secret, (), None)
    assert secret not in SafeFormatter().format(external)



def test_public_log_args_are_shown_others_hidden():
    public = logging.LogRecord('netschoolbot.sgo', logging.INFO, __file__, 1,
                               '«Сетевой город» %s: версия %s', ('sgo.example.ru', '5.58.7'), None)
    public.public = True
    assert 'sgo.example.ru: версия 5.58.7' in SafeFormatter().format(public)
    hidden = logging.LogRecord('netschoolbot.sgo', logging.INFO, __file__, 1,
                               '«Сетевой город» %s: версия %s', ('sgo.example.ru', '5.58.7'), None)
    assert '5.58.7' not in SafeFormatter().format(hidden)

async def test_delete_cancels_work_and_removes_all_linked_rows(app, users, login_state):
    user = await users.save(make_user())
    await app.sessions.save(1, '{}')
    await app.cache.put(1, 'mail', {'secret': 'message'})
    token = await app.miniapp.issue_token(1)
    await app.miniapp.issue_login_code(1)
    await app.miniapp.add_push_subscription(1, 'endpoint', 'key', 'auth')
    await app.watchers.start(1)
    entered = asyncio.Event()
    async def login():
        async with app.login_attempt(1):
            entered.set()
            await asyncio.Future()
    task = asyncio.create_task(login())
    await entered.wait()
    refresh = asyncio.create_task(asyncio.sleep(3600))
    app.cache_refreshes[(1, 'mail', ())] = refresh
    calendar = AsyncMock()
    app.caldav = calendar
    await handlers.delete_data(FakeCallback('confirm:delete_data'), user, app, login_state)
    assert task.cancelled() and refresh.cancelled()
    calendar.delete_user.assert_awaited_once_with(1)
    assert await users.get(1) is None
    assert await app.miniapp.resolve_token(token) is None
    assert app.watchers.running == set()
    tables = await app.db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
    for row in tables:
        table = row['name']
        columns = await app.db.fetch_all(f'PRAGMA table_info({table})')
        if any(c['name'] == 'telegram_id' for c in columns):
            count = await app.db.fetch_one(f'SELECT COUNT(*) AS n FROM {table} WHERE telegram_id=1')
            assert count['n'] == 0, table


async def test_cancelled_password_attempt_cannot_save(app, users, login_state, monkeypatch):
    user = await users.get_or_create(1)
    await login_state.update_data(login='user', remember=True)
    async def perform(*args):
        await login_state.clear()
        return '{}'
    monkeypatch.setattr(auth, '_perform_login', perform)
    await auth.receive_password(FakeMessage('secret'), login_state, user, app)
    assert await app.sessions.load(1) is None
    assert (await users.get(1)).school.url == ''


async def test_last_deleted_account_is_not_reimported(app, users, tmp_path):
    from app.context import _import_legacy_if_empty
    folder = tmp_path / 'netschool_users'
    folder.mkdir()
    (folder / 'netschool_users.json').write_text('{"1":{"login":"old","password":"secret"}}')
    app.settings = replace(app.settings, data_dir=tmp_path)
    await users.save(make_user())
    await users.delete(1)
    await _import_legacy_if_empty(app.settings, app.db, users)
    assert await users.all_ids() == []


async def test_session_only_login_restores_after_restart_and_requests_login_on_expiry(app, users):
    from app.netschool.session import SessionPool
    from app.netschool.errors import NetSchoolError, Reason
    from tests.test_session_pool import FakeClient
    user = await users.save(make_user(credentials=Credentials(LoginType.PASSWORD, remember=False)))
    await app.sessions.save(1, '{"cookies":"existing"}')
    pool = SessionPool(app.settings.netschool, app.sessions, client_factory=FakeClient)
    async with pool.acquire(user) as client:
        assert client.imported == '{"cookies":"existing"}'
        assert client.logins == 0
    await pool.invalidate(1, forget_saved=True)
    with pytest.raises(NetSchoolError) as error:
        async with pool.acquire(user):
            pass
    assert error.value.reason is Reason.AUTH
    await pool.close_all()


def test_netschool_error_reason_and_cause_are_logged_without_text():
    import logging

    from app.netschool.errors import NetSchoolError, Reason

    secret = "Иванов Иван"
    error = NetSchoolError(Reason.AUTH, f"Не удалось войти: {secret}", cause=ValueError(secret))
    record = logging.LogRecord("netschoolbot.watcher", logging.WARNING, __file__, 1,
                               "Проверка почты %s не завершилась: %s", (1, error), None)
    rendered = SafeFormatter().format(record)
    assert "NetSchoolError(auth) ← ValueError" in rendered
    assert secret not in rendered


async def test_esia_skips_storage_question_and_shows_consent(app, login_state):
    callback = FakeCallback('login:esia')
    await auth.ask_save_credentials(callback, login_state, app)
    # Пароль Госуслуг не храним — вопроса «сохранить?» нет, сразу логин.
    assert await login_state.get_state() == auth.Login.username.state
    assert (await login_state.get_data())['remember'] is False
    text = callback.message.answers[-1][0]
    assert 'не сохраняются' in text
    assert '/privacy' in text


async def test_school_login_prompt_shows_consent(app, login_state):
    callback = FakeCallback('login:remember:yes')
    await auth.choose_save_credentials(callback, login_state, app)
    assert '/privacy' in callback.message.answers[-1][0]


async def test_esia_password_never_stored(app, db, users, login_state, monkeypatch):
    user = await users.get_or_create(1)
    # Даже если клиент попросил запомнить (старый сайт), пароль Госуслуг не пишется.
    await login_state.update_data(login_type='esia', login='+79990000000', remember=True)
    monkeypatch.setattr(auth, '_perform_login', AsyncMock(return_value='{}'))
    await auth.receive_password(FakeMessage('gosuslugi-secret'), login_state, user, app)
    loaded = await users.get(1)
    assert loaded.credentials.login == loaded.credentials.password == ''
    assert loaded.credentials.remember is False
    row = await db.fetch_one('SELECT login, password FROM users WHERE telegram_id = 1')
    assert row['login'] == row['password'] == ''


async def test_migration_wipes_saved_esia_passwords(db, users):
    from app.db.migrations import MIGRATIONS

    await users.save(make_user())
    await db.execute(
        "UPDATE users SET login_type = 'esia', login = 'phone', password = 'old-secret', "
        "remember_credentials = 1 WHERE telegram_id = 1")
    async with db.transaction() as conn:
        # Не последняя: после неё могут добавиться новые миграции.
        await conn.executescript(next(m for m in MIGRATIONS if "login_type IN ('esia', 'esia_qr')" in m))
    row = await db.fetch_one('SELECT login, password, remember_credentials FROM users WHERE telegram_id = 1')
    assert (row['login'], row['password'], row['remember_credentials']) == ('', '', 0)


async def test_delete_data_erases_chat_messages(app):
    from app.chat.repository import ChatRepository
    await app.users.save(make_user(1))
    await app.users.save(make_user(2))
    chats = ChatRepository(app.db)
    await chats.set_nick(1, 'ivan', 'ivan')
    mine = await chats.add_message('room', author_id=1, author_name='', author_grade=8, is_admin=False, text='secret')
    other = await chats.add_message('room', author_id=2, author_name='', author_grade=8, is_admin=False, text='keep')
    await app.db.execute("INSERT INTO chat_reactions VALUES (?, 2, '👍'), (?, 1, '👍')", (mine, other))
    await app.db.execute("INSERT INTO chat_edits VALUES (?, 'old secret', 1)", (mine,))
    await app.delete_user_data(1)
    texts = [r['text'] for r in await app.db.fetch_all('SELECT text FROM chat_messages')]
    assert texts == ['keep']
    for table, column in [('chat_reactions', 'telegram_id'), ('chat_nicks', 'telegram_id')]:
        assert not await app.db.fetch_all(f'SELECT 1 FROM {table} WHERE {column} = 1')
    assert not await app.db.fetch_all('SELECT 1 FROM chat_reactions WHERE message_id = ?', (mine,))
    assert not await app.db.fetch_all('SELECT 1 FROM chat_edits')
