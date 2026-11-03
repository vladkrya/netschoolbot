"""Версионированное API интеграций с отдельными от входа read-only ключами."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from aiohttp import web
from .api.base import USER, context, current_user, json_error
from .api import diary, marks, homework, mail, planning, attachments
from . import extras
from .api.profile import student_class

routes = web.RouteTableDef()
PREFIX = '/api/v1'
TOKEN_PREFIX = 'sgoshka-'
TOKEN_PREFIXES = (TOKEN_PREFIX, 'nsro_')

# Только явно проверенные обработчики. Новые маршруты приложения не получают
# доступ по ключу интеграции автоматически.
READS = {
    'attachment/{attachment_id}': (attachments.attachment, 'Файл задания или письма', 'name: имя скачиваемого файла; ответ — файл'),
    'homework/{assignment_id}/comment': (homework.homework_comment, 'Комментарий к заданию', ''),
    'homework/{assignment_id}/answer': (homework.homework_answer, 'Сохранённый ответ на задание', ''),
    'diary': (diary.diary, 'Дневник и расписание', 'week: смещение недели −30…30; window=current; refresh=1'),
    'schedule': (diary.diary, 'Расписание с заданиями и оценками', 'week; window=current; refresh=1'),
    'marks': (marks.marks, 'Оценки и итоги', 'year: ID учебного года; refresh=1'),
    'marks/period': (marks.archived_period_marks, 'Оценки архивного периода', 'year и quarter обязательны'),
    'marks/subject': (marks.archived_subject_marks, 'Предмет архивного года', 'year, subject, quarter'),
    'marks/lessons': (marks.period_lessons, 'Уроки периода', 'quarter'),
    'subject': (marks.subject_stats, 'Статистика предмета', 'subject и quarter обязательны'),
    'subject/class-history': (marks.class_average_history, 'История среднего балла класса', 'subject, quarter'),
    'class-averages': (marks.class_averages, 'Средние баллы класса', 'quarter'),
    'homework': (homework.homework, 'Домашние задания', 'past=1: прошлые четыре недели; refresh=1'),
    'homework/done': (homework.homework_done, 'Выполненные задания', ''),
    'announcements': (diary.announcements, 'Объявления', 'refresh=1'),
    'bells': (diary.bells, 'Расписание звонков', 'refresh=1'),
    'events': (diary.events, 'Школьные события', ''),
    'history': (diary.mark_history, 'История изменений оценок', 'before: ID для следующей страницы, до 50 записей'),
    'children': (planning.children, 'Сводка по детям аккаунта', 'refresh=1'),
    'countdown': (planning.countdown, 'Учебные периоды и каникулы', ''),
    'search': (planning.search, 'Поиск по заданиям и почте', 'q: минимум два символа'),
    'notes': (extras.list_notes, 'Свои задачи', 'from и to: YYYY-MM-DD, не более 400 дней'),
    'mail/{message_id}': (mail.mail_message, 'Текст письма и вложения', 'Школа автоматически отмечает открытое письмо прочитанным'),
    'student-class': (student_class, 'Класс ученика', ''),
    'references/types': (diary.reference_types, 'Доступные типы школьных справок', ''),
    'mail': (mail.mail, 'Школьная почта: список', 'folder=Inbox или Deleted; page от 1, по 50 писем'),
    'mail/recipients': (mail.mail_recipients, 'Получатели школьной почты', ''),
}


QUERY_PARAMS = {
    'diary': ('week', 'window', 'refresh'), 'schedule': ('week', 'window', 'refresh'),
    'marks': ('year', 'refresh'), 'marks/period': ('year', 'quarter', 'refresh'),
    'marks/subject': ('year', 'quarter', 'subject', 'refresh'),
    'marks/lessons': ('quarter', 'refresh'), 'subject': ('subject', 'quarter', 'refresh'),
    'subject/class-history': ('subject', 'quarter'), 'class-averages': ('quarter', 'refresh'),
    'homework': ('past', 'refresh'), 'mail': ('folder', 'page', 'refresh'),
    'history': ('before',),
    'announcements': ('refresh',), 'bells': ('refresh',), 'events': ('refresh',),
    'children': ('refresh',), 'countdown': ('refresh',),
    'mail/recipients': ('refresh',),
    'notes': ('from', 'to'), 'search': ('q',), 'attachment/{attachment_id}': ('name',),
}
REQUIRED_PARAMS = {
    'marks/period': ('year', 'quarter'), 'marks/subject': ('year', 'quarter', 'subject'),
    'marks/lessons': ('quarter',), 'subject': ('subject', 'quarter'),
    'subject/class-history': ('subject', 'quarter'), 'class-averages': ('quarter',), 'search': ('q',),
}
PARAM_SCHEMAS = {
    'week': {'type': 'integer', 'minimum': -30, 'maximum': 30, 'default': 0},
    'window': {'type': 'string', 'enum': ['current']},
    'refresh': {'type': 'string', 'enum': ['1']}, 'past': {'type': 'string', 'enum': ['1']},
    'year': {'type': 'integer', 'minimum': 1},
    'page': {'type': 'integer', 'minimum': 1, 'default': 1},
    'folder': {'type': 'string', 'enum': ['Inbox', 'Deleted'], 'default': 'Inbox'},
    'from': {'type': 'string', 'format': 'date'}, 'to': {'type': 'string', 'format': 'date'},
    'q': {'type': 'string', 'minLength': 2, 'maxLength': 100},
    'before': {'type': 'integer', 'description': 'ID последней записи предыдущей страницы истории'},
}


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


async def token_items(app, owner):
    rows = await app.db.fetch_all('SELECT id, name, created_at, expires_at, last_used_at FROM api_tokens WHERE owner_id=? ORDER BY id DESC', (owner,))
    return [{**dict(row), 'expires_at': row['expires_at'] or None} for row in rows]


@routes.get('/api/integration-tokens')
async def list_tokens(request):
    return web.json_response({'ok': True, 'data': {'items': await token_items(context(request), current_user(request).telegram_id)}})


@routes.post('/api/integration-tokens')
async def create_token(request):
    try:
        body = await request.json()
        name = body.get('name', '').strip()
        days = body.get('days', 90)
        if not isinstance(name, str) or not 1 <= len(name) <= 80 or type(days) is not int or not 0 <= days <= 365:
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        return json_error('Укажите название до 80 символов и срок от 1 до 365 дней или 0 для бессрочного токена')
    app, owner = context(request), current_user(request).telegram_id
    number = await app.accounts.number(owner)
    if number is None:
        return json_error('Не удалось получить номер аккаунта', status=503)
    token = f'{TOKEN_PREFIX}{number}_' + secrets.token_urlsafe(32)
    now = int(time.time())
    async with app.db.transaction() as db:
        async with db.execute('SELECT COUNT(*) FROM api_tokens WHERE owner_id=? AND (expires_at=0 OR expires_at>?)', (owner, now)) as cursor:
            count = (await cursor.fetchone())[0]
        if count >= 20:
            return json_error('Не больше 20 действующих токенов', status=409)
        await db.execute('INSERT INTO api_tokens(owner_id,name,token_hash,created_at,expires_at) VALUES (?,?,?,?,?)', (owner, name, digest(token), now, now + days * 86400 if days else 0))
    return web.json_response({'ok': True, 'data': {'token': token, 'items': await token_items(app, owner)}}, status=201)


@routes.delete('/api/integration-tokens/{id}')
async def revoke_token(request):
    try:
        token_id = int(request.match_info['id'])
    except ValueError:
        return json_error('Некорректный ID токена')
    await context(request).db.execute('DELETE FROM api_tokens WHERE id=? AND owner_id=?', (token_id, current_user(request).telegram_id))
    return web.json_response({'ok': True})


@web.middleware
async def readable_json(request, handler):
    response = await handler(request)
    relative = request.path[request.path.index('/api/'):] if '/api/' in request.path else ''
    if (relative.startswith(PREFIX + '/') and '/captcha-proxy/' not in request.path
            and type(response) is web.Response and not response.prepared
            and response.content_type == 'application/json'
            and isinstance(response.body, (bytes, bytearray))):
        payload = json.loads(response.body)
        response.body = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
        response.charset = 'utf-8'
    return response


@web.middleware
async def authenticate(request, handler):
    path = request.path
    # Капча проксирует собственное /api/v1, это не API интеграций.
    if "/captcha-proxy/" in path:
        return await handler(request)
    relative = path[path.index('/api/'):].rstrip('/') if '/api/' in path else ''
    authorization = request.headers.get('Authorization', '')
    # Ключ API никогда не становится обычным сеансом приложения.
    if relative != PREFIX and not relative.startswith(PREFIX + '/'):
        if authorization.split(' ', 1)[-1].startswith(TOKEN_PREFIXES):
            return json_error('Токен интеграции работает только в /api/v1', status=403, reason='read_only')
        return await handler(request)
    if relative in (PREFIX + '/docs', PREFIX + '/openapi.json') and request.method in ('GET', 'HEAD'):
        return await handler(request)
    scheme, _, token = authorization.partition(' ')
    app = context(request)
    row = None
    if scheme.lower() == 'bearer' and token.startswith(TOKEN_PREFIXES) and len(token) <= 100:
        row = await app.db.fetch_one('SELECT id,owner_id FROM api_tokens WHERE token_hash=? AND (expires_at=0 OR expires_at>?)', (digest(token), time.time()))
    user = await app.users.get(row['owner_id']) if row else None
    if user is None:
        response = json_error('Передайте действующий токен: Authorization: Bearer <token>', status=401, reason='invalid_token')
        response.headers['WWW-Authenticate'] = 'Bearer realm="read-only API"'
        return response
    request[USER] = user
    request['integration_api'] = True
    if request.method not in ('GET', 'HEAD'):
        return json_error('API доступно только для чтения', status=405, reason='read_only')
    await app.db.execute('UPDATE api_tokens SET last_used_at=? WHERE id=? AND (last_used_at IS NULL OR last_used_at<?)', (int(time.time()), row['id'], int(time.time()) - 300))
    return await handler(request)


@routes.get(PREFIX + '/profile')
async def profile(request):
    user = current_user(request)
    return web.json_response({'ok': True, 'data': {'name': user.label, 'school': user.school.name, 'timezone': user.timezone,
        'student_id': user.selected_student_id, 'students': [{'id': s.id, 'name': s.name, 'current': s.id == user.selected_student_id} for s in user.available_students]}})


@routes.get(PREFIX + '/openapi.json')
async def openapi(request):
    paths = {'/profile': {'get': {'summary': 'Профиль и ученики', 'responses': {'200': {'description': 'Профиль'}}}}}
    for path, (_, title, params) in READS.items():
        paths['/' + path] = {'get': {'summary': title, 'description': params, 'responses': {'200': {'description': 'JSON: ok, data; stale — данные из устаревшего кэша'}, '401': {'description': 'Недействующий токен'}, '503': {'description': 'Школа недоступна'}}}}
    for path, item in paths.items():
        operation = item['get']
        operation['parameters'] = []
        for name in ('attachment_id', 'assignment_id', 'message_id'):
            if '{' + name + '}' in path:
                operation['parameters'].append({'name': name, 'in': 'path', 'required': True, 'schema': {'type': 'integer', 'minimum': 1}})
        for name in QUERY_PARAMS.get(path.lstrip('/'), ()):
            operation['parameters'].append({'name': name, 'in': 'query', 'required': name in REQUIRED_PARAMS.get(path.lstrip('/'), ()), 'schema': PARAM_SCHEMAS.get(name, {'type': 'string'})})
        operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/Response'}}}
        if path.startswith('/attachment/'):
            operation['responses']['200']['content'] = {'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}}
    return web.json_response({'openapi': '3.0.3', 'info': {'title': 'СГОшка · Read-only API', 'version': '1.0.0'}, 'servers': [{'url': context(request).settings.web.miniapp_url.rstrip('/') + PREFIX}], 'security': [{'bearerAuth': []}], 'components': {'schemas': {'Response': {'type': 'object', 'required': ['ok', 'data'], 'properties': {'ok': {'type': 'boolean', 'enum': [True]}, 'data': {}, 'stale': {'type': 'boolean'}, 'age': {'type': 'integer', 'minimum': 0}, 'refreshing': {'type': 'boolean'}}}}, 'securitySchemes': {'bearerAuth': {'type': 'http', 'scheme': 'bearer'}}}, 'paths': paths})


@routes.get(PREFIX + '/docs')
async def docs(request):
    import aiohttp_jinja2
    return aiohttp_jinja2.render_template('api_docs.html', request, {
        'base': context(request).settings.web.miniapp_url.rstrip('/') + PREFIX,
        'resources': [(path, title, params) for path, (_, title, params) in READS.items()],
        'mail_note': 'Получение текста письма через /mail/{message_id} отмечает письмо прочитанным на сервере школы. Других команд изменения почты API не предоставляет.',
    })


@routes.get(PREFIX + r'/mail/{message_id:\d+}')
async def mail_message(request):
    return await mail.mail_message(request)


@routes.get(PREFIX + '/attachment/{attachment_id}')
async def attachment(request):
    return await attachments.attachment(request)


@routes.get(PREFIX + '/homework/{assignment_id}/comment')
async def homework_comment(request):
    return await homework.homework_comment(request)


@routes.get(PREFIX + '/homework/{assignment_id}/answer')
async def homework_answer(request):
    return await homework.homework_answer(request)


@routes.route('*', PREFIX + '/{resource:.*}')
async def read_resource(request):
    resource = request.match_info['resource']
    if resource in READS:
        return await READS[resource][0](request)
    return json_error('Нет такого ресурса API', status=404, reason='not_found')
