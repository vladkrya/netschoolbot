"""Идентификация, права, принудительные настройки и опасные действия владельца."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import access
from app.bot.handlers.admin import find_account
from app.bot.handlers.admin_users import admin_user, manage_user
from app.bot.handlers.common import ContextMiddleware
from tests.test_bot_handlers import FakeMessage
from tests.test_bot_handlers import app as app_context  # noqa: F401
from tests.test_repositories import make_user
from tests.test_web import client  # noqa: F401


class FakeBot:
    async def get_chat(self, uid):
        return SimpleNamespace(full_name='Аня <Петрова>', username='anya')


class Card:
    def __init__(self):
        self.text = ''
        self.reply_markup = None

    async def edit_text(self, text, **kwargs):
        self.text = text
        self.reply_markup = kwargs.get('reply_markup')


class Callback:
    def __init__(self, action, actor=1, uid=2, card=None):
        self.from_user = SimpleNamespace(id=actor)
        self.data = f'adminuser:{uid}:{action}'
        self.message = card or Card()
        self.answers = []

    async def answer(self, text='', **kwargs):
        self.answers.append(text)


async def test_username_index_tracks_rename_and_reuse(users):
    await users.get_or_create(2, telegram_username='Anya')
    await users.get_or_create(3, telegram_username='anya')
    assert (await users._db.fetch_one('SELECT telegram_username FROM users WHERE telegram_id=2'))['telegram_username'] == ''
    assert (await users._db.fetch_one('SELECT telegram_username FROM users WHERE telegram_id=3'))['telegram_username'] == 'anya'
    await users.get_or_create(3, telegram_username='')
    assert (await users._db.fetch_one('SELECT telegram_username FROM users WHERE telegram_id=3'))['telegram_username'] == ''


async def test_lookup_and_card_are_owner_only(app_context, users):
    await users.save(make_user(2))
    await users.remember_telegram_username(2, 'anya')
    message = FakeMessage()
    message.from_user = SimpleNamespace(id=1)
    await admin_user(message, SimpleNamespace(args='@ANYA'), FakeBot(), app_context)
    assert 'Аня &lt;Петрова&gt;' in message.texts
    assert 'Telegram ID: <code>2</code>' in message.texts
    assert 'Безопасность' in str(message.answers[0][1]['reply_markup'])
    outsider = FakeMessage()
    outsider.from_user = SimpleNamespace(id=3)
    await admin_user(outsider, SimpleNamespace(args='2'), FakeBot(), app_context)
    assert not outsider.answers
    await find_account(message, SimpleNamespace(args='tg:2'), FakeBot(), app_context)
    assert '/user 2' in message.texts


async def test_stale_username_does_not_identify_another_person(app_context, users):
    await users.save(make_user(2))
    await users.remember_telegram_username(2, 'old_name')
    message = FakeMessage()
    message.from_user = SimpleNamespace(id=1)
    await admin_user(message, SimpleNamespace(args='@old_name'), FakeBot(), app_context)
    assert 'изменился' in message.texts
    assert 'Telegram ID: <code>' not in message.texts


@pytest.mark.parametrize('action', ['pause', 'force-unity-on', 'grant-solver-allow', 'block', 'confirm-delete', 'do-delete-forged'])
async def test_other_people_cannot_manage_users(app_context, users, action):
    await users.save(make_user(2))
    callback = Callback(action, actor=3)
    await manage_user(callback, app_context)
    assert 'Только для администратора' in callback.answers
    assert (await access.controls(app_context, 2)) == {'blocked': False, 'grants': {}, 'forced_prefs': {}}
    assert await users.get(2) is not None


async def test_pause_resume_and_notifications(app_context, users, monkeypatch):
    await users.save(make_user(2))
    start, stop = AsyncMock(), AsyncMock()
    monkeypatch.setattr(app_context.watchers, 'start', start)
    monkeypatch.setattr(app_context.watchers, 'stop', stop)
    await manage_user(Callback('pause'), app_context)
    assert (await users.get(2)).enabled is False
    stop.assert_awaited_once_with(2)
    await manage_user(Callback('resume'), app_context)
    assert (await users.get(2)).enabled is True
    start.assert_awaited_once_with(2)
    await manage_user(Callback('grades-off'), app_context)
    assert (await users.get(2)).notifications.grades is False
    await manage_user(Callback('interval-600'), app_context)
    assert (await users.get(2)).check_interval == 600
    await manage_user(Callback('interval-1'), app_context)
    assert (await users.get(2)).check_interval == 600


async def test_grants_are_checked_by_profile_and_api(app_context, users, client):
    await users.save(make_user(2))
    token = await app_context.miniapp.issue_token(2)
    params = {'token': token}
    response = await client.post('/mini/api/prefs', params=params, json={'unity': 'on'})
    assert response.status == 403
    await manage_user(Callback('grant-unity-allow'), app_context)
    response = await client.get('/mini/api/profile', params=params)
    assert (await response.json())['data']['unity'] is True
    assert (await client.post('/mini/api/prefs', params=params, json={'unity': 'on'})).status == 200
    await manage_user(Callback('grant-unity-deny'), app_context)
    assert (await client.post('/mini/api/prefs', params=params, json={'unity': 'on'})).status == 403
    assert (await app_context.miniapp.prefs(2))['unity'] == 'off'
    await manage_user(Callback('grant-admin-allow'), app_context)
    assert 'admin' not in (await access.controls(app_context, 2))['grants']


async def test_forced_settings_cannot_be_overridden_and_can_be_released(app_context, users, client):
    await users.save(make_user(2))
    token = await app_context.miniapp.issue_token(2)
    params = {'token': token}
    await manage_user(Callback('force-theme-dark'), app_context)
    response = await client.post('/mini/api/prefs', params=params, json={'theme': 'light', 'accent': '#123456'})
    assert response.status == 200
    response = await client.get('/mini/api/prefs', params=params)
    payload = await response.json()
    assert payload['managed'] == {'theme': 'dark'}
    assert payload['data']['theme'] == 'dark' and payload['data']['accent'] == '#123456'
    await manage_user(Callback('force-unity-on'), app_context)
    assert await access.feature(app_context, 2, 'unity') is True
    await manage_user(Callback('force-halloween-on'), app_context)
    controls = await access.controls(app_context, 2)
    assert controls['forced_prefs']['halloween'] == 'on'
    assert 'unity' not in controls['forced_prefs']
    assert (await app_context.miniapp.prefs(2))['unity'] == 'off'
    await manage_user(Callback('force-theme-free'), app_context)
    await client.post('/mini/api/prefs', params=params, json={'theme': 'light'})
    assert (await app_context.miniapp.prefs(2))['theme'] == 'light'


async def test_block_closes_web_bot_and_background_checks(app_context, users, client, monkeypatch):
    await users.save(make_user(2))
    token = await app_context.miniapp.issue_token(2)
    monkeypatch.setattr(app_context.watchers, 'stop', AsyncMock())
    monkeypatch.setattr(app_context.watchers, 'start', AsyncMock())
    await manage_user(Callback('block'), app_context)
    assert (await client.get('/mini/api/profile', params={'token': token})).status == 403
    assert (await client.get('/mini/api/prefs', params={'token': token})).status == 403
    handler = AsyncMock()
    event = FakeMessage()
    await ContextMiddleware(app_context)(handler, event, {'event_from_user': SimpleNamespace(id=2, full_name='Аня', username='anya')})
    handler.assert_not_awaited()
    assert 'ограничен' in event.texts
    await manage_user(Callback('unblock'), app_context)
    assert (await client.get('/mini/api/profile', params={'token': token})).status == 200
    app_context.watchers.start.assert_awaited_once_with(2)
    await manage_user(Callback('block', uid=1), app_context)
    assert not await access.blocked(app_context, 1)


async def test_reset_and_delete_require_single_use_confirmation(app_context, users):
    await users.save(make_user(2))
    token = await app_context.miniapp.issue_token(2)
    forged = Callback('do-reset-invalid')
    await manage_user(forged, app_context)
    assert await app_context.miniapp.resolve_token(token) == 2
    confirmation = Callback('confirm-reset')
    await manage_user(confirmation, app_context)
    assert await app_context.miniapp.resolve_token(token) == 2
    action = confirmation.message.reply_markup.inline_keyboard[0][0].callback_data.split(':', 2)[2]
    await manage_user(Callback(action), app_context)
    assert await app_context.miniapp.resolve_token(token) is None
    replay = Callback(action)
    await manage_user(replay, app_context)
    assert 'устарело' in replay.answers[0]
    confirmation = Callback('confirm-delete')
    await manage_user(confirmation, app_context)
    action = confirmation.message.reply_markup.inline_keyboard[0][0].callback_data.split(':', 2)[2]
    await manage_user(Callback(action), app_context)
    assert await users.get(2) is None
    assert await app_context.db.fetch_one('SELECT * FROM user_controls WHERE telegram_id=2') is None


async def test_owner_cannot_be_deleted_or_reset(app_context, users):
    await users.save(make_user(1))
    for action in ('confirm-reset', 'confirm-pin', 'confirm-delete'):
        callback = Callback(action, uid=1)
        await manage_user(callback, app_context)
        assert 'недоступно' in callback.answers[0]
    assert await users.get(1) is not None


async def test_feature_denials_are_enforced_on_ai_endpoints(app_context, users, client):
    from app.web.solver import solver_access
    await users.save(make_user(2))
    app_context.settings = replace(app_context.settings, assistant=replace(app_context.settings.assistant, api_key='test'))
    await access.set_control(app_context, 2, grant=('solver', 'allow'))
    assert await solver_access(app_context, await users.get(2)) is True
    await access.set_control(app_context, 2, grant=('solver', 'deny'))
    assert await solver_access(app_context, await users.get(2)) is False
    await access.set_control(app_context, 2, grant=('assistant', 'deny'))
    token = await app_context.miniapp.issue_token(2)
    response = await client.post('/mini/api/assistant', params={'token': token}, json={'messages': []})
    assert response.status == 404
    await access.set_control(app_context, 2, grant=('unity', 'allow'))
    await app_context.miniapp.save_prefs(2, {'unity': 'on'})
    await access.set_control(app_context, 2, grant=('unity', 'default'))
    assert (await app_context.miniapp.prefs(2))['unity'] == 'off'
    assert not await access.feature(app_context, 2, 'unity')


async def test_blocked_user_cannot_be_restarted_by_registry(app_context, users):
    await users.save(make_user(2))
    await access.set_control(app_context, 2, block=True)
    await app_context.watchers.start(2)
    assert 2 not in app_context.watchers.running


async def test_owner_commands_are_only_in_owner_chat_menu():
    from app.bot.app import run_bot
    bot = SimpleNamespace(delete_my_commands=AsyncMock(), set_my_commands=AsyncMock(),
                          set_chat_menu_button=AsyncMock(), get_me=AsyncMock(return_value=SimpleNamespace(username='bot')))
    await run_bot(bot, SimpleNamespace(start_polling=AsyncMock()), '', admin_id=123)
    public, private = bot.set_my_commands.await_args_list
    assert 'user' not in {command.command for command in public.args[0]}
    assert {'user', 'userid', 'id'} <= {command.command for command in private.args[0]}
    assert private.kwargs['scope'].chat_id == 123
