"""HTTP-доступ к Radicale: Basic Auth только отдельным паролем календаря."""
import asyncio
import json
from aiohttp import BasicAuth, web
from .api import CONTEXT, context, current_user, json_error

routes = web.RouteTableDef()


async def discovery(request):
    app = request.app[CONTEXT]
    raise web.HTTPPermanentRedirect(location=app.settings.web.miniapp_path.rstrip('/') + '/caldav/')


@web.middleware
async def root_discovery(request, handler):
    # Некоторые клиенты начинают обнаружение с корня сайта, минуя well-known.
    # Перенаправление сохраняет метод и тело PROPFIND; обычный GET открывает PWA.
    if request.path == '/' and request.method in {'PROPFIND', 'OPTIONS'}:
        return await discovery(request)
    return await handler(request)


@routes.route('*', '/caldav')
async def slash(request):
    raise web.HTTPPermanentRedirect(location=context(request).settings.web.miniapp_path.rstrip('/') + '/caldav/')


@routes.route('*', '/caldav/{path:.*}')
async def dav(request):
    service = context(request).caldav
    if service is None:
        raise web.HTTPServiceUnavailable()
    headers = {'WWW-Authenticate': 'Basic realm="School calendar", charset="UTF-8"', 'Cache-Control': 'no-store'}
    try:
        auth = BasicAuth.decode(request.headers.get('Authorization', ''), encoding='utf-8')
    except ValueError:
        return web.Response(status=401, headers=headers)
    found = await service.accounts.authenticate(auth.login, auth.password)
    telegram_id, login = found or (None, '')
    user = await context(request).users.get(telegram_id) if telegram_id is not None else None
    # Вход в «Сетевой город» не требуется: после /logout календарь на телефоне
    # показывает последнее расписание, а не просит пароль заново.
    if not user:
        # Ограниченный параллелизм WSGI + задержка неверной авторизации.
        await asyncio.sleep(1)
        return web.Response(status=401, headers=headers)
    if request.method not in {'OPTIONS', 'PROPFIND', 'REPORT', 'GET', 'HEAD'}:
        return web.Response(status=403, text='Школьное расписание доступно только для чтения', headers={'Cache-Control': 'no-store'})
    if request.content_length and request.content_length > 262144:
        raise web.HTTPRequestEntityTooLarge(max_size=262144, actual_size=request.content_length)
    body = bytearray()
    async for chunk in request.content.iter_chunked(65536):
        body.extend(chunk)
        if len(body) > 262144:
            raise web.HTTPRequestEntityTooLarge(max_size=262144, actual_size=len(body))
    result = await service.call(request.method, '/' + request.match_info['path'], login, bytes(body), dict(request.headers))
    response_headers = {key: value for key, value in result['headers'] if key.lower() not in {'transfer-encoding', 'connection', 'content-length'}}
    response_headers['Cache-Control'] = 'no-store'
    etag = response_headers.get('ETag', '').strip('"')
    if result['status'] == 200 and request.method in {'GET', 'HEAD'} and etag:
        if any(tag.value in {'*', etag} for tag in (request.if_none_match or ())):
            return web.Response(status=304, headers=response_headers)
    return web.Response(status=result['status'], body=result['body'], headers=response_headers)


def require_service(request):
    app, user = context(request), current_user(request)
    if app.caldav is None:
        raise web.HTTPServiceUnavailable(text='CalDAV отключён на сервере')
    return app, user, app.caldav


@routes.get('/api/caldav-account')
async def account(request):
    app, user = context(request), current_user(request)
    service = app.caldav
    row = await service.accounts.get(user.telegram_id) if service else None
    login = await service.accounts.login(user.telegram_id) if service else ''
    return web.json_response({'ok': True, 'data': {
        'server': app.settings.web.miniapp_url + '/caldav/',
        'account_url': app.settings.web.miniapp_url + f'/caldav/{login}/',
        'login': login, 'connected': bool(row), 'available': service is not None,
        'interval_minutes': app.settings.caldav_interval // 60,
        'last_sync_at': row['last_sync_at'] if row else None,
        'last_error': row['last_error'] if row else None,
    }}, headers={'Cache-Control': 'no-store'})


@routes.post('/api/caldav-account')
async def create_account(request):
    app, user, service = require_service(request)
    if not user.school.configured or not user.credentials.usable:
        return json_error('Сначала войдите в школу через /login', status=401, reason='login')
    # Новый пароль показывается только в ответе; в базе остаётся хэш.
    password = await service.create(user.telegram_id)
    service.request_sync(user.telegram_id)
    response = await account(request)
    payload = json.loads(response.text)
    payload['data']['password'] = password
    return web.json_response(payload, headers={'Cache-Control': 'no-store'})


@routes.delete('/api/caldav-account')
async def revoke(request):
    _, user, service = require_service(request)
    await service.accounts.revoke(user.telegram_id)
    return web.json_response({'ok': True}, headers={'Cache-Control': 'no-store'})


@routes.post('/api/caldav-sync')
async def refresh(request):
    _, user, service = require_service(request)
    if not await service.accounts.get(user.telegram_id):
        return json_error('Сначала создайте доступ к календарю', status=400)
    service.request_sync(user.telegram_id)
    return web.json_response({'ok': True}, headers={'Cache-Control': 'no-store'})
