"""Read-only представление публичной страницы статуса для интеграций."""
from aiohttp import web
from ..status.service import RANGES
from .api.base import context, json_error


async def snapshot(request):
    range_key = request.query.get('range', '24h')
    if range_key not in RANGES:
        return None, json_error('Период: 24h, 7d или 30d')
    service = context(request).status
    if service is None:
        return None, json_error('Мониторинг статуса недоступен', status=503, reason='unavailable')
    return await service.summary(range_key), None


async def summary(request):
    data, error = await snapshot(request)
    return error if error is not None else web.json_response({'ok': True, 'data': data})


async def hosts(request):
    data, error = await snapshot(request)
    if error is not None:
        return error
    return web.json_response({'ok': True, 'data': {key: data[key] for key in ('generated_at', 'range', 'since', 'bucket_s', 'hosts')}})


async def host(request):
    data, error = await snapshot(request)
    if error is not None:
        return error
    name = request.match_info['host'].lower()
    item = next((item for item in data['hosts'] if item['host'].lower() == name), None)
    if item is None:
        return json_error('Сервер не найден в каталоге статуса', status=404, reason='not_found')
    return web.json_response({'ok': True, 'data': {'generated_at': data['generated_at'], 'range': data['range'],
        'since': data['since'], 'bucket_s': data['bucket_s'], 'host': item}})


async def incidents(request):
    return await collection(request, 'incidents')


async def versions(request):
    return await collection(request, 'versions')


async def reports(request):
    return await collection(request, 'reports')


async def collection(request, key):
    data, error = await snapshot(request)
    if error is not None:
        return error
    return web.json_response({'ok': True, 'data': {'generated_at': data['generated_at'], 'range': data['range'], 'items': data[key]}})
