"""«Неверный пароль» — только когда «Сетевой город» так и пишет.

На отказ во входе (409) netschoolpy смотрит в текст сервера, а если тот ни
на что не похож или пуст, считает, что не подошёл пароль. Когда вход
сломан у самого «Сетевого города» (сайт открывается, а войти нельзя),
бот из-за этого писал всем «возможно, вы сменили пароль — войдите заново».

Здесь такой отказ без слов о логине или пароле — временный сбой сервера:
бот повторит вход сам и не просит человека ничего менять.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("netschoolbot.netschool")

# Слова, по которым ясно, что школа не приняла именно логин или пароль.
PASSWORD_MARKERS = ("парол", "логин", "имя пользователя", "имени пользователя", "password", "login")

_applied = False


def says_wrong_password(message: str | None) -> bool:
    low = (message or "").lower()
    return any(marker in low for marker in PASSWORD_MARKERS)


def apply() -> None:
    """Применить правку. Безопасно вызывать несколько раз."""
    global _applied
    if _applied:
        return
    try:
        from netschoolpy import client, exceptions, mobile
    except ImportError as exc:
        logger.warning("Не удалось пропатчить разбор отказа во входе netschoolpy: %s", exc)
        return
    original = getattr(client, "conflict_error", None)
    if original is None or getattr(mobile, "conflict_error", None) is not original:
        logger.warning("В netschoolpy нет conflict_error — отказ во входе без объяснения сочтут неверным паролем")
        return

    def patched(message):
        error = original(message)
        if type(error) is exceptions.InvalidCredentials and not says_wrong_password(message):
            text = (message or "").strip()
            return exceptions.ServerUnavailable(
                "вход: «Сетевой город» отказал без слов о пароле" + (f": {text[:200]}" if text else ""))
        return error

    client.conflict_error = patched
    mobile.conflict_error = patched
    _applied = True
