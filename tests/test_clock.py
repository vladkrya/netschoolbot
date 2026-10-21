"""Сверка часов сервера с NTP (app/web/clock.py)."""
import struct
import time

from app.web import clock


def test_ntp_time_decodes_timestamp():
    raw = bytes(32) + struct.pack("!II", clock.NTP_EPOCH + 100, 2**31) + bytes(8)
    assert clock._ntp_time(raw, 32) == 100.5


def test_query_offset(monkeypatch):
    # Сервер времени впереди часов сервера на 2 с.
    class Socket:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def settimeout(self, value):
            pass

        def sendto(self, data, address):
            assert data[0] == 0x23 and address[1] == 123

        def recvfrom(self, size):
            stamp = time.time() + 2 + clock.NTP_EPOCH
            packed = struct.pack("!II", int(stamp), int(stamp % 1 * 2**32))
            return bytes([0x24, 2]) + bytes(30) + packed + packed, None

    monkeypatch.setattr(clock.socket, "socket", Socket)
    assert abs(clock.query_offset("ntp") - 2) < 0.05


def test_refresh_falls_through_servers(monkeypatch):
    def query(host, timeout=0):
        if host != clock.NTP_SERVERS[-1]:
            raise OSError("timeout")
        return 1.25

    monkeypatch.setattr(clock, "query_offset", query)
    assert clock._refresh() == 1.25
