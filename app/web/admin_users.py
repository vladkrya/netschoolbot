"""Поиск и персональные темы в админке. В ответах нет данных дневника."""
from __future__ import annotations

import sqlite3
import secrets
import time
from dataclasses import replace

from aiohttp import web

from .. import access
from ..accounts import has_telegram
from .api import context, current_user, halloween_season, json_error

routes = web.RouteTableDef()
CONFIRMATIONS = web.AppKey("admin_user_confirmations", dict)
NOTIFICATIONS = {"grades": "Оценки", "homework": "Домашние задания", "mail": "Почта", "telegram": "Telegram"}
INTERVALS = (180, 300, 600, 1800)
THEMES = (
    ("unity", "День народного единства", "Синяя палитра и ленты триколора"),
    ("halloween", "Хеллоуин", "Ночная тема, тыквенный акцент и летучие мыши"),
)


def _owner(request):
    return access.owner(context(request), current_user(request).telegram_id)


def _uid(raw):
    if not raw.isascii() or not raw.isdigit() or len(raw) > 19 or not 0 < int(raw) <= 2**63 - 1:
        raise ValueError("Некорректный ID")
    return int(raw)


def _identity(row):
    return {"id": row["telegram_id"], "account_no": row["account_no"], "username": row["telegram_username"],
            "has_telegram": has_telegram(row["telegram_id"])}


async def _details(app, uid):
    row = await app.db.fetch_one("SELECT telegram_id, account_no, telegram_username FROM users WHERE telegram_id = ?", (uid,))
    if row is None:
        return None
    user = await app.users.get(uid)
    if user is None:
        return None
    control = await access.controls(app, uid)
    prefs = {**await app.miniapp.prefs(uid), **control["forced_prefs"]}
    defaults = {"unity": access.owner(app, uid), "halloween": halloween_season(user.today())}
    available = {key: await access.feature(app, uid, key, defaults[key]) for key, *_ in THEMES}
    unity_on = available["unity"] and prefs.get("unity") == "on"
    return {**_identity(row), "blocked": control["blocked"], "owner": access.owner(app, uid),
            "checking": {"enabled": user.enabled, "interval": user.check_interval,
                         "ready": replace(user, enabled=True).ready_to_check},
            "notifications": {key: getattr(user.notifications, key) for key in NOTIFICATIONS},
            "forced_prefs": control["forced_prefs"],
            "features": [{"key": key, "name": name, "permission":
                          "default" if key not in control["grants"] else "allow" if control["grants"][key] else "deny"}
                         for key, name in access.FEATURES.items() if key not in {k for k, *_ in THEMES}],
            "themes": [{
        "key": key, "name": name, "description": description,
        "available": available[key], "default_available": defaults[key],
        "permission": "default" if key not in control["grants"] else "allow" if control["grants"][key] else "deny",
        "forced": control["forced_prefs"].get(key, "free"),
        "active": unity_on if key == "unity" else available[key] and not unity_on and prefs.get(key) != "off",
    } for key, name, description in THEMES]}


@routes.get('/api/admin/users')
async def search(request):
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    query = request.query.get('q', '').strip()
    if len(query) > 64:
        return json_error("Слишком длинный запрос")
    if not query:
        return web.json_response({"ok": True, "data": {"items": [], "more": False}})
    params = ()
    if query.startswith('#') or query.startswith('tg:') or query.isascii() and query.isdigit():
        try:
            uid = _uid(query[3:] if query.startswith('tg:') else query.lstrip('#'))
        except ValueError as exc:
            return json_error(str(exc))
        if query.startswith('#'):
            where, params = 'account_no = ?', (uid,)
        elif query.startswith('tg:'):
            where, params = 'telegram_id = ?', (uid,)
        else:
            where, params = '(telegram_id = ? OR account_no = ?)', (uid, uid)
    else:
        name = query.lstrip('@').lower()
        if len(name) < 2:
            return web.json_response({"ok": True, "data": {"items": [], "more": False}})
        if not all(c.isascii() and (c.isalnum() or c == '_') for c in name):
            return json_error("Введите @username, Telegram ID или #номер аккаунта")
        where, params = "telegram_username LIKE ? ESCAPE '\\'", (name.replace('_', '\\_') + '%',)
    rows = await context(request).db.fetch_all(
        f"SELECT telegram_id, account_no, telegram_username FROM users WHERE {where} ORDER BY account_no LIMIT 13", params)
    return web.json_response({"ok": True, "data": {"items": [_identity(row) for row in rows[:12]], "more": len(rows) > 12}})


@routes.get('/api/admin/users/{uid:\\d+}')
async def details(request):
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    try:
        uid = _uid(request.match_info['uid'])
    except ValueError as exc:
        return json_error(str(exc))
    data = await _details(context(request), uid)
    return web.json_response({"ok": True, "data": data}) if data else json_error("Пользователь не найден", status=404)


@routes.post('/api/admin/users/{uid:\\d+}/theme')
async def theme(request):
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    try:
        uid = _uid(request.match_info['uid'])
        body = await request.json()
    except ValueError:
        return json_error("Некорректный запрос")
    if not isinstance(body, dict) or set(body) != {'kind', 'name', 'value'} or not all(isinstance(v, str) for v in body.values()):
        return json_error("Укажите тему, действие и значение")
    if body['kind'] not in ('grant', 'force') or body['name'] not in {key for key, *_ in THEMES}:
        return json_error("Неизвестная настройка темы")
    app = context(request)
    if await app.users.get(uid) is None:
        return json_error("Пользователь не найден", status=404)
    try:
        await access.set_control(app, uid, **{body['kind']: (body['name'], body['value'])})
    except (ValueError, TypeError):
        return json_error("Некорректное значение")
    except sqlite3.IntegrityError:
        return json_error("Пользователь уже удалён", status=404)
    data = await _details(app, uid)
    return web.json_response({"ok": True, "data": data}) if data else json_error("Пользователь уже удалён", status=404)


@routes.post(r'/api/admin/users/{uid:\d+}/manage')
async def manage(request):
    if not _owner(request):
        return json_error("Это может только владелец", status=403)
    try:
        uid = _uid(request.match_info['uid'])
        body = await request.json()
    except ValueError:
        return json_error("Некорректный запрос")
    if not isinstance(body, dict) or set(body) != {'kind', 'name', 'value'} or not isinstance(body['kind'], str) or not isinstance(body['name'], str):
        return json_error("Некорректное действие")
    app = context(request)
    user = await app.users.get(uid)
    if user is None:
        return json_error("Пользователь не найден", status=404)
    kind, name, value = body['kind'], body['name'], body['value']
    if kind in ('confirm', 'execute'):
        if name not in ('reset', 'pin', 'delete') or access.owner(app, uid):
            return json_error("Действие недоступно", status=403)
        confirmations = request.app[CONFIRMATIONS]
        now = time.monotonic()
        if kind == 'confirm':
            for token, record in list(confirmations.items()):
                if record[3] <= now:
                    confirmations.pop(token, None)
            if len(confirmations) >= 100:
                return json_error("Закройте предыдущие подтверждения и повторите позже", status=429)
            token = secrets.token_urlsafe(18)
            confirmations[token] = (uid, name, current_user(request).telegram_id, now + 300)
            return web.json_response({"ok": True, "data": {"confirmation": token, "id": uid}})
        if not isinstance(value, str):
            return json_error("Подтверждение устарело", status=409)
        record = confirmations.get(value)
        if not record or record[:3] != (uid, name, current_user(request).telegram_id) or record[3] <= now:
            return json_error("Подтверждение устарело. Повторите действие.", status=409)
        confirmations.pop(value)
        if name == 'delete':
            await app.delete_user_data(uid)
            return web.json_response({"ok": True, "data": {"deleted": True, "id": uid}})
        if name == 'reset':
            await app.miniapp.revoke_tokens(uid)
            await app.db.execute("DELETE FROM app_unlocks WHERE telegram_id = ?", (uid,))
            if app.caldav:
                await app.caldav.accounts.revoke(uid)
            app.web_logins.pop(uid, None)
            await app.hub.disconnect(uid)
        else:
            async with app.db.transaction() as db:
                for table in ('app_locks', 'app_unlocks', 'app_passkeys', 'app_biometrics', 'app_lock_challenges'):
                    await db.execute(f"DELETE FROM {table} WHERE telegram_id = ?", (uid,))
    elif kind in ('grant', 'force'):
        try:
            if not isinstance(value, str):
                raise ValueError
            await access.set_control(app, uid, **{kind: (name, value)})
        except ValueError:
            return json_error("Некорректная настройка")
    elif kind == 'block' and name == 'access' and type(value) is bool:
        if access.owner(app, uid):
            return json_error("Нельзя заблокировать владельца", status=403)
        await access.set_control(app, uid, block=value)
        if not value and user.ready_to_check:
            await app.watchers.start(uid)
    elif kind == 'notify' and name in NOTIFICATIONS and type(value) is bool:
        await app.users.save(replace(user, notifications=replace(user.notifications, **{name: value})))
    elif kind == 'checking' and name in ('enabled', 'interval', 'restart'):
        if name == 'interval' and type(value) is int and value in INTERVALS:
            user = await app.users.save(replace(user, check_interval=value))
        elif name == 'enabled' and type(value) is bool:
            if value and (await access.blocked(app, uid) or not replace(user, enabled=True).ready_to_check):
                return json_error("Проверку нельзя включить: проверьте доступ и вход в дневник")
            user = await app.users.save(replace(user, enabled=value))
        elif name != 'restart' or value is not True or not user.ready_to_check or await access.blocked(app, uid):
            return json_error("Не удалось перезапустить проверку")
        if user.ready_to_check:
            await app.watchers.start(uid)
        else:
            await app.watchers.stop(uid)
    else:
        return json_error("Неизвестное действие")
    from .sync import publish
    await publish(app, uid, 'settings')
    data = await _details(app, uid)
    return web.json_response({"ok": True, "data": data}) if data else json_error("Пользователь уже удалён", status=404)
