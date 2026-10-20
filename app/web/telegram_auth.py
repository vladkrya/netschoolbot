"""Проверка подписи Telegram Mini App.

Telegram передаёт мини-приложению строку `initData` с данными о пользователе
и полем `hash`. Подпись считается по токену бота, поэтому подделать её без
токена нельзя — а значит, `initData` можно доверять как удостоверению
личности и не спрашивать никаких паролей.

Схема описана в документации Telegram и здесь реализована буквально:

    secret = HMAC_SHA256(key="WebAppData", message=bot_token)
    hash   = HMAC_SHA256(key=secret, message=data_check_string)

где `data_check_string` — все поля кроме `hash`, отсортированные по имени
и склеенные через перевод строки как `ключ=значение`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

logger = logging.getLogger("netschoolbot.web")

# Сколько считаем подпись действительной. Telegram кладёт в initData время
# выдачи; ограничение защищает от повторного использования перехваченной
# строки спустя долгое время.
MAX_AGE_SECONDS = 24 * 3600


class InitDataError(Exception):
    """initData не прошла проверку."""


@dataclass(frozen=True, slots=True)
class TelegramUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part)


def verify_init_data(
    init_data: str, bot_token: str, *, max_age: int = MAX_AGE_SECONDS
) -> TelegramUser:
    """Проверить подпись и вернуть пользователя. Иначе — InitDataError."""
    if not init_data:
        raise InitDataError("initData пуста")
    if not bot_token:
        raise InitDataError("Токен бота не задан, проверить подпись нечем")

    # parse_qsl со strict_parsing даёт осмысленную ошибку на мусоре вместо
    # молчаливого пустого результата, который выглядел бы как «нет полей».
    try:
        fields = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError as exc:
        raise InitDataError("initData не разбирается") from exc

    received_hash = fields.pop("hash", "")
    if not received_hash:
        raise InitDataError("В initData нет подписи")

    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()

    # compare_digest, а не ==: обычное сравнение строк завершается на первом
    # различии и по времени ответа позволяет подбирать подпись посимвольно.
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("Подпись initData не совпала")

    auth_date = fields.get("auth_date")
    if auth_date:
        try:
            issued_at = int(auth_date)
        except ValueError as exc:
            raise InitDataError("Некорректная дата в initData") from exc
        if max_age and time.time() - issued_at > max_age:
            raise InitDataError("initData устарела, переоткройте приложение")

    return _parse_user(fields.get("user", ""))


def _parse_user(raw: str) -> TelegramUser:
    if not raw:
        raise InitDataError("В initData нет пользователя")
    try:
        data = json.loads(raw)
        return TelegramUser(
            id=int(data["id"]),
            first_name=str(data.get("first_name") or ""),
            last_name=str(data.get("last_name") or ""),
            username=str(data.get("username") or ""),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise InitDataError("Не удалось прочитать пользователя из initData") from exc


# Telegram Login Widget на сайте: подпись проще, чем у мини-приложения, —
# ключ это SHA256 от токена бота, а поля приходят плоским словарём, без JSON
# в `user`. https://core.telegram.org/widgets/login#checking-authorization


def verify_login_widget(
    data: dict, bot_token: str, *, max_age: int = MAX_AGE_SECONDS
) -> TelegramUser:
    """Проверить ответ Telegram Login Widget и вернуть пользователя."""
    if not bot_token:
        raise InitDataError("Токен бота не задан, проверить подпись нечем")
    received_hash = str(data.get("hash") or "")
    if not received_hash:
        raise InitDataError("В ответе виджета нет подписи")
    # Подписаны все поля, кроме hash, — и те, о которых мы не знаем.
    fields = {str(key): str(value) for key, value in data.items() if key != "hash" and value is not None}
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("Подпись виджета не совпала")
    try:
        issued_at = int(fields["auth_date"])
        user_id = int(fields["id"])
    except (KeyError, ValueError) as exc:
        raise InitDataError("В ответе виджета нет даты или пользователя") from exc
    if max_age and time.time() - issued_at > max_age:
        raise InitDataError("Вход устарел, войдите ещё раз")
    return TelegramUser(
        id=user_id,
        first_name=fields.get("first_name", ""),
        last_name=fields.get("last_name", ""),
        username=fields.get("username", ""),
    )
