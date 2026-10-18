"""Статус серверов «Сетевого города» по всем регионам.

Бот сам раз в несколько минут ходит на каждый сервер из каталога
мобильного приложения NetSchool и из справочника регионов `netschoolpy`:

* веб — `/webapi/logindata` без входа: отвечает ли сайт и какая версия;
* мобильное API — `/api/mobile/users` без токена: 401 значит «живо» (данных
  без токена не получить, но сервер принял запрос и ответил как положено).

Отдельно учитываются обезличенные итоги фоновых проверок аккаунтов: только
счётчики успехов и ошибок сервера, без ошибок входа и пользовательских данных.

Из каталога берутся и официальные пометки ИРТех: регион отключён в
приложении и почему. Люди могут пожаловаться на сбой, который проверка не
видит (вход, пустые данные).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    from ..db.repositories import SgoStatusRepository
    from .groups import GroupBroadcast

logger = logging.getLogger("netschoolbot.status")

PROBE_EVERY_S = 60
PROBE_TIMEOUT_S = 20
MOBILE_TIMEOUT_S = 10
# Каталог регионов мобильного приложения меняется редко.
CATALOG_EVERY_S = 6 * 3600
CATALOG_URL = "https://mobile.ir-tech.ru/api/v1/mobile/parent/end-points"
# Тестовые и демо-серверы ИРТех в каталоге — не регионы.
SERVICE_HOSTS = ("ir-tech.ru",)
USER_AGENT = "netschoolbot-status (+https://ns.ikrya.ru/status)"
PROBE_CONCURRENCY = 10
# Ответ дольше — «медленно»: logindata маленький, обычно это доли секунды.
SLOW_MS = 5000
KEEP_PROBES_S = 31 * 86400
KEEP_REPORTS_S = 90 * 86400
PRUNE_EVERY_S = 6 * 3600
SUMMARY_TTL_S = 60
ACCOUNT_WINDOW_S = 30 * 60
# Для приложения: проверка из круга моложе этого — свежая; своя — помнится.
LIVE_FRESH_S = 3 * PROBE_EVERY_S
LIVE_CACHE_S = 120

# Период графика: (длина, размер столбика).
RANGES = {
    "24h": (86400, 1800),
    "7d": (7 * 86400, 2 * 3600),
    "30d": (30 * 86400, 8 * 3600),
}

REPORT_KINDS = {
    "down": "Не открывается",
    "login": "Не получается войти",
    "slow": "Очень медленно",
    "data": "Ошибки или пустые данные",
    "other": "Другое",
}
COMMENT_MAX = 500
# Жалобы с одного адреса: на один сервер — раз в полчаса, всего — пять в час.
REPORT_SAME_HOST_S = 30 * 60
REPORTS_PER_HOUR = 5
# Столько жалоб на сервер за полчаса — повод написать админу (раз в час).
REPORT_ALERT_COUNT = 3
REPORT_ALERT_WINDOW_S = 30 * 60
REPORT_ALERT_EVERY_S = 3600

Notify = Callable[[str], Awaitable[None]]


def host_of(url: str) -> str:
    return urlparse(url).hostname or url


@dataclass(frozen=True, slots=True)
class Target:
    host: str
    url: str
    region: str
    # Адрес мобильного API; '' — региона нет в приложении NetSchool.
    mobile_url: str = ""
    # Пометка ИРТех в каталоге: регион отключён в приложении, и почему.
    unavailable: bool = False
    notice: str = ""


@dataclass(frozen=True, slots=True)
class Probe:
    ok: bool
    latency_ms: int
    version: str = ""
    error: str = ""
    # None — мобильное API не проверяли (региона нет в приложении).
    mobile: bool | None = None


Prober = Callable[[Target], Awaitable[Probe]]
Catalog = Callable[[], Awaitable[list[dict]]]


def merge_targets(catalog: list[dict]) -> list[Target]:
    """Серверы из каталога приложения и справочника регионов, по хосту.

    Название и веб-адрес — из справочника (там полные названия и адреса
    веб-версии, у Сахалина — с портом), мобильный адрес и пометки — из
    каталога. Волгограда в каталоге нет — он остаётся из справочника.
    """
    from netschoolpy import REGIONS

    targets: dict[str, Target] = {}
    for region, url in REGIONS.items():
        host = host_of(url)
        targets[host] = Target(host, url.rstrip("/"), region)
    for item in catalog:
        api = (item.get("url") or "").rstrip("/")
        host = host_of(api)
        if not api or not host or host.endswith(SERVICE_HOSTS):
            continue
        known = targets.get(host)
        site = api.removesuffix("/api/mobile")
        targets[host] = Target(
            host,
            known.url if known else site,
            known.region if known else (item.get("name") or host),
            mobile_url=api,
            unavailable=bool(item.get("unavailable")),
            notice=" ".join((item.get("comment") or "").split())[:500],
        )
    return sorted(targets.values(), key=lambda t: t.region)


class HttpProber:
    """Запросы бота к серверам — своим клиентом, не тем, что ходит за оценками.

    Маршрут тот же, что у входа (прокси по хосту, сертификаты Минцифры):
    иначе страница показывала бы «недоступен» там, где бот работает.
    """

    def __init__(self, proxy_for: Callable[[str], str | None] = lambda _url: None) -> None:
        self._proxy_for = proxy_for

    def _client(self, url: str, timeout: float) -> httpx.AsyncClient:
        from ..netschool.patches.http import school_ssl_context

        return httpx.AsyncClient(
            proxy=self._proxy_for(url), verify=school_ssl_context(), timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            # В справочнике бывают адреса http://, а сайт уводит на https.
            follow_redirects=True, max_redirects=3,
        )

    async def __call__(self, target: Target) -> Probe:
        started = time.monotonic()
        try:
            async with self._client(target.url, PROBE_TIMEOUT_S) as client:
                response = await client.get(f"{target.url}/webapi/logindata")
                maintenance = _maintenance_page(response)
                # API иногда отдаёт только 503, а объявление размещено на сайте.
                if response.status_code == 503 and not maintenance:
                    try:
                        maintenance = _maintenance_page(await client.get(f"{target.url}/", timeout=5))
                    except httpx.HTTPError:
                        pass
            ms = int((time.monotonic() - started) * 1000)
            if maintenance:
                web = Probe(False, ms, error="maintenance")
            elif response.status_code != 200:
                web = Probe(False, ms, error=f"http {response.status_code}")
            else:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict) and payload.get("version"):
                    web = Probe(True, ms, version=str(payload["version"]).strip()[:40])
                else:
                    web = Probe(False, ms, error="invalid_response")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — недоступность и есть результат
            web = Probe(False, int((time.monotonic() - started) * 1000), error=error_name(exc))
        if not target.mobile_url:
            return web
        return Probe(web.ok, web.latency_ms, web.version, web.error, await self._mobile(target))

    async def _mobile(self, target: Target) -> bool:
        try:
            async with self._client(target.mobile_url, MOBILE_TIMEOUT_S) as client:
                response = await client.get(f"{target.mobile_url}/users")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            return False
        # Без токена живой сервер отвечает 401: запрос дошёл до API.
        return response.status_code in (200, 401, 403)


def _maintenance_page(response: httpx.Response) -> bool:
    text = html.unescape(re.sub(r"<[^>]*>", " ", response.text[:100_000])).lower()
    text = " ".join(text.split())
    return any(marker in text for marker in (
        "техническое обслуживание", "технические работы", "регламентные работы",
        "регламентных работ", "технических работ", "scheduled maintenance", "under maintenance",
    ))


def catalog_fetcher(proxy_for: Callable[[str], str | None] = lambda _url: None) -> Catalog:
    """Каталог регионов мобильного приложения — открытый, без входа."""
    async def fetch() -> list[dict]:
        from netschoolpy.mobile import APP_USER_AGENT, APP_VERSION

        async with httpx.AsyncClient(proxy=proxy_for(CATALOG_URL), timeout=PROBE_TIMEOUT_S) as client:
            response = await client.get(
                CATALOG_URL, params={"lng": "ru", "appVersion": APP_VERSION},
                headers={"User-Agent": APP_USER_AGENT},
            )
        response.raise_for_status()
        return list(response.json()["items"])

    return fetch


def error_name(exc: BaseException) -> str:
    """Короткая причина неудачи для страницы: без текста исключения, в нём
    бывают адреса прокси."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return f"http {status}"
    name = type(exc).__name__
    lowered = name.lower()
    if "ssl" in lowered or "certificate" in lowered:
        return "ssl"
    if "connect" in lowered or "network" in lowered or "proxy" in lowered:
        return "connect"
    if "timeout" in lowered:
        return "timeout"
    if name == "ServerUnavailable":
        return "unavailable"
    return name[:40]


class StatusService:
    def __init__(self, repo: SgoStatusRepository, *, prober: Prober | None = None,
                 catalog: Catalog | None = None, notify: Notify | None = None, secret: str = "",
                 clock: Callable[[], float] = time.time) -> None:
        self._repo = repo
        self._prober = prober
        self._catalog = catalog
        self._notify = notify
        self._secret = secret.encode()
        self._clock = clock
        self._targets = merge_targets([])
        self._catalog_at: float | None = None
        self._cache: dict[str, tuple[float, dict]] = {}
        self._alerted: dict[str, float] = {}
        self._live: dict[str, tuple[float, dict]] = {}
        self._listeners: set[asyncio.Queue] = set()
        self._revision = 0
        # Сообщения в группы Telegram о простоях и обновлениях (status/groups.py).
        self.groups: GroupBroadcast | None = None

    # ───────────────────────────── серверы ─────────────────────────────

    def targets(self) -> list[Target]:
        return self._targets

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=1)
        self._listeners.add(queue)
        queue.put_nowait(self._revision)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._listeners.discard(queue)

    def close_listeners(self) -> None:
        for queue in self._listeners:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)
        self._listeners.clear()

    def _changed(self, *, invalidate: bool = True) -> None:
        if invalidate:
            self._cache.clear()
        self._revision += 1
        for queue in self._listeners:
            if not queue.full():
                queue.put_nowait(self._revision)

    def region(self, host: str) -> str:
        return next((t.region for t in self._targets if t.host == host), "")

    async def record_account_check(self, url: str, ok: bool) -> None:
        """Обезличенный итог проверки; ошибки входа отсекает OutageMonitor."""
        await self._repo.add_account_check(host_of(url), self._clock(), ok)
        self._changed(invalidate=False)

    async def refresh_catalog(self) -> None:
        """Обновить список серверов из каталога приложения, если пора.
        Не вышло — работаем с прежним списком."""
        now = self._clock()
        if self._catalog is None or (self._catalog_at is not None and now - self._catalog_at < CATALOG_EVERY_S):
            return
        self._catalog_at = now
        try:
            self._targets = merge_targets(await self._catalog())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — есть справочник регионов
            logger.info("Каталог регионов NetSchool не получен: %s", type(exc).__name__)
            # Повторить через полчаса, а не через 6 часов.
            self._catalog_at = now - CATALOG_EVERY_S + 1800

    # ───────────────────────────── проверки ─────────────────────────────

    async def probe_all(self) -> int:
        """Один круг проверок. Возвращает, сколько серверов ответили."""
        if self._prober is None:
            return 0
        await self.refresh_catalog()
        targets = self._targets
        at = self._clock()
        semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)

        async def probe(target: Target) -> Probe:
            async with semaphore:
                try:
                    return await asyncio.wait_for(self._prober(target), PROBE_TIMEOUT_S + MOBILE_TIMEOUT_S + 5)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — недоступность и есть результат
                    return Probe(False, 0, error=error_name(exc))

        results = await asyncio.gather(*(probe(target) for target in targets))
        await self._repo.add_probes([
            (target.host, at, result.ok, result.latency_ms, result.error, result.mobile)
            for target, result in zip(targets, results)
        ])
        changed: dict[str, tuple[str, str]] = {}
        for target, result in zip(targets, results):
            if result.version:
                previous = await self._repo.save_version(target.host, result.version, at)
                if previous and previous != result.version:
                    changed[target.host] = (previous, result.version)
                    logger.info("«Сетевой город» %s: версия %s → %s", target.host, previous, result.version,
                                extra={"public": True})
        self._changed()
        if self.groups is not None:
            try:
                await self.groups.observe(list(zip(targets, results)), changed, at)
            except Exception as exc:  # noqa: BLE001 — рассылка не должна ломать проверки
                logger.warning("Рассылка о «Сетевом городе» в группы не удалась: %s", exc)
        good = sum(1 for result in results if result.ok)
        logger.debug("Статус «Сетевого города»: ответили %s из %s", good, len(results))
        return good

    async def run(self) -> None:
        """Опрашивать серверы ровным шагом, пока задачу не отменят."""
        pruned = 0.0
        while True:
            started = self._clock()
            try:
                await self.probe_all()
                if started - pruned >= PRUNE_EVERY_S:
                    await self._repo.prune(started - KEEP_PROBES_S, started - KEEP_REPORTS_S)
                    pruned = started
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — страница статуса не должна ронять бота
                logger.warning("Круг проверок статуса не удался: %s", exc)
            await asyncio.sleep(max(10.0, PROBE_EVERY_S - (self._clock() - started)))

    # ───────────────────────────── жалобы ─────────────────────────────

    def client_key(self, ip: str) -> str:
        return hmac.new(self._secret, ip.encode(), hashlib.sha256).hexdigest()[:24]

    async def report(self, host: str, kind: str, comment: str, ip: str) -> str | None:
        """Принять жалобу. Возвращает текст отказа или None."""
        names = {target.host: target for target in self._targets}
        if host not in names:
            return "Неизвестный сервер"
        if kind not in REPORT_KINDS:
            return "Выберите, что случилось"
        comment = " ".join((comment or "").split())[:COMMENT_MAX]
        now = self._clock()
        client = self.client_key(ip)
        if await self._repo.reports_from(client, now - REPORT_SAME_HOST_S, host):
            return "Жалоба на этот сервер уже принята — спасибо! Повторить можно через полчаса."
        if await self._repo.reports_from(client, now - 3600) >= REPORTS_PER_HOUR:
            return "Слишком много жалоб с вашего адреса — попробуйте через час."
        await self._repo.add_report(host, kind, comment, client, now)
        self._changed()
        logger.info("Жалоба на «Сетевой город» %s: %s", host, kind, extra={"public": True})
        await self._maybe_alert(names[host], kind, comment, now)
        return None

    async def _maybe_alert(self, target: Target, kind: str, comment: str, now: float) -> None:
        if self._notify is None or now - self._alerted.get(target.host, 0) < REPORT_ALERT_EVERY_S:
            return
        recent = [r for r in await self._repo.reports(now - REPORT_ALERT_WINDOW_S) if r["host"] == target.host]
        if len(recent) < REPORT_ALERT_COUNT:
            return
        self._alerted[target.host] = now
        kinds = ", ".join(sorted({REPORT_KINDS.get(r["kind"], r["kind"]) for r in recent}))
        text = (f"📣 Жалобы на «Сетевой город» {target.host}"
                + (f" ({target.region})" if target.region else "")
                + f": {len(recent)} за {REPORT_ALERT_WINDOW_S // 60} мин — {kinds}")
        if comment:
            # Бот шлёт с parse_mode=HTML, а текст жалобы — чужой.
            text += f"\nПоследняя: {html.escape(comment[:200])}"
        try:
            await self._notify(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось сообщить админу о жалобах: %s", exc)

    # ───────────────────────────── один сервер ─────────────────────────────

    async def live_state(self, url: str) -> dict:
        """Что с сервером школы прямо сейчас — для приложения, пока у
        человека долго грузится дневник. Свежая проверка из круга, иначе своя
        (результат помним две минуты)."""
        host = host_of(url)
        now = self._clock()
        probes = await self._repo.host_probes(host, now - LIVE_FRESH_S)
        accounts = (await self._repo.account_checks(now - ACCOUNT_WINDOW_S)).get(host)
        if not probes:
            cached = self._live.get(host)
            if cached and now - cached[0] < LIVE_CACHE_S:
                return {**cached[1], "account_checks": accounts}
            if self._prober is None:
                return {"host": host, "state": "unknown"}
            parsed = urlparse(url)
            target = Target(host, f"{parsed.scheme}://{parsed.netloc}", "")
            try:
                result = await asyncio.wait_for(self._prober(target), PROBE_TIMEOUT_S + 5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                result = Probe(False, 0, error=error_name(exc))
            probes = [{"host": host, "at": now, "ok": result.ok, "latency_ms": result.latency_ms,
                       "error": result.error, "mobile": result.mobile}]
            data = self._host_state(host, probes, await self._repo.host_ever_ok(host) or result.ok,
                                    result.version)
            self._live[host] = (now, data)
            return {**data, "account_checks": accounts}
        versions = await self._repo.versions()
        version = versions.get(host, {}).get("version", "")
        history = await self._repo.host_check_history(host)
        data = self._host_state(host, probes, history["last_ok_at"] is not None, version)
        data["last_ok_at"] = history["last_ok_at"]
        if data["state"] == "down":
            data["down_since"] = history["down_since"]
        return {**data, "account_checks": accounts}

    def _host_state(self, host: str, probes: list[dict], ever_ok: bool, version: str) -> dict:
        region = self.region(host)
        state = _state(probes)
        if not ever_ok and state != "up":
            state = "blocked"
        failing = [p["at"] for p in probes if not p["ok"]]
        last_ok = next((p["at"] for p in probes if p["ok"]), None)
        return {
            "host": host,
            "region": region,
            "state": state,
            # Последняя проверка удалась: сервер отвечает, даже если недавно сбоил.
            "ok": bool(probes[0]["ok"]),
            "checked_at": probes[0]["at"],
            "error": probes[0]["error"] if not probes[0]["ok"] else "",
            "last_ok_at": last_ok,
            # Сколько не отвечает — по проверкам за последние часы.
            "down_since": min((at for at in failing if last_ok is None or at > last_ok), default=None)
            if state == "down" else None,
            "version": version,
        }

    # ───────────────────────────── сводка ─────────────────────────────

    async def summary(self, range_key: str = "24h") -> dict:
        if range_key not in RANGES:
            range_key = "24h"
        now = self._clock()
        cached = self._cache.get(range_key)
        if cached and now - cached[0] < SUMMARY_TTL_S:
            # Счётчики аккаунтов меняются часто; ради них не пересчитываем
            # месячную историю всех регионов после каждой фоновой проверки.
            accounts = await self._repo.account_checks(now - ACCOUNT_WINDOW_S)
            return {**cached[1], "generated_at": now, "hosts": [
                _account_view(host, accounts.get(host["host"])) for host in cached[1]["hosts"]
            ]}
        data = await self._build(range_key, now)
        self._cache[range_key] = (now, data)
        return data

    async def _build(self, range_key: str, now: float) -> dict:
        length, size = RANGES[range_key]
        count = int(length // size)
        since = (math.floor(now / size) - count + 1) * size
        targets = self._targets
        # Серверы, которые ни разу не ответили боту: с нашего сервера они не
        # открываются (а людям — может быть, вполне). «Недоступен» о них —
        # неправда, сбоев и полос у них нет — только «нет данных».
        reachable = await self._repo.reachable_hosts()
        regions = {t.host: t.region for t in targets if t.host in reachable}

        recent: dict[str, list[dict]] = {}
        for row in await self._repo.recent_probes(now - 2 * 3600):
            recent.setdefault(row["host"], []).append(row)
        day = await self._repo.totals(now - 86400)
        period = day if range_key == "24h" else await self._repo.totals(since)
        bars: dict[str, list] = {t.host: [None] * count for t in targets}
        down = [0] * count
        checked = [0] * count
        for row in await self._repo.buckets(since, size):
            index = row["bucket"]
            if row["host"] not in regions or not 0 <= index < count or not row["total"]:
                continue
            share = (row["good"] or 0) / row["total"]
            bars[row["host"]][index] = round(share, 3)
            checked[index] += 1
            if share < 0.5:
                down[index] += 1

        reports = await self._repo.reports(min(since, now - 86400))
        report_bars: dict[str, list[int]] = {t.host: [0] * count for t in targets}
        reports_total = [0] * count
        reports_day: dict[str, int] = {}
        for report in reports:
            if report["created_at"] >= now - 86400:
                reports_day[report["host"]] = reports_day.get(report["host"], 0) + 1
            index = int((report["created_at"] - since) // size)
            if 0 <= index < count and report["host"] in report_bars:
                report_bars[report["host"]][index] += 1
                reports_total[index] += 1

        incidents = self._incidents(await self._repo.failures(now - KEEP_PROBES_S), regions)
        regions = {t.host: t.region for t in targets}
        ongoing = {i["host"]: i["start"] for i in incidents if i["ongoing"]}
        versions = await self._repo.versions()

        last_successes = await self._repo.latest_successes()
        accounts = await self._repo.account_checks(now - ACCOUNT_WINDOW_S)
        hosts = []
        for target in targets:
            probes = recent.get(target.host, [])
            blocked = target.host not in reachable
            state = "blocked" if blocked and probes else _state(probes)
            last = probes[0] if probes else None
            last_ok = last_successes.get(target.host)
            mobile = next((p["mobile"] for p in probes if p["mobile"] is not None), None)
            version = versions.get(target.host)
            hosts.append(_account_view({
                "host": target.host,
                "url": target.url,
                "region": target.region,
                "state": state,
                "web_state": state,
                "checked_at": last["at"] if last else None,
                "latency_ms": last["latency_ms"] if last and last["ok"] else None,
                "error": last["error"] if last and not last["ok"] else "",
                "last_ok_at": last_ok,
                "down_since": ongoing.get(target.host),
                "version": version["version"] if version else "",
                "version_changed_at": version["changed_at"] if version else None,
                "in_app": bool(target.mobile_url),
                "app_disabled": target.unavailable,
                "notice": target.notice,
                "mobile": None if mobile is None else bool(mobile),
                "mobile_uptime": _share(period.get(target.host), "mobile_total", "mobile_good"),
                "uptime": None if blocked else _share(period.get(target.host)),
                "uptime_24h": None if blocked else _share(day.get(target.host)),
                "avg_latency_ms": _latency(day.get(target.host)),
                "reports_24h": reports_day.get(target.host, 0),
                "bars": bars[target.host],
                "report_bars": report_bars[target.host],
            }, accounts.get(target.host)))

        history = await self._repo.version_history(now - 365 * 86400)
        return {
            "generated_at": now,
            "range": range_key,
            "since": since,
            "bucket_s": size,
            "probe_every_s": PROBE_EVERY_S,
            "slow_ms": SLOW_MS,
            "hosts": hosts,
            "overview": {"down": down, "checked": checked, "reports": reports_total},
            "incidents": [i for i in incidents if (i["end"] or now) >= since][:60],
            "versions": [
                {**row, "region": regions.get(row["host"], "")}
                for row in history if row["host"] in {t.host for t in targets}
            ],
            "reports": [
                {"host": r["host"], "region": regions.get(r["host"], ""), "kind": r["kind"],
                 "created_at": r["created_at"]}
                for r in reports[:30] if r["host"] in regions
            ],
            "kinds": REPORT_KINDS,
        }

    @staticmethod
    def _incidents(failures: list[dict], regions: dict[str, str]) -> list[dict]:
        """Сбои — подряд идущие неудачные проверки (одна — не сбой, а случайность)."""
        runs: list[dict] = []
        current: dict | None = None
        for row in failures:
            if row["host"] not in regions:
                continue
            if current and current["host"] == row["host"] and row["previous_ok"] == 0:
                current["end"] = row["at"]
                current["recovered_at"] = row["next_at"] if row["next_ok"] else None
                current["probes"] += 1
                current["errors"][row["error"]] = current["errors"].get(row["error"], 0) + 1
                continue
            current = {"host": row["host"], "start": row["at"], "end": row["at"], "probes": 1,
                       "recovered_at": row["next_at"] if row["next_ok"] else None,
                       "errors": {row["error"]: 1}}
            runs.append(current)
        incidents = []
        for run in runs:
            if run["probes"] < 2:
                continue
            ongoing = run["recovered_at"] is None
            incidents.append({
                "host": run["host"],
                "region": regions.get(run["host"], ""),
                "start": run["start"],
                "end": None if ongoing else (run["recovered_at"] or run["end"]),
                "ongoing": ongoing,
                "probes": run["probes"],
                "error": max(run["errors"], key=run["errors"].get),
            })
        incidents.sort(key=lambda i: i["start"], reverse=True)
        return incidents


def _account_view(host: dict, account: dict | None) -> dict:
    state = host["web_state"]
    issues = bool(account and account["total"] >= 3 and account["good"] / account["total"] < 0.5)
    if issues and state in ("up", "slow", "blocked", "unknown"):
        state = "unstable"
    return {**host, "state": state, "account_checks": account, "diary_issues": issues}


def _state(probes: list[dict]) -> str:
    """Состояние по последним проверкам (новые первыми)."""
    if not probes:
        return "unknown"
    last = probes[0]
    if not last["ok"]:
        if len(probes) == 1 or not probes[1]["ok"]:
            return "down"
        return "unstable"
    if any(not p["ok"] for p in probes[:6]):
        return "unstable"
    if last["latency_ms"] >= SLOW_MS:
        return "slow"
    return "up"


def _share(row: dict | None, total: str = "total", good: str = "good") -> float | None:
    if not row or not row[total]:
        return None
    return round(100 * (row[good] or 0) / row[total], 2)


def _latency(row: dict | None) -> int | None:
    if not row or row["latency"] is None:
        return None
    return int(row["latency"])
