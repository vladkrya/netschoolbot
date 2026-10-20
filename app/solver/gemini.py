"""Условие упражнения со скана текстом — его читает Gemini.

Villnus считает картинку по размеру файла как текст: вырезка номера — около
15 тысяч токенов. Google считает по пикселям — около тысячи, и Flash-Lite
читает формулы без ошибок. Прочитанное условие сохраняется (exercise_conditions)
и уходит решающей модели текстом. Не прочиталось — номер уходит картинкой,
как раньше.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re

import aiohttp

from ..settings import SolverSettings

logger = logging.getLogger("netschoolbot.solver")

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT = 60
ATTEMPTS = 2
RETRY_DELAY = 2
PARALLEL = 4
# Номер не нашёлся на вырезке или прочитан неуверенно — картинку отдаём решающей модели.
NOT_FOUND = "НЕТ НОМЕРА"
UNSURE = "[?]"

PROMPT = (
    "На картинке — упражнение №{n} из школьного учебника, вырезка со скана{more}. "
    "Чтобы решить его, нужно условие текстом. Перепиши условие этого номера точно, "
    "со всеми пунктами а), б)…, без решения и без пояснений. Формулы — в LaTeX внутри $…$, "
    "системы — \\begin{{cases}}…\\end{{cases}}, химические формулы — тоже в $…$ ($H_2SO_4$, "
    "степень окисления — $\\overset{{+2}}{{Hg}}$). Рисунок к задаче опиши одной строкой "
    "в квадратных скобках. Без жирного шрифта и заголовков. Текст соседних номеров, "
    "попавших на край, не пиши. Если какой-то символ читается неуверенно, поставь после "
    "него [?]. Если номера {n} на картинке нет, ответь одной строкой: " + NOT_FOUND + "."
)


def _clean(text: str) -> str:
    text = text.replace("**", "").strip()
    return re.sub(r"^```\w*\n|\n```$", "", text).strip()


async def _read_one(session: aiohttp.ClientSession, settings: SolverSettings, number: int,
                    crops: list[bytes]) -> str | None:
    more = ", продолжение — на следующих картинках" if len(crops) > 1 else ""
    parts = [{"text": PROMPT.format(n=number, more=more)}]
    parts += [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(c).decode()}} for c in crops]
    body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"temperature": 0}}
    url = URL.format(model=settings.gemini_model)
    for attempt in range(1, ATTEMPTS + 1):
        try:
            async with session.post(url, json=body, headers={"x-goog-api-key": settings.gemini_key}) as response:
                if response.status in (429, 500, 502, 503, 504) and attempt < ATTEMPTS:
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                if response.status >= 400:
                    logger.warning("Gemini: №%s — ответ %s: %s", number, response.status, (await response.text())[:200])
                    return None
                data = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.warning("Gemini: №%s — %s", number, type(exc).__name__)
            if attempt < ATTEMPTS:
                continue
            return None
        candidate = (data.get("candidates") or [{}])[0]
        text = _clean("".join(p.get("text", "") for p in (candidate.get("content") or {}).get("parts", [])
                              if not p.get("thought")))
        usage = data.get("usageMetadata") or {}
        logger.info("Gemini: №%s прочитан, токенов %s/%s", number, usage.get("promptTokenCount"),
                    usage.get("candidatesTokenCount"))
        if NOT_FOUND in text or UNSURE in text or not 20 <= len(text) <= 4000:
            logger.info("Gemini: №%s не годится (%s симв.), уйдёт картинкой", number, len(text))
            return None
        return text
    return None


async def read_conditions(settings: SolverSettings, crops: dict[int, list[bytes]]) -> dict[int, str]:
    """Условия номеров с их вырезок (начало и продолжение). Что не
    прочиталось, в ответе нет."""
    if not settings.gemini_key or not crops:
        return {}
    connector = None
    if settings.gemini_proxy:
        from aiohttp_socks import ProxyConnector
        connector = ProxyConnector.from_url(settings.gemini_proxy, rdns=True)
    limit = asyncio.Semaphore(PARALLEL)

    async def one(number: int, pictures: list[bytes]) -> tuple[int, str | None]:
        async with limit:
            return number, await _read_one(session, settings, number, pictures)

    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
        results = await asyncio.gather(*(one(n, p) for n, p in crops.items()))
    return {number: text for number, text in results if text}
