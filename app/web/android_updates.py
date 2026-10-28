"""Public distribution of signed Android builds; publication is an atomic file swap."""
from __future__ import annotations

import json
import re
from pathlib import Path
from aiohttp import web
from .api import context, json_error
from ..db.repositories import AppFlagRepository

routes = web.RouteTableDef()
APK_NAME = re.compile(r"sgoshka-[1-9][0-9]{0,9}\.apk")
MAX_APK_BYTES = 200 * 1024 * 1024


async def updates_enabled(app) -> bool:
    # Existing installations keep updating unless the owner explicitly pauses the channel.
    return not await AppFlagRepository(app.db).get("android_updates_disabled")


def directory(request: web.Request) -> Path:
    return context(request).settings.data_dir / "android-releases"


def release_manifest(root: Path) -> dict | None:
    path = root / "update.json"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 32768:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("packageName") != "ru.ikrya.sgoshka":
            return None
        code = data.get("versionCode")
        if type(code) is not int or not 0 < code <= 2_100_000_000:
            return None
        if data.get("file") != f"sgoshka-{code}.apk":
            return None
        if not isinstance(data.get("versionName"), str) or not 0 < len(data["versionName"]) <= 64:
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


@routes.get("/android/update.json")
async def latest(request: web.Request) -> web.Response:
    data = release_manifest(directory(request)) if await updates_enabled(context(request)) else None
    if data is None:
        return web.json_response({"available": False}, headers={"Cache-Control": "no-store"})
    data["available"] = True
    data["url"] = context(request).settings.web.miniapp_url.rstrip("/") + "/android/" + data["file"]
    return web.json_response(data, headers={"Cache-Control": "no-store"})


@routes.get("/android/{name}")
async def download(request: web.Request) -> web.StreamResponse:
    if not await updates_enabled(context(request)):
        return json_error("Обновления APK выключены администратором", status=403)
    name = request.match_info["name"]
    if not APK_NAME.fullmatch(name):
        raise web.HTTPNotFound()
    path = directory(request) / name
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_APK_BYTES:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": "application/vnd.android.package-archive",
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
