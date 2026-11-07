"""Индивидуальные ограничения и разрешения. Полномочия владельца не делегируются."""
from __future__ import annotations

import json

UNITY_ENABLED = False

FEATURES = {"unity": "День единства", "halloween": "Хеллоуин", "solver": "Решения заданий", "assistant": "ИИ-помощник"}
MANAGED_PREFS = {
    "unity": ("on", "off"), "halloween": ("on", "off"),
    "theme": ("light", "dark", "auto"), "design": ("min", "v3", "new", "classic"),
    "motion": ("on", "off"), "confetti": ("show", "hide"),
}


def owner(app, uid):
    return bool(app.settings.telegram.admin_id) and uid == app.settings.telegram.admin_id


async def controls(app, uid):
    row = await app.db.fetch_one("SELECT blocked, grants, forced_prefs FROM user_controls WHERE telegram_id = ?", (uid,))
    return {"blocked": bool(row["blocked"]), "grants": json.loads(row["grants"]),
            "forced_prefs": json.loads(row["forced_prefs"])} if row else {"blocked": False, "grants": {}, "forced_prefs": {}}


async def blocked(app, uid):
    if owner(app, uid):
        return False
    row = await app.db.fetch_one("SELECT blocked FROM user_controls WHERE telegram_id = ?", (uid,))
    return bool(row and row["blocked"])


async def feature(app, uid, name, default=False):
    if name == "unity" and not UNITY_ENABLED:
        return False
    data = await controls(app, uid)
    return data["grants"].get(name, default) if not owner(app, uid) else default or data["grants"].get(name, False)


async def set_control(app, uid, *, block=None, grant=None, force=None):
    async with app.user_controls_lock:
        return await _set_control(app, uid, block=block, grant=grant, force=force)


async def _set_control(app, uid, *, block=None, grant=None, force=None):
    """Изменение из админ-команд. Имена и значения — только из белых списков."""
    if not UNITY_ENABLED and any(change and change[0] == "unity" for change in (grant, force)):
        raise ValueError("Праздничная тема завершилась")
    if block and owner(app, uid):
        raise ValueError("Нельзя заблокировать владельца")
    data = await controls(app, uid)
    if block is not None:
        data["blocked"] = bool(block)
    if grant:
        name, value = grant
        if name not in FEATURES or value not in ("allow", "deny", "default"):
            raise ValueError("Неизвестное разрешение")
        if value == "default":
            data["grants"].pop(name, None)
        else:
            data["grants"][name] = value == "allow"
        if value in ("deny", "default") and name in ("unity", "halloween"):
            data["forced_prefs"].pop(name, None)
            await app.miniapp.save_prefs(uid, {name: "off"})
    if force:
        name, value = force
        if name not in MANAGED_PREFS or value not in (*MANAGED_PREFS[name], "free"):
            raise ValueError("Неизвестная настройка")
        if value == "free":
            data["forced_prefs"].pop(name, None)
        else:
            data["forced_prefs"][name] = value
            if name in ("unity", "halloween") and value == "on":
                data["grants"][name] = True
                other = "halloween" if name == "unity" else "unity"
                data["forced_prefs"].pop(other, None)
                await app.miniapp.save_prefs(uid, {other: "off"})
            await app.miniapp.save_prefs(uid, {name: value})
    await app.db.execute(
        "INSERT INTO user_controls (telegram_id, blocked, grants, forced_prefs) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(telegram_id) DO UPDATE SET blocked = excluded.blocked, grants = excluded.grants, forced_prefs = excluded.forced_prefs",
        (uid, int(data["blocked"]), json.dumps(data["grants"]), json.dumps(data["forced_prefs"])))
    if block:
        await app.watchers.stop(uid)
        await app.hub.disconnect(uid)
    from .web.sync import publish
    await publish(app, uid, "settings")
    await publish(app, uid, "prefs")
    return data
