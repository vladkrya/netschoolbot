import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from netschoolpy.client import NetSchool
from netschoolpy.exceptions import ESIAError

from app.netschool.patches import esia


@pytest.mark.parametrize("payload,expired", [
    (b'data: {"redirect_url": "https://school.example/callback"}\n', False),
    (b'data: {"error": {"code": "QR_AUTHORIZATION_SESSION_EXPIRED"}}\n', True),
])
async def test_qr_poll_reads_scan_and_closes_connection(monkeypatch, payload, expired):
    monkeypatch.setattr(NetSchool, "_poll_esia_qr_sse",
                        NetSchool.__dict__["_poll_esia_qr_sse"])
    original = NetSchool._poll_esia_qr_sse
    esia._patch_qr_poll()
    fixed = NetSchool._poll_esia_qr_sse
    esia._patch_qr_poll()
    assert NetSchool._poll_esia_qr_sse is fixed
    if "self" in original.__code__.co_names:
        assert fixed is not original
        assert "self" not in original.__globals__
    reader = Mock(read=AsyncMock(return_value=payload))
    writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
    connect = AsyncMock(return_value=(reader, writer))
    monkeypatch.setattr(asyncio, "open_connection", connect)
    scanned = AsyncMock()
    async with httpx.AsyncClient() as client:
        if expired:
            with pytest.raises(ESIAError, match="QR сессия истекла"):
                await fixed(client, "https://esia.gosuslugi.ru/qr-delegate/qr/subscribe/test",
                            scan_callback=scanned)
        else:
            result = await fixed(client, "https://esia.gosuslugi.ru/qr-delegate/qr/subscribe/test",
                                 scan_callback=scanned)
            assert result == {"redirect_url": "https://school.example/callback"}
    scanned.assert_awaited_once()
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()
    assert connect.call_args.kwargs["ssl"] is not None
