"""Запись превью: поднимает веб-сервер бота, как в тестах, и гоняет сценарий
Playwright по демо-режиму (/mini/demo). Не тест — запускается из build.sh:

    SCRIPT=tools/preview/record.mjs ARGS=out/rec python -m pytest -q -s tools/preview/test_record.py

В CI не попадает: pytest.ini собирает тесты только из tests/.
"""
import asyncio
import os

import pytest

from tests.conftest import *  # noqa: F401,F403 — фикстуры базы и пользователей
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_web import client  # noqa: F401 — фикстура

pytestmark = pytest.mark.skipif(not os.environ.get("SCRIPT"), reason="только для записи превью")


async def test_record(client):
    url = str(client.make_url("/mini/demo"))
    proc = await asyncio.create_subprocess_exec("node", os.environ["SCRIPT"], url, *os.environ.get("ARGS", "").split())
    assert await proc.wait() == 0
