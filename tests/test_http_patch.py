import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
import netschoolpy.http as ns_http
import netschoolpy.mobile as ns_mobile
from netschoolpy.exceptions import ServerUnavailable
from app.netschool.patches import http as patch


@pytest.fixture
def direct_mode(monkeypatch):
    monkeypatch.setattr(patch, '_applied', False)
    monkeypatch.setattr(ns_http, '_DEFAULT_TIMEOUT', ns_http._DEFAULT_TIMEOUT)
    monkeypatch.setattr(ns_http, '_tor_hosts', ns_http._tor_hosts)
    monkeypatch.setattr(ns_http.HttpSession, '_send', ns_http.HttpSession._send)
    monkeypatch.setattr(ns_http.HttpSession, '_create_async_client',
                        ns_http.HttpSession.__dict__['_create_async_client'])
    monkeypatch.setattr(ns_mobile, '_new_client', ns_mobile._new_client)
    patch.apply(timeout=1, blocked_host_ttl=600, fallback_proxy='off')


async def make_session(handler):
    session = ns_http.HttpSession('https://school.example')
    await session._client.aclose()
    session._client = httpx.AsyncClient(base_url='https://school.example/webapi/',
        transport=httpx.MockTransport(handler), event_hooks={'response': [session._check_status]})
    return session


async def test_off_does_not_retry_connect_error_via_tor(direct_mode):
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError('unreachable', request=request)
    session = await make_session(handler)
    try:
        with pytest.raises(ServerUnavailable, match='прямом подключении'):
            await session.get('logindata')
        assert calls == 1
        assert not ns_http._tor_hosts
        assert not hasattr(session, '_tor_client')
    finally:
        await session.close()


async def test_off_bounds_timeout_without_proxy_retry(direct_mode):
    async def handler(request):
        await asyncio.Event().wait()
    session = await make_session(handler)
    try:
        with pytest.raises(ServerUnavailable, match='прямом подключении'):
            await session.get('logindata', timeout=.01)
        assert not ns_http._tor_hosts
        assert not hasattr(session, '_tor_client')
    finally:
        await session.close()


async def test_off_preserves_server_error_retry(direct_mode):
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200, json={'ok': True})
    session = await make_session(handler)
    try:
        response = await session.get('logindata')
        assert response.status_code == 200
        assert calls == 2
    finally:
        await session.close()


async def test_off_ignores_environment_proxy_for_web_and_mobile(direct_mode, monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:59999')
    monkeypatch.setenv('ALL_PROXY', 'socks5://127.0.0.1:59999')
    session = ns_http.HttpSession('https://school.example')
    mobile = ns_mobile._new_client()
    try:
        assert not session._client._trust_env
        assert not mobile._trust_env
        assert session._external_proxy is None
    finally:
        await session.close()
        await mobile.aclose()


class _Failing(httpx.AsyncBaseTransport):
    def __init__(self):
        self.calls = 0

    async def handle_async_request(self, request):
        self.calls += 1
        raise httpx.ProxyError('proxy refused')


async def test_proxy_failure_falls_back_to_direct(monkeypatch):
    monkeypatch.setattr(patch.ProxyFallbackTransport, '_down', {})
    transport = patch.ProxyFallbackTransport('socks5://127.0.0.1:9')
    failing = _Failing()
    transport._via_proxy = failing
    transport._direct = httpx.MockTransport(lambda request: httpx.Response(200, json={'ok': True}))
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.get('https://school.example/webapi/x')).status_code == 200
        # Пока прокси помечен упавшим, к хосту идём сразу напрямую.
        assert (await client.get('https://school.example/webapi/y')).status_code == 200
    assert failing.calls == 1


async def test_proxy_answer_is_not_replaced(monkeypatch):
    monkeypatch.setattr(patch.ProxyFallbackTransport, '_down', {})
    transport = patch.ProxyFallbackTransport('socks5://127.0.0.1:9')
    transport._via_proxy = httpx.MockTransport(lambda request: httpx.Response(403))
    transport._direct = httpx.MockTransport(lambda request: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await client.get('https://school.example/')).status_code == 403


def test_client_with_proxy_gets_fallback_transport(direct_mode):
    session = ns_http.HttpSession('https://school.example', proxy='socks5://127.0.0.1:9')
    assert isinstance(session._client._transport, patch.ProxyFallbackTransport)
    direct = ns_http.HttpSession('https://school.example')
    assert not isinstance(direct._client._transport, patch.ProxyFallbackTransport)
