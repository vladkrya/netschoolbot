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
