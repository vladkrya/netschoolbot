"""Правки netschoolpy для учительского аккаунта.

1. Автоматический выбор роли «Сотрудник» / «Учитель», если у аккаунта есть
   и родительская, и учительская роль (по умолчанию netschoolpy всегда выбирает
   родительскую роль, с которой классный журнал отдаёт 401).
2. Безопасный пропуск 401 на student/diary/init при входе учителя (у
   чистого сотрудника нет дневника ученика).
"""

from __future__ import annotations

import logging
from typing import Any
import httpx

logger = logging.getLogger("netschoolbot.netschool")

_applied = False


def apply() -> None:
    global _applied
    if _applied:
        return

    try:
        from netschoolpy import NetSchool
    except ImportError as exc:
        logger.warning("Не удалось импортировать NetSchool для патча: %s", exc)
        return

    orig_login = getattr(NetSchool, "_login_password", None)
    if orig_login is not None:
        async def patched_login(self: Any, user_name: str, password: str, school: Any, timeout: int | None = None) -> None:
            orig_get = self._http.get

            async def custom_get(path: str, **kw: Any) -> Any:
                try:
                    return await orig_get(path, **kw)
                except httpx.HTTPStatusError as exc:
                    if path == "student/diary/init" and exc.response.status_code == 401 and getattr(self, "_prefer_teacher_role", False):
                        class FakeResp:
                            def json(self) -> dict[str, Any]:
                                return {"students": []}
                        return FakeResp()
                    raise

            self._http.get = custom_get
            try:
                await orig_login(self, user_name, password, school, timeout=timeout)
            finally:
                self._http.get = orig_get

        NetSchool._login_password = patched_login

    orig_pick = getattr(NetSchool, "_pick_parent_role", None)
    if orig_pick is not None:
        def patched_pick(*args: Any) -> int | None:
            self = args[0] if len(args) == 2 else None
            roles = args[1] if len(args) == 2 else args[0]
            if self and getattr(self, "_prefer_teacher_role", False):
                for r in roles or []:
                    inner = r.get("role", r)
                    name = (inner.get("name") or "").lower()
                    if any(w in name for w in ("сотрудник", "учитель", "преподаватель", "teacher", "staff")):
                        return inner.get("id")
            return orig_pick(roles)

        NetSchool._pick_parent_role = patched_pick

    _applied = True
