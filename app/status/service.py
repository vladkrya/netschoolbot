"""Статус серверов «Сетевого города» по всем регионам.

Проверки оценок знают только о серверах, где есть наши пользователи, и только
в своём ритме. Здесь все серверы из справочника регионов (и серверы наших
пользователей вне его) опрашиваются раз в несколько минут одним и тем же
запросом — `/webapi/logindata` без входа: он же отдаёт версию. Из проверок
складываются состояние, доступность за период и список сбоев; люди могут
пожаловаться на сбой, который проверка не видит (вход, пустые данные).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from ..db.repositories import SgoStatusRepository, SgoVersionRepository
    from ..notifications.sgo_version import VersionTracker

logger = logging.getLogger("netschoolbot.status")

PROBE_EVERY_S = 5 * 60
PROBE_TIMEOUT_S = 20
PROBE_CONCURRENCY = 6
# Ответ дольше — «медленно»: logindata маленький, обычно это доли секунды.
SLOW_MS = 5000
# Неудачи с таким разрывом — один сбой: между ними удачной проверки не было.
RUN_GAP_S = PROBE_EVERY_S * 1.5
KEEP_PROBES_S = 31 * 86400
KEEP_REPORTS_S = 90 * 86400
PRUNE_EVERY_S = 6 * 3600
SUMMARY_TTL_S = 60

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

Fetch = Callable[[str], Awaitable[str]]
Notify = Callable[[str], Awaitable[None]]


def host_of(url: str) -> str:
    return urlparse(url).hostname or url


@dataclass(frozen=True, slots=True)
class Target:
    host: str
    url: str
    region: str


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
    def __init__(self, repo: SgoStatusRepository, version_repo: SgoVersionRepository, *,
                 fetch: Fetch | None = None, versions: VersionTracker | None = None,
                 notify: Notify | None = None, secret: str = "",
                 clock: Callable[[], float] = time.time) -> None:
        self._repo = repo
        self._version_repo = version_repo
        self._fetch = fetch
        self._versions = versions
        self._notify = notify
        self._secret = secret.encode()
        self._clock = clock
        self._cache: dict[str, tuple[float, dict]] = {}
        self._alerted: dict[str, float] = {}

    # ───────────────────────────── серверы ─────────────────────────────

    async def targets(self) -> tuple[list[Target], set[str]]:
        """Все серверы и хосты, на которых есть наши пользователи."""
        from netschoolpy import REGIONS

        targets: dict[str, Target] = {}
        for region, url in sorted(REGIONS.items()):
            targets.setdefault(host_of(url), Target(host_of(url), url.rstrip("/"), region))
        ours: set[str] = set()
        for url in await self._repo.school_urls():
            host = host_of(url)
            ours.add(host)
            if host not in targets:
                parsed = urlparse(url)
                targets[host] = Target(host, f"{parsed.scheme}://{parsed.netloc}", "")
        return sorted(targets.values(), key=lambda t: (not t.region, t.region or t.host)), ours

    # ───────────────────────────── проверки ─────────────────────────────

    async def probe_all(self) -> int:
        """Один круг проверок. Возвращает, сколько серверов ответили."""
        if self._fetch is None:
            return 0
        targets, ours = await self.targets()
        at = self._clock()
        semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)

        async def probe(target: Target) -> tuple[tuple, str]:
            async with semaphore:
                started = time.monotonic()
                try:
                    version = await asyncio.wait_for(self._fetch(target.url), PROBE_TIMEOUT_S)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — недоступность и есть результат
                    ms = int((time.monotonic() - started) * 1000)
                    return (target.host, at, False, ms, error_name(exc)), ""
                ms = int((time.monotonic() - started) * 1000)
                return (target.host, at, True, ms, ""), (version or "").strip()

        results = await asyncio.gather(*(probe(target) for target in targets))
        await self._repo.add_probes([row for row, _ in results])
        if self._versions is not None:
            for row, version in results:
                if version:
                    host = row[0]
                    await self._versions.observe(host, version, self._notify if host in ours else None)
        self._cache.clear()
        good = sum(1 for row, _ in results if row[2])
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
        targets, _ = await self.targets()
        names = {target.host: target for target in targets}
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
        self._cache.clear()
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

    # ───────────────────────────── сводка ─────────────────────────────

    async def summary(self, range_key: str = "24h") -> dict:
        if range_key not in RANGES:
            range_key = "24h"
        now = self._clock()
        cached = self._cache.get(range_key)
        if cached and now - cached[0] < SUMMARY_TTL_S:
            return cached[1]
        data = await self._build(range_key, now)
        self._cache[range_key] = (now, data)
        return data

    async def _build(self, range_key: str, now: float) -> dict:
        length, size = RANGES[range_key]
        count = int(length // size)
        since = (math.floor(now / size) - count + 1) * size
        targets, _ = await self.targets()
        regions = {t.host: t.region for t in targets}

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
            if row["host"] not in bars or not 0 <= index < count or not row["total"]:
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

        incidents = self._incidents(await self._repo.failures(now - KEEP_PROBES_S), recent, regions)
        ongoing = {i["host"]: i["start"] for i in incidents if i["ongoing"]}
        versions = {row["host"]: row for row in await self._version_repo.all()}

        hosts = []
        for target in targets:
            probes = recent.get(target.host, [])
            state = _state(probes)
            last = probes[0] if probes else None
            last_ok = next((p["at"] for p in probes if p["ok"]), None)
            version = versions.get(target.host)
            hosts.append({
                "host": target.host,
                "url": target.url,
                "region": target.region,
                "state": state,
                "checked_at": last["at"] if last else None,
                "latency_ms": last["latency_ms"] if last and last["ok"] else None,
                "error": last["error"] if last and not last["ok"] else "",
                "last_ok_at": last_ok,
                "down_since": ongoing.get(target.host),
                "version": version["version"] if version else "",
                "version_changed_at": version["changed_at"] if version else None,
                "uptime": _share(period.get(target.host)),
                "uptime_24h": _share(day.get(target.host)),
                "avg_latency_ms": _latency(day.get(target.host)),
                "reports_24h": reports_day.get(target.host, 0),
                "bars": bars[target.host],
                "report_bars": report_bars[target.host],
            })

        history = await self._version_repo.history(now - 365 * 86400)
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
                for row in history if row["host"] in regions
            ],
            "reports": [
                {"host": r["host"], "region": regions.get(r["host"], ""), "kind": r["kind"],
                 "created_at": r["created_at"]}
                for r in reports[:30] if r["host"] in regions
            ],
            "kinds": REPORT_KINDS,
        }

    @staticmethod
    def _incidents(failures: list[dict], recent: dict[str, list[dict]], regions: dict[str, str]) -> list[dict]:
        """Сбои — подряд идущие неудачные проверки (одна — не сбой, а случайность)."""
        runs: list[dict] = []
        current: dict | None = None
        for row in failures:
            if row["host"] not in regions:
                continue
            if current and current["host"] == row["host"] and row["at"] - current["end"] <= RUN_GAP_S:
                current["end"] = row["at"]
                current["probes"] += 1
                current["errors"][row["error"]] = current["errors"].get(row["error"], 0) + 1
                continue
            current = {"host": row["host"], "start": row["at"], "end": row["at"], "probes": 1,
                       "errors": {row["error"]: 1}}
            runs.append(current)
        incidents = []
        for run in runs:
            if run["probes"] < 2:
                continue
            probes = recent.get(run["host"], [])
            ongoing = bool(probes) and not probes[0]["ok"] and probes[0]["at"] == run["end"]
            incidents.append({
                "host": run["host"],
                "region": regions.get(run["host"], ""),
                "start": run["start"],
                "end": None if ongoing else run["end"],
                "ongoing": ongoing,
                "probes": run["probes"],
                "error": max(run["errors"], key=run["errors"].get),
            })
        incidents.sort(key=lambda i: i["start"], reverse=True)
        return incidents


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


def _share(row: dict | None) -> float | None:
    if not row or not row["total"]:
        return None
    return round(100 * (row["good"] or 0) / row["total"], 2)


def _latency(row: dict | None) -> int | None:
    if not row or row["latency"] is None:
        return None
    return int(row["latency"])
