"""Выборочный доступ к решениям и учебникам."""
from __future__ import annotations

import json
from urllib.parse import urlparse

from .. import access


async def class_identity(app, user):
    from ..web.solver import _class_name
    name = await _class_name(app, user)
    host = urlparse(user.school.url).hostname
    school = user.school.name.strip().casefold()
    return [host, school, name] if host and school and name else None


async def rules_for(app, user):
    own = (await access.controls(app, user.telegram_id))["grants"].get("solver_rules")
    rules = [own] if own else []
    identity = await class_identity(app, user)
    if identity:
        rows = await app.db.fetch_all("SELECT telegram_id, grants FROM user_controls")
        for row in rows:
            rule = json.loads(row['grants']).get('solver_rules')
            if row['telegram_id'] != user.telegram_id and rule and rule.get('class_identity') == identity:
                rules.append(rule)
    return rules


async def permitted_books(app, user, subject):
    rules = await rules_for(app, user)
    if not rules:
        return None
    ids = set()
    for rule in rules:
        if subject.strip().casefold() in {s.casefold() for s in rule['subjects']}:
            ids.update(rule['books'])
    return ids


def catalog(app):
    return [{"id": b.id, "title": b.title, "subjects": list(b.subjects)} for b in app.textbooks.all_books()]


async def save(app, uid, value):
    if not isinstance(value, dict) or set(value) != {'enabled', 'classwide', 'subjects', 'books'}:
        raise ValueError('Некорректные настройки решений')
    if type(value['enabled']) is not bool or type(value['classwide']) is not bool:
        raise ValueError('Некорректный переключатель')
    books = catalog(app)
    for key, allowed in [('subjects', {s for b in books for s in b['subjects']}), ('books', {b['id'] for b in books})]:
        if not isinstance(value[key], list) or any(not isinstance(v, str) or v not in allowed for v in value[key]):
            raise ValueError('Выберите доступные предметы и учебники')
    rule = None
    if value['enabled']:
        if not value['subjects'] or not value['books']:
            raise ValueError('Выберите хотя бы один предмет и учебник')
        if any(not any(b['id'] in value['books'] and s in b['subjects'] for b in books) for s in value['subjects']):
            raise ValueError('Для каждого предмета выберите учебник')
        user = await app.users.get(uid)
        identity = await class_identity(app, user) if value['classwide'] else None
        if value['classwide'] and not identity:
            raise ValueError('Класс неизвестен. Пользователь должен открыть настройки дневника и загрузить класс')
        rule = {**value, 'class_identity': identity}
    async with app.user_controls_lock:
        data = await access.controls(app, uid)
        if rule:
            data['grants']['solver_rules'] = rule
            if data['grants'].get('solver') is False:
                data['grants'].pop('solver')
        else:
            data['grants'].pop('solver_rules', None)
        await app.db.execute(
            'INSERT INTO user_controls (telegram_id, blocked, grants, forced_prefs) VALUES (?, ?, ?, ?) '
            'ON CONFLICT(telegram_id) DO UPDATE SET grants = excluded.grants',
            (uid, int(data['blocked']), json.dumps(data['grants']), json.dumps(data['forced_prefs'])))
