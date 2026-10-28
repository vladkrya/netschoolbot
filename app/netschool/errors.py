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
    CREDENTIALS = "credentials"  # школа не приняла логин или пароль: их сменили
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
    def breaks_diary(self) -> bool:
        """Сбой «Сетевого города», а не одного аккаунта: страница статуса
        считает такую проверку дневника неудачной. Вход, который школа не
        пустила со своим объяснением, — тоже UNKNOWN: так выглядит сломанный
        вход у всех сразу, когда сайт при этом открывается."""
        return self in (Reason.SERVER_UNAVAILABLE, Reason.UNKNOWN)

    @property
    def needs_relogin(self) -> bool:
        return self in (Reason.AUTH, Reason.CREDENTIALS)


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
    "сессия истекла", "авторизуйтесь", "не сессия мобильного api",
)
_SERVER_MARKERS = (
    "timeout", "temporarily unavailable", "service unavailable",
    "all connection attempts failed", "server disconnected", "connection refused",
    # httpx пишет «Server error '500 Internal Server Error'»; 500 у школьного
    # сервера — такой же временный сбой, а считался «неизвестной ошибкой»,
    # без повтора и без сохранённых данных.
    "500", "502", "503", "504", "internal server error",
    "maintenance", "технические работы", "технических работ", "техработ",
    "expecting value: line 1 column 1",
)
_ESIA_MARKERS = ("esia", "gosuslugi", "госуслуг")
_RATE_MARKERS = ("429", "too many requests")

_USER_MESSAGES = {
    Reason.AUTH: "Вход в «Сетевой город» истёк, а повторить его без вас нельзя. Войти заново: /login",
    Reason.CREDENTIALS: "«Сетевой город» не принял логин или пароль — возможно, их сменили. Войти заново: /login",
    Reason.SERVER_UNAVAILABLE: (
        "Сервер «Сетевого города» недоступен. Попробуем подключиться позже — ничего делать не нужно."
    ),
    Reason.ESIA: "Госуслуги сейчас недоступны. Попробуйте войти чуть позже.",
    Reason.MFA: "Нужно подтвердить вход — проверьте телефон или приложение «Госуслуги».",
    Reason.RATE_LIMIT: "«Сетевой город» ограничил частоту запросов. Попробуйте позже.",
    Reason.UNKNOWN: "Не удалось получить данные из «Сетевого города». Попробуйте позже.",
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
    """Классификация по классу исключения netschoolpy.

    С 9.6.0 библиотека называет причину неудачного входа классом. Подклассы
    проверяются раньше родителей: ESIAInvalidCredentials — это и ESIAError,
    но человеку важнее, что не подошёл пароль.
    """
    from netschoolpy import exceptions as ns

    reason = _classify_myschool(exc)
    if reason is not None:
        return reason
    if isinstance(exc, ns.RefreshTokenRejected):
        # Истёк refresh-токен мобильного API: нужен новый вход, как при
        # протухшей сессии, хотя это и LoginRejected.
        return Reason.AUTH
    if isinstance(exc, ns.MFAError):
        return Reason.MFA
    if isinstance(exc, (ns.InvalidCredentials, ns.SchoolNotFound)):
        return Reason.CREDENTIALS
    if isinstance(exc, ns.AccountLocked):
        # Блокировка после неудачных попыток: с верным паролем тоже не
        # пускает, поэтому ждём, а не просим новый пароль.
        return Reason.RATE_LIMIT
    if isinstance(exc, ns.SessionExpired):
        if any(marker in str(exc).lower() for marker in _SERVER_MARKERS):
            return Reason.SERVER_UNAVAILABLE
        return Reason.AUTH
    if isinstance(exc, ns.ESIAError):
        return Reason.ESIA
    if isinstance(exc, ns.ServerUnavailable):
        return Reason.SERVER_UNAVAILABLE
    return None


def _classify_myschool(exc: BaseException) -> Reason | None:
    """Исключения myschoolpy («Госуслуги Моя школа»)."""
    try:
        from myschoolpy import exceptions as ms
    except ImportError:
        return None
    if isinstance(exc, ms.MFAError):
        return Reason.MFA
    if isinstance(exc, ms.InvalidCredentials):
        return Reason.CREDENTIALS
    if isinstance(exc, ms.AccountLocked):
        return Reason.RATE_LIMIT
    if isinstance(exc, ms.SessionExpired):
        return Reason.AUTH
    if isinstance(exc, ms.ESIAError):
        return Reason.ESIA
    if isinstance(exc, (ms.ServerUnavailable, ms.DataNotReady)):
        return Reason.SERVER_UNAVAILABLE
    return None


def _myschool_exceptions():
    try:
        from myschoolpy import exceptions as ms
    except ImportError:
        return None
    return ms


def _special_message(exc: BaseException, reason: Reason) -> str | None:
    """Текст для человека, когда общий текст причины неточен."""
    from netschoolpy import exceptions as ns

    ms = _myschool_exceptions()
    if ms is not None:
        if isinstance(exc, ms.InvalidCredentials):
            return "Госуслуги не приняли логин или пароль. Войти заново: /login"
        if isinstance(exc, ms.AccountLocked):
            return "Госуслуги временно не пускают: много попыток входа или нужна капча. Попробуем позже."
    if isinstance(exc, ns.ESIAInvalidCredentials):
        return "Госуслуги не приняли логин или пароль — возможно, их сменили. Войти заново: /login"
    if isinstance(exc, ns.SchoolNotFound):
        return "«Сетевой город» не нашёл школу — возможно, её переименовали. Войти заново: /login"
    if isinstance(exc, ns.ESIAAccountLocked):
        return "Госуслуги временно не пускают: много попыток входа или нужна капча. Попробуем позже."
    if isinstance(exc, ns.AccountLocked):
        return "«Сетевой город» временно не пускает после неудачных попыток входа. Попробуем позже."
    if isinstance(exc, ns.LoginRejected) and reason is Reason.UNKNOWN and str(exc):
        # Причину отказа пишет сервер школы — без неё непонятно, что не так.
        return f"«Сетевой город» отказал во входе: {str(exc)[:200]}"
    return None


def login_message(error: NetSchoolError) -> str:
    """Текст ошибки, когда человек сам только что ввёл логин и пароль:
    «возможно, их сменили» и «войти заново» тут не к месту."""
    if error.reason is Reason.CREDENTIALS and "школ" not in error.user_message:
        source = "Госуслуг" if "Госуслуги" in error.user_message else "«Сетевого города»"
        return f"Неверный логин или пароль от {source}."
    return error.user_message.removesuffix(" Войти заново: /login")


def wrap(exc: Exception, *, context: str = "") -> NetSchoolError:
    """Превратить любое исключение в NetSchoolError с внятным текстом."""
    if isinstance(exc, NetSchoolError):
        return exc
    reason = classify(exc)
    message = _special_message(exc, reason) or _USER_MESSAGES[reason]
    if reason is Reason.MFA and str(exc):
        # У MFA-ошибок текст библиотеки уже написан для человека.
        message = str(exc)
    if reason is Reason.UNKNOWN:
        # Иначе за общим «Не удалось получить данные» не видно настоящей причины.
        logger.warning(
            "Неизвестная ошибка «Сетевого города» в %s: %s: %s",
            context or "запросе", type(exc).__name__, exc,
        )
    elif context:
        logger.debug("Ошибка «Сетевого города» (%s) в %s: %s", reason.value, context, exc)
    return NetSchoolError(reason, message, cause=exc)
