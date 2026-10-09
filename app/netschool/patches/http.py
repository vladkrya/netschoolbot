"""Правки HTTP-слоя netschoolpy.

Перенесено из старого `netschool/http_patch.py` — это выстраданное знание о
поведении чужой библиотеки, переписывать его с нуля было бы вредительством.

Суть проблемы: netschoolpy при первой же неудаче прямого соединения (таймаут
по умолчанию — 5 секунд) помечает хост в глобальном `_tor_hosts` и до конца
жизни процесса гонит все запросы к нему через Tor. Если Tor мёртв или
заблокирован, бот получает «Сервер не ответил (Tor)» даже тогда, когда прямое
соединение прекрасно работает — именно так ломался поиск школ.

Не трогая саму библиотеку:
  * поднимаем таймаут прямого соединения (медленный ответ ≠ недоступный хост);
  * переводим fallback с Tor на рабочий SOCKS5, если он поднят;
  * даём пометке «хост требует прокси» срок жизни, чтобы одна неудача не
    выключала прямые запросы навсегда;
  * заданный прокси — основной маршрут, но не единственный: если он не
    соединяется с сервером школы, тот же запрос уходит напрямую с сервера
    бота (см. `ProxyFallbackTransport`).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urlparse

import certifi
import httpx

logger = logging.getLogger("netschoolbot.netschool")

DEFAULT_FALLBACK_CANDIDATES = ("socks5://127.0.0.1:1080",)

_applied = False

# Сколько секунд после отказа прокси ходить к хосту сразу напрямую, не
# тратя время на заведомо неудачную попытку.
PROXY_RETRY_AFTER_S = 120


# Отказ самого прокси: не соединился, оборвал рукопожатие SOCKS («Malformed
# reply»), не дотянулся до сервера школы. Таймауты чтения сюда не входят —
# запрос мог уже дойти до школы.
_PROXY_FAILURES = (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout, httpx.ProtocolError)


class ProxyFallbackTransport(httpx.AsyncBaseTransport):
    """Транспорт httpx: сначала через прокси, при отказе — напрямую.

    Повторяем только ошибки соединения: запрос до сервера школы ещё не дошёл,
    повтор безопасен. Ответ сервера (в том числе 403) — это ответ, его не
    подменяем. Куки и заголовки живут в клиенте, поэтому вход и сессия
    переживают смену маршрута.
    """

    _down: dict[str, float] = {}

    def __init__(self, proxy: str, **kwargs) -> None:
        self._proxy = proxy
        self._via_proxy = httpx.AsyncHTTPTransport(proxy=proxy, **kwargs)
        self._direct = httpx.AsyncHTTPTransport(**kwargs)

    async def handle_async_request(self, request):
        host = request.url.host
        if time.monotonic() - self._down.get(host, -PROXY_RETRY_AFTER_S) >= PROXY_RETRY_AFTER_S:
            try:
                response = await self._via_proxy.handle_async_request(request)
                self._down.pop(host, None)
                return response
            except _PROXY_FAILURES as exc:
                logger.warning("Прокси не довёл запрос до %s (%s), пробуем напрямую", host, exc)
                self._down[host] = time.monotonic()
        return await self._direct.handle_async_request(request)

    async def aclose(self) -> None:
        await self._via_proxy.aclose()
        await self._direct.aclose()


RUSSIAN_CA = Path(__file__).resolve().parent.parent / "certs" / "russian_trusted_ca.pem"


@functools.cache
def school_ssl_context() -> ssl.SSLContext:
    """Обычные доверенные центры плюс центр Минцифры.

    Часть школьных серверов (Калуга) подписана «Russian Trusted Sub CA»,
    которого нет в стандартном наборе, и соединение падало на проверке
    сертификата. Только для школьных клиентов: Telegram и прочее не трогаем.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cafile=str(RUSSIAN_CA))
    return context


def _with_fallback(kwargs: dict) -> dict:
    """Настроить новый httpx-клиент к школе: доверие к центру Минцифры и
    запасной маршрут вместо `proxy=`."""
    kwargs = dict(kwargs)
    kwargs.setdefault("verify", school_ssl_context())
    proxy = kwargs.get("proxy")
    if not proxy or kwargs.get("transport") is not None:
        return kwargs
    # С собственным транспортом httpx не смотрит на verify клиента — передаём его транспорту.
    kwargs.pop("proxy")
    kwargs["transport"] = ProxyFallbackTransport(str(proxy), verify=kwargs.pop("verify"))
    return kwargs


def _install_proxy_fallback(ns_http) -> None:
    import netschoolpy.mobile as ns_mobile

    original_create = ns_http.HttpSession._create_async_client
    original_mobile = ns_mobile._new_client
    ns_http.HttpSession._create_async_client = staticmethod(lambda **kw: original_create(**_with_fallback(kw)))
    ns_mobile._new_client = lambda **kw: original_mobile(**_with_fallback(kw))


class ExpiringHostSet(set):
    """Множество хостов с TTL: пометка о недоступности сама протухает."""

    def __init__(self, ttl: int) -> None:
        super().__init__()
        self._ttl = ttl
        self._added: dict[str, float] = {}

    def add(self, item) -> None:
        self._added[item] = time.monotonic()
        super().add(item)

    def discard(self, item) -> None:
        self._added.pop(item, None)
        super().discard(item)

    def __contains__(self, item) -> bool:
        added_at = self._added.get(item)
        if added_at is not None and time.monotonic() - added_at > self._ttl:
            self.discard(item)
            logger.info("Снята пометка «нужен прокси» с %s, пробуем напрямую", item)
            return False
        return super().__contains__(item)


def _proxy_reachable(proxy_url: str, timeout: float = 0.7) -> bool:
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        return False
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=timeout):
            return True
    except OSError:
        return False


def resolve_fallback_proxy(configured: str = "") -> str | None:
    """Рабочий SOCKS5 для замены Tor, либо None."""
    if configured.lower() in {"off", "none", "no"}:
        return None
    candidates = [configured] if configured else list(DEFAULT_FALLBACK_CANDIDATES)
    return next((c for c in candidates if c and _proxy_reachable(c)), None)


def reset_blocked_hosts() -> None:
    """Сбросить накопленные пометки о «заблокированных» хостах."""
    try:
        import netschoolpy.http as ns_http
    except ImportError:
        return
    ns_http._tor_hosts.clear()


def apply(*, timeout: int, blocked_host_ttl: int, fallback_proxy: str = "") -> None:
    """Применить правки. Безопасно вызывать несколько раз."""
    global _applied
    if _applied:
        return
    try:
        import netschoolpy.http as ns_http
    except ImportError as exc:
        logger.warning("netschoolpy.http недоступен, правки не применены: %s", exc)
        return

    ns_http._DEFAULT_TIMEOUT = timeout
    ns_http._tor_hosts = ExpiringHostSet(blocked_host_ttl)
    _install_proxy_fallback(ns_http)

    disabled = fallback_proxy.lower() in {"off", "none", "no"}
    proxy = resolve_fallback_proxy(fallback_proxy)
    if disabled:
        # В netschoolpy отсутствие SOCKS не отключает Tor. Явный off должен
        # сохранять прямой маршрут даже при таймауте и не отправлять вход через Tor.
        import httpx
        from netschoolpy.exceptions import ServerUnavailable

        original_send = ns_http.HttpSession._send
        original_create = ns_http.HttpSession._create_async_client

        def create_client(**kwargs):
            kwargs['trust_env'] = False
            return original_create(**kwargs)

        async def direct_send(self, method, path, *, timeout=None, follow_redirects=False, **kwargs):
            if self._external_proxy:
                return await original_send(self, method, path, timeout=timeout,
                                           follow_redirects=follow_redirects, **kwargs)

            async def request():
                retries = 0
                while True:
                    try:
                        req = self._client.build_request(method, path,
                            **{key: value for key, value in kwargs.items() if value is not None})
                        return await self._client.send(req, follow_redirects=follow_redirects)
                    except httpx.ReadTimeout:
                        await asyncio.sleep(0.1)
                    except httpx.HTTPStatusError as exc:
                        if not 500 <= exc.response.status_code < 600 or retries >= 3:
                            raise
                        retries += 1
                        await asyncio.sleep(0.2 * retries)

            try:
                return await asyncio.wait_for(request(), self._timeout if timeout is None else timeout)
            except (asyncio.TimeoutError, httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise ServerUnavailable('Школьный сервер недоступен при прямом подключении') from exc

        ns_http.HttpSession._create_async_client = staticmethod(create_client)
        ns_http.HttpSession._send = direct_send
        # Мобильный API создаёт свой httpx-клиент: общий ALL_PROXY из .env
        # (нужный Telegram) также не должен менять школьный маршрут.
        import netschoolpy.mobile as ns_mobile
        original_mobile_create = ns_mobile._new_client

        def create_mobile_client(**kwargs):
            kwargs['trust_env'] = False
            return original_mobile_create(**kwargs)

        ns_mobile._new_client = create_mobile_client
        logger.info("Автоматический прокси/Tor отключён: прямое подключение к школьным серверам")
    elif proxy:
        ns_http._TOR_PROXY = proxy
        logger.info("Fallback для «Сетевого города»: %s (вместо Tor)", proxy)
    else:
        logger.info("Рабочий SOCKS5 не найден, fallback остаётся на Tor")

    logger.info(
        "Таймаут прямых запросов: %sс, пометка о недоступности живёт %sс",
        timeout,
        blocked_host_ttl,
    )
    _applied = True
