"""Поток ответа модели в формате Anthropic Messages (/v1/messages).

Решениям нужны картинки страниц, а провайдер помощника (Villnus) молча
выбрасывает их из OpenAI-формата (/chat/completions) — принимает только здесь.
Ключ, адрес и прокси — помощника.
"""

from __future__ import annotations

import json
import logging

import aiohttp

from ..settings import AssistantSettings
from ..web.assistant import AssistantError

logger = logging.getLogger("netschoolbot.solver")


async def stream_messages(settings: AssistantSettings, system: str, content: list[dict], *,
                          model: str, max_tokens: int, timeout: float):
    connector = None
    if settings.proxy:
        from aiohttp_socks import ProxyConnector
        connector = ProxyConnector.from_url(settings.proxy, rdns=True)
    body = {
        "model": model, "max_tokens": max_tokens, "temperature": 0.2, "stream": True,
        "system": system, "messages": [{"role": "user", "content": content}],
    }
    headers = {"x-api-key": settings.api_key, "anthropic-version": "2023-06-01"}
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.post(f"{settings.base_url}/messages", json=body, headers=headers) as response:
                if response.status == 429:
                    raise AssistantError("Модель сейчас перегружена. Попробуйте через минуту.", status=503, reason="busy")
                if response.status >= 400:
                    logger.warning("Решение: провайдер ответил %s: %s", response.status, (await response.text())[:300])
                    raise AssistantError("Решение сейчас недоступно. Попробуйте чуть позже.")
                finished = False
                async for raw in response.content:
                    line = raw.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    kind = event.get("type")
                    if kind == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield delta["text"]
                    elif kind == "message_stop":
                        finished = True
                        break
                    elif kind == "error":
                        logger.warning("Решение: ошибка в потоке: %s", str(event)[:300])
                        raise AssistantError("Решение сейчас недоступно. Попробуйте чуть позже.")
                if not finished:
                    raise AssistantError("Ответ прервался. Попробуйте ещё раз.")
    except (aiohttp.ClientError, TimeoutError, ValueError, UnicodeError, AttributeError, TypeError) as exc:
        logger.warning("Поток решения прервался: %s", type(exc).__name__)
        raise AssistantError("Ответ прервался. Попробуйте ещё раз.") from exc
