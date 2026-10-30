"""Public distribution of signed Android builds; publication is an atomic file swap."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from aiohttp import web
from .api import context, json_error
from ..db.repositories import AppFlagRepository

routes = web.RouteTableDef()
POLICY_LOCK = web.AppKey("android_policy_lock", asyncio.Lock)
APK_NAME = re.compile(r"sgoshka-[1-9][0-9]{0,9}\.apk")
VERSION_APK = re.compile(r"sgoshka-v\.[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}\.apk")
MAX_APK_BYTES = 200 * 1024 * 1024


async def updates_enabled(app) -> bool:
    # Existing installations keep updating unless the owner explicitly pauses the channel.
    return not await AppFlagRepository(app.db).get("android_updates_disabled")


BLOCK_PREFIX = "android_version_blocked:"


async def blocked_versions(app) -> list[int]:
    rows = await app.db.fetch_all("SELECT key FROM app_flags WHERE key GLOB ? AND value = '1'", (BLOCK_PREFIX + "*",))
    return sorted({int(row["key"][len(BLOCK_PREFIX):]) for row in rows
                   if re.fullmatch(r"[1-9][0-9]{0,9}", row["key"][len(BLOCK_PREFIX):]) and int(row["key"][len(BLOCK_PREFIX):]) <= 2_100_000_000})


@routes.get("/android/policy.json")
async def version_policy(request: web.Request) -> web.Response:
    return web.json_response({"packageName": "ru.ikrya.sgoshka", "blockedVersionCodes": await blocked_versions(context(request))},
                             headers={"Cache-Control": "no-store"})


def directory(request: web.Request) -> Path:
    return context(request).settings.data_dir / "android-releases"


def release_manifest(root: Path, manifest_name: str = "update.json") -> dict | None:
    path = root / manifest_name
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 32768:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("packageName") != "ru.ikrya.sgoshka":
            return None
        code = data.get("versionCode")
        if type(code) is not int or not 0 < code <= 2_100_000_000:
            return None
        if not isinstance(data.get("versionName"), str) or not 0 < len(data["versionName"]) <= 64:
            return None
        name = data.get("file")
        if name != f"sgoshka-{code}.apk" and not (isinstance(name, str) and VERSION_APK.fullmatch(name) and name == f"sgoshka-v.{data['versionName']}.apk"):
            return None
        if not isinstance(data.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", data["sha256"]):
            return None
        size = data.get("size")
        if type(size) is not int or not 0 < size <= MAX_APK_BYTES:
            return None
        if type(data.get("minSdk")) is not int or not 26 <= data["minSdk"] <= 100:
            return None
        if not isinstance(data.get("notes", ""), str) or len(data.get("notes", "")) > 4000:
            return None
        apk = root / data["file"]
        if apk.is_symlink() or not apk.is_file() or apk.stat().st_size != size:
            return None
        return {key: data[key] for key in ("packageName", "versionCode", "versionName", "file", "sha256", "size", "minSdk")} | {"notes": data.get("notes", "")}
    except (OSError, ValueError, TypeError):
        return None


# Сборки, выпущенные до того, как у каждой появился свой манифест.
LEGACY_VERSIONS = {4: "0.4.0", 5: "0.5.0"}


def published_versions(root: Path) -> list[dict]:
    """Все выпущенные сборки: номер и имя версии, новые первыми.

    Имя берётся из манифестов sgoshka-*.json и update.json, которые остаются на сервере и
    после удаления самих APK, — отключать старую версию можно по её имени."""
    found = dict(LEGACY_VERSIONS)
    try:
        paths = sorted(root.glob("sgoshka-*.json"))[:500] + [root / "update.json"]
    except OSError:
        paths = []
    for path in paths:
        try:
            if path.is_symlink() or path.stat().st_size > 32768:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        code, name = (data.get("versionCode"), data.get("versionName")) if isinstance(data, dict) else (None, None)
        if (isinstance(data, dict) and data.get("packageName") == "ru.ikrya.sgoshka" and type(code) is int
                and 0 < code <= 2_100_000_000 and isinstance(name, str) and 0 < len(name) <= 64):
            found[code] = name
    return [{"versionCode": code, "versionName": found[code]} for code in sorted(found, reverse=True)]


def _manifest_by_code(root: Path, code: int) -> dict | None:
    """Манифест по номеру сборки среди манифестов, названных по версии (sgoshka-v.0.6.1.json).

    Ссылки вида sgoshka-11.apk остаются рабочими, хотя отдельный
    sgoshka-11.json больше не нужен: достаточно манифеста по имени версии."""
    try:
        paths = sorted(root.glob("sgoshka-v.*.json"))[:500]
    except OSError:
        return None
    for path in paths:
        metadata = release_manifest(root, path.name)
        if metadata is not None and metadata["versionCode"] == code:
            return metadata
    return None


def _site(request: web.Request) -> str:
    """Адрес, на который ссылается update.json: тот, откуда спросили.

    Приложение сверяет ссылку на APK со своим сайтом; установленные до
    переезда копии спрашивают прежний домен и должны получить его же.
    """
    web_settings = context(request).settings.web
    own = f"https://{request.host}".lower()
    known = {url.lower() for url in web_settings.all_urls}
    base = own if own in known else web_settings.public_url
    return base.rstrip("/") + web_settings.miniapp_path.rstrip("/")


@routes.get("/android/update.json")
async def latest(request: web.Request) -> web.Response:
    data = release_manifest(directory(request)) if await updates_enabled(context(request)) else None
    if data is None:
        return web.json_response({"available": False}, headers={"Cache-Control": "no-store"})
    data["available"] = True
    settings = context(request).settings.web
    own = f"https://{request.host}".lower()
    if own in {url.lower() for url in settings.all_urls} and own != settings.public_url.lower():
        # Existing APKs pin their original origin and sgoshka-<versionCode>.apk URL.
        data["file"] = f"sgoshka-{data['versionCode']}.apk"
    data["url"] = _site(request) + "/android/" + data["file"]
    return web.json_response(data, headers={"Cache-Control": "no-store"}, dumps=lambda value: json.dumps(value, ensure_ascii=False))


@routes.get("/android/{name}")
async def download(request: web.Request) -> web.StreamResponse:
    if not await updates_enabled(context(request)):
        return json_error("Обновления APK выключены администратором", status=403)
    name = request.match_info["name"]
    root = directory(request)
    if APK_NAME.fullmatch(name):
        code = int(name.removeprefix("sgoshka-").removesuffix(".apk"))
        metadata = release_manifest(root, f"sgoshka-{code}.json") or _manifest_by_code(root, code)
        if metadata is not None and metadata["versionCode"] != code:
            raise web.HTTPNotFound()
        filename = metadata["file"] if metadata else name
    elif VERSION_APK.fullmatch(name):
        metadata = release_manifest(root, name.removesuffix(".apk") + ".json")
        if metadata is None or metadata["file"] != name:
            raise web.HTTPNotFound()
        code, filename = metadata["versionCode"], name
    else:
        raise web.HTTPNotFound()
    if code in await blocked_versions(context(request)):
        return json_error("Эта версия отключена. Скачайте актуальную сборку", status=410)
    path = root / filename
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_APK_BYTES:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": "application/vnd.android.package-archive",
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})
