"""Точное время для таймера урока.

Часы сервера сами по себе спешили на секунды, и таймер, сверявшийся с ними,
доходил до «00» раньше точных часов на телефоне. Поэтому сервер раз в полчаса
сверяется с NTP и отдаёт время с поправкой. Пока сверки нет (NTP недоступен,
ещё не успели) — отдаёт свои часы и говорит, что они не сверены.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time

log = logging.getLogger(__name__)

NTP_SERVERS = ("pool.ntp.org", "time.google.com", "time.cloudflare.com")
NTP_EPOCH = 2208988800  # 1900-01-01 → 1970-01-01
REFRESH_S = 30 * 60
# После неудачи пробуем снова раньше, но не на каждый запрос.
RETRY_S = 5 * 60
TIMEOUT_S = 2.0

_offset: float | None = None
_checked_at = 0.0
_task: asyncio.Task | None = None


def _ntp_time(raw: bytes, at: int) -> float:
    seconds, fraction = struct.unpack("!II", raw[at:at + 8])
    return seconds - NTP_EPOCH + fraction / 2**32


def query_offset(host: str, timeout: float = TIMEOUT_S) -> float:
    """Насколько точное время впереди часов сервера, с (SNTP, RFC 4330)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sent = time.time()
        sock.sendto(b"\x23" + 47 * b"\0", (host, 123))
        raw, _ = sock.recvfrom(48)
        received = time.time()
    if len(raw) < 48 or raw[0] & 0x07 != 4 or raw[1] == 0:
        raise ValueError("не ответ сервера времени")
    server_received, server_sent = _ntp_time(raw, 32), _ntp_time(raw, 40)
    return ((server_received - sent) + (server_sent - received)) / 2


def _refresh() -> float | None:
    for host in NTP_SERVERS:
        try:
            return query_offset(host)
        except (OSError, ValueError) as error:
            log.debug("NTP %s: %s", host, error)
    return None


async def _update() -> None:
    global _offset
    offset = await asyncio.to_thread(_refresh)
    if offset is None:
        log.warning("Не удалось сверить часы с NTP")
    else:
        _offset = offset


def now() -> tuple[float, bool]:
    """Точное время и сверено ли оно. Сверка обновляется в фоне."""
    global _checked_at, _task
    mono = time.monotonic()
    wait = REFRESH_S if _offset is not None else RETRY_S
    if (_task is None or _task.done()) and (not _checked_at or mono - _checked_at >= wait):
        _checked_at = mono
        _task = asyncio.get_running_loop().create_task(_update())
    if _offset is None:
        return time.time(), False
    return time.time() + _offset, True
