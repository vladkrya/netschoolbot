"""Классификация ошибок «Сетевого города».

В старом коде было пять почти одинаковых предикатов, разбросанных по трём
модулям: `is_server_unavailable_error`, `is_netschool_auth_error`,
`_is_netschool_transient_error`, `_is_netschool_auth_error`,
`_classify_netschool_exception`. Они расходились в деталях, поэтому одна и та
же ошибка в боте считалась временной, а в мини-приложении — фатальной.

Теперь классификация одна, и она возвращает не булево, а причину: по ней
вызывающий код решает, повторять запрос, просить перелогиниться или сдаться.
"""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger("netschoolbot.netschool")


class Reason(str, Enum):
    """Почему запрос к «Сетевому городу» не удался."""

    AUTH = "auth"                # сессия протухла, нужен повторный вход
    SERVER_UNAVAILABLE = "server"  # школьный сервер недоступен, стоит повторить
    ESIA = "esia"                # проблема на стороне Госуслуг
    MFA = "mfa"                  # нужен шаг от пользователя (код, «МАКС»)
    RATE_LIMIT = "rate_limit"    # слишком часто, надо подождать
    UNKNOWN = "unknown"          # всё остальное — не повторяем вслепую

    @property
    def is_retryable(self) -> bool:
        """Имеет ли смысл повторить тот же запрос без участия человека."""
        return self in (Reason.SERVER_UNAVAILABLE, Reason.RATE_LIMIT)

    @property
    def needs_relogin(self) -> bool:
        return self is Reason.AUTH


class NetSchoolError(Exception):
    """Ошибка обращения к «Сетевому городу» с понятной причиной.

    `user_message` — то, что не стыдно показать человеку. Раньше в чат
    прилетал сырой traceback вида «Ошибка подтверждения кода: 202 {...}».
    """

    def __init__(self, reason: Reason, user_message: str, *, cause: Exception | None = None):
        super().__init__(user_message)
        self.reason = reason
        self.user_message = user_message
        self.cause = cause


_AUTH_MARKERS = (
    "401", "unauthorized", "not authorized", "authorization required",
    "session expired", "не авториз", "forbidden",
    # Так netschoolpy 3.x пишет о протухшей сессии («Сессия истекла.
    # Авторизуйтесь заново.») — без этих маркеров она считалась неизвестной
    # ошибкой, и мёртвая сессия выдавалась за живую.
    "сессия истекла", "авторизуйтесь",
)
_SERVER_MARKERS = (
    "timeout", "temporarily unavailable", "service unavailable",
    "all connection attempts failed", "server disconnected", "connection refused",
    "502", "503", "504",
)
_ESIA_MARKERS = ("esia", "gosuslugi", "госуслуг")
_RATE_MARKERS = ("429", "too many requests")

_USER_MESSAGES = {
    Reason.AUTH: "Сессия «Сетевого города» истекла. Нужно войти заново: /login",
    Reason.SERVER_UNAVAILABLE: (
        "Сервер школы не отвечает. Это на их стороне — попробую ещё раз автоматически."
    ),
    Reason.ESIA: "Госуслуги сейчас недоступны. Попробуйте войти чуть позже.",
    Reason.MFA: "Нужно подтвердить вход — проверьте телефон или приложение «Госуслуги».",
    Reason.RATE_LIMIT: "Слишком много запросов к школьному серверу. Подождём и повторим.",
    Reason.UNKNOWN: "Не удалось получить данные из «Сетевого города».",
}


def classify(exc: BaseException) -> Reason:
    """Определить причину ошибки по типу исключения и его тексту."""
    reason = _classify_by_type(exc)
    if reason is not None:
        return reason

    text = str(exc).lower()
    name = type(exc).__name__.lower()
    haystack = f"{name} {text}"

    # Порядок важен: у ошибки ЕСИА текст часто содержит и «timeout», но
    # реагировать на неё надо иначе, чем на недоступность школьного сервера.
    if any(marker in haystack for marker in _RATE_MARKERS):
        return Reason.RATE_LIMIT
    if any(marker in haystack for marker in _AUTH_MARKERS):
        return Reason.AUTH
    if any(marker in haystack for marker in _ESIA_MARKERS):
        return Reason.ESIA
    if any(marker in haystack for marker in _SERVER_MARKERS):
        return Reason.SERVER_UNAVAILABLE
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return Reason.SERVER_UNAVAILABLE
    return Reason.UNKNOWN


def _classify_by_type(exc: BaseException) -> Reason | None:
    """Классификация по классу исключения netschoolpy, если он доступен."""
    try:
        from netschoolpy import exceptions as ns
    except ImportError:
        return None

    for attr, reason in (
        ("MFAError", Reason.MFA),
        ("AuthError", Reason.AUTH),
        # В netschoolpy 3.3.3 класса AuthError нет — протухшая сессия
        # приходит как SessionExpired.
        ("SessionExpired", Reason.AUTH),
        ("ESIAError", Reason.ESIA),
        ("ServerUnavailable", Reason.SERVER_UNAVAILABLE),
    ):
        exc_class = getattr(ns, attr, None)
        if exc_class is not None and isinstance(exc, exc_class):
            return reason
    return None


def wrap(exc: Exception, *, context: str = "") -> NetSchoolError:
    """Превратить любое исключение в NetSchoolError с внятным текстом."""
    if isinstance(exc, NetSchoolError):
        return exc
    reason = classify(exc)
    message = _USER_MESSAGES[reason]
    if reason is Reason.MFA and str(exc):
        # У MFA-ошибок текст библиотеки уже написан для человека.
        message = str(exc)
    if context:
        logger.debug("Ошибка «Сетевого города» (%s) в %s: %s", reason.value, context, exc)
    return NetSchoolError(reason, message, cause=exc)
