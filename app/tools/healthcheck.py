"""Внешняя проверка, что бот жив: python -m app.tools.healthcheck.

Запускается таймером systemd раз в пару минут, отдельно от самого бота:
упавший процесс о себе не сообщит. Две неудачи подряд — сообщение админу,
после восстановления — ещё одно. Состояние — в маленьком файле рядом с базой.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

from .notify_admin import send

URL = "http://127.0.0.1:8283/health"
FAILURES_TO_ALERT = 2


def probe(url: str = URL) -> bool:
    try:
        return urllib.request.urlopen(url, timeout=10).status == 200
    except Exception:  # noqa: BLE001 — любая ошибка значит «не отвечает»
        return False


def step(state: dict, healthy: bool, now: float) -> tuple[dict, str | None]:
    """Новое состояние и сообщение админу, если пора."""
    failures = 0 if healthy else state.get("failures", 0) + 1
    down_since = state.get("down_since")
    message = None
    if not healthy and failures == FAILURES_TO_ALERT:
        down_since = state.get("first_failure", now)
        message = "🔴 Бот не отвечает на /health уже несколько минут. Проверьте сервер: systemctl status netschoolbot"
    if healthy and down_since is not None:
        minutes = max(1, round((now - down_since) / 60))
        message = f"🟢 Бот снова отвечает (простой ~{minutes} мин)"
        down_since = None
    return {
        "failures": failures,
        "first_failure": state.get("first_failure", now) if failures else None,
        "down_since": down_since,
    }, message


def main(argv: list[str] | None = None) -> int:
    path = Path(argv[0] if argv else sys.argv[1] if len(sys.argv) > 1 else "data/health-state.json")
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {}
    state, message = step(state, probe(), time.time())
    path.write_text(json.dumps(state))
    if message:
        asyncio.run(send(message))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
