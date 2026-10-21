"""Адаптер WSGI Radicale и фоновая публикация без дополнительных портов."""
from __future__ import annotations
import asyncio
import contextlib
import io
import logging
import sys
from radicale import config
from radicale.app import Application
from app.db.repositories import CaldavRepository
from .events import collection_ics, snapshot
from .rights import PUBLISHER

logger = logging.getLogger('netschoolbot.caldav')


class CaldavService:
    def __init__(self, app):
        self.app = app
        self.accounts = CaldavRepository(app.db)
        self.prefix = app.settings.web.miniapp_path.rstrip('/') + '/caldav'
        folder = app.settings.data_dir / 'caldav'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        configuration = config.load([])
        configuration.update({
            'auth': {'type': 'remote_user'},
            'rights': {'type': 'app.caldav.rights', 'permit_overwrite_collection': 'True'},
            'storage': {'filesystem_folder': str(folder)},
            'web': {'type': 'none'},
            'server': {'max_content_length': '262144', 'max_resource_size': '200000'},
        })
        self.wsgi = Application(configuration)
        self.requests = asyncio.Semaphore(4)
        self.sync_lock = asyncio.Lock()
        self.task = None
        self.jobs = {}

    def _call(self, method, path, user, body=b'', headers=None):
        environ = {
            'REQUEST_METHOD': method, 'SCRIPT_NAME': self.prefix,
            'PATH_INFO': path, 'QUERY_STRING': '', 'SERVER_NAME': 'localhost',
            'SERVER_PORT': '443', 'SERVER_PROTOCOL': 'HTTP/1.1',
            'REMOTE_USER': user, 'REMOTE_ADDR': '127.0.0.1',
            'wsgi.version': (1, 0), 'wsgi.url_scheme': 'https',
            'wsgi.input': io.BytesIO(body), 'wsgi.errors': sys.stderr,
            'wsgi.multithread': True, 'wsgi.multiprocess': False, 'wsgi.run_once': False,
            'CONTENT_LENGTH': str(len(body)),
        }
        # Только протокольные заголовки: клиент не может подменить REMOTE_USER
        # или внешний SCRIPT_NAME через HTTP_X_SCRIPT_NAME.
        for key, value in (headers or {}).items():
            if key.lower() in {'depth', 'if-match', 'if-none-match', 'accept', 'content-type', 'user-agent'}:
                envkey = 'CONTENT_TYPE' if key.lower() == 'content-type' else 'HTTP_' + key.upper().replace('-', '_')
                environ[envkey] = value
        result = {}
        def start_response(status, response_headers, exc_info=None):
            result.update(status=int(status.split()[0]), headers=response_headers)
        iterable = self.wsgi(environ, start_response)
        try:
            result['body'] = b''.join(iterable)
        finally:
            if hasattr(iterable, 'close'):
                iterable.close()
        return result

    async def call(self, *args, **kwargs):
        async with self.requests:
            # Radicale и файловые блокировки не задерживают Telegram/event loop.
            task = asyncio.create_task(asyncio.to_thread(self._call, *args, **kwargs))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # Отмена HTTP-запроса не освобождает слот до завершения WSGI.
                await task
                raise

    async def prepare(self, telegram_id):
        parent = f'/{await self.accounts.login(telegram_id)}/'
        response = await self.call('MKCOL', parent, PUBLISHER)
        if response['status'] not in (201, 405):
            raise RuntimeError(f'CalDAV MKCOL: {response["status"]}')
        calendar = parent + 'schedule/'
        response = await self.call('PROPFIND', calendar, PUBLISHER, headers={'Depth': '0'})
        if response['status'] == 404:
            body = b'<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:set><D:prop><D:displayname>School schedule</D:displayname></D:prop></D:set></C:mkcalendar>'
            response = await self.call('MKCALENDAR', calendar, PUBLISHER, body, {'Content-Type': 'application/xml'})
            if response['status'] == 405:
                return False
            if response['status'] != 201:
                raise RuntimeError(f'CalDAV MKCALENDAR: {response["status"]}')
            return True
        if response['status'] != 207:
            raise RuntimeError(f'CalDAV PROPFIND: {response["status"]}')
        return False

    async def _publish(self, telegram_id, state):
        text = collection_ics(state)
        text = text.replace('CALSCALE:GREGORIAN\r\n', 'CALSCALE:GREGORIAN\r\nX-WR-CALNAME:Школьное расписание\r\n', 1)
        response = await self.call('PUT', f'/{await self.accounts.login(telegram_id)}/schedule/', PUBLISHER,
                                   text.encode(), {'Content-Type': 'text/calendar; charset=utf-8'})
        if response['status'] not in (201, 204):
            raise RuntimeError(f'CalDAV PUT: {response["status"]}')

    async def clear(self, telegram_id):
        """Убрать уроки из календаря, оставив подписку (/logout): после
        следующего входа sync заполнит его расписанием нового дневника."""
        async with self.sync_lock:
            if not await self.accounts.get(telegram_id):
                return
            await self.prepare(telegram_id)
            await self._publish(telegram_id, {'events': {}})
            await self.app.cache.put(telegram_id, 'caldav', {})

    async def create(self, telegram_id):
        """Выдать новый пароль. Логин при этом становится нового формата
        (cal-<номер>), и календарь прежнего логина удаляется."""
        async with self.sync_lock:
            old = (await self.accounts.get(telegram_id) or {}).get('login')
            password = await self.accounts.create(telegram_id)
            await self.prepare(telegram_id)
            if old and old != await self.accounts.login(telegram_id):
                await self._remove(old)
                # Снимок был от прежней папки: новую синхронизация заполнит целиком.
                await self.app.cache.put(telegram_id, 'caldav', {})
        return password

    async def _remove(self, login):
        result = await self.call('DELETE', f'/{login}/', PUBLISHER)
        if result['status'] not in (200, 204, 404):
            raise RuntimeError('Не удалось удалить календарь CalDAV')

    async def delete_user(self, telegram_id):
        # Ждём публикацию, уже выполняющуюся в потоке Radicale.
        async with self.sync_lock:
            login = await self.accounts.login(telegram_id)
            await self.accounts.revoke(telegram_id)
            await self._remove(login)

    async def sync(self, telegram_id):
        async with self.sync_lock:
            if not await self.accounts.get(telegram_id):
                return
            user = await self.app.users.get(telegram_id)
            if not user or not user.school.configured or not user.credentials.usable:
                return
            try:
                async with asyncio.timeout(90):
                    days = await self.app.diary.fetch_diary(user, weeks_back=1, weeks_forward=3, today=user.today())
                current = await self.app.users.get(telegram_id)
                def source_key(value):
                    return (value.school.url, value.selected_student_id, value.student_name, value.timezone)
                # Не публикуем ответ для прежнего ребёнка после смены профиля.
                if current is None or source_key(current) != source_key(user):
                    return
                cached = await self.app.cache.get(telegram_id, 'caldav')
                previous = cached[0] if cached else {}
                state = snapshot(days, user, previous)
                # /logout мог произойти, пока школа отвечала.
                if not await self.accounts.get(telegram_id):
                    return
                created = await self.prepare(telegram_id)
                if created or state['events'] != previous.get('events'):
                    await self._publish(telegram_id, state)
                await self.app.cache.put(telegram_id, 'caldav', state)
                await self.accounts.synced(telegram_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Успешный календарь остаётся целиком; повторяем в следующем цикле.
                logger.warning('Не удалось обновить CalDAV пользователя %s: %s', telegram_id, exc)
                await self.accounts.synced(telegram_id, 'Не удалось обновить расписание; сохранена предыдущая версия')

    async def run(self):
        while True:
            try:
                for telegram_id in await self.accounts.ids():
                    await self.sync(telegram_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Сбой цикла CalDAV')
            await asyncio.sleep(self.app.settings.caldav_interval)

    def request_sync(self, telegram_id):
        existing = self.jobs.get(telegram_id)
        if existing and not existing.done():
            return
        task = asyncio.create_task(self.sync(telegram_id), name=f'caldav-sync-{telegram_id}')
        self.jobs[telegram_id] = task
        task.add_done_callback(lambda finished: self.jobs.pop(telegram_id, None))

    def start(self):
        self.task = asyncio.create_task(self.run(), name='caldav-sync')

    async def close(self):
        jobs = list(self.jobs.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
