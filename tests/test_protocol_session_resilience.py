from __future__ import annotations

import asyncio

import aiohttp
import pytest

from astrbot_plugin_qfarm.services.protocol.session import (
    GatewaySession,
    GatewaySessionConfig,
    GatewaySessionError,
)
from astrbot_plugin_qfarm.services.protocol import session as session_module


class _FakeMsg:
    def __init__(self, msg_type: aiohttp.WSMsgType, data: bytes = b"") -> None:
        self.type = msg_type
        self.data = data


class _FakeWS:
    def __init__(self, messages: list[_FakeMsg], delay_sec: float = 0.0) -> None:
        self._messages = list(messages)
        self._delay_sec = delay_sec
        self.closed = False
        self.sent_payloads: list[bytes] = []

    def __aiter__(self):
        return self

    async def __anext__(self) -> _FakeMsg:
        if not self._messages:
            raise StopAsyncIteration
        if self._delay_sec > 0:
            await asyncio.sleep(self._delay_sec)
        return self._messages.pop(0)

    async def send_bytes(self, payload: bytes) -> None:
        self.sent_payloads.append(payload)

    async def close(self) -> None:
        self.closed = True


class _FakeClientSession:
    ws_messages: list[_FakeMsg] = []
    ws_delay_sec: float = 0.0
    connect_kwargs: list[dict[str, object]] = []

    def __init__(self, *_, headers=None, **__):
        self.headers = headers or {}
        self.closed = False
        self.ws: _FakeWS | None = None

    async def ws_connect(self, _url: str, **kwargs):
        _FakeClientSession.connect_kwargs.append(dict(kwargs))
        self.ws = _FakeWS(_FakeClientSession.ws_messages, delay_sec=_FakeClientSession.ws_delay_sec)
        return self.ws

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def patch_client_session(monkeypatch: pytest.MonkeyPatch):
    _FakeClientSession.ws_messages = []
    _FakeClientSession.ws_delay_sec = 0.0
    _FakeClientSession.connect_kwargs = []
    monkeypatch.setattr(session_module.aiohttp, "ClientSession", _FakeClientSession)


def _build_session() -> GatewaySession:
    cfg = GatewaySessionConfig(
        gateway_ws_url="wss://example.invalid/ws",
        platform="qq",
        client_version="1.0.0",
        rpc_timeout_sec=5,
    )
    return GatewaySession(cfg)


@pytest.mark.asyncio
async def test_ws_connect_uses_heartbeat_and_autoping():
    session = _build_session()
    await session.start(code="abc")
    await asyncio.sleep(0)

    assert _FakeClientSession.connect_kwargs, "ws_connect should be called"
    kwargs = _FakeClientSession.connect_kwargs[-1]
    assert kwargs.get("heartbeat") == 30
    assert kwargs.get("autoping") is True
    assert kwargs.get("autoclose") is True

    await session.stop()


@pytest.mark.asyncio
async def test_recv_loop_ignores_bad_binary_payload():
    _FakeClientSession.ws_messages = [
        _FakeMsg(aiohttp.WSMsgType.BINARY, b"bad-payload"),
        _FakeMsg(aiohttp.WSMsgType.CLOSED, b""),
    ]
    session = _build_session()
    await session.start(code="abc")

    await asyncio.sleep(0.2)
    assert session.connected is False


@pytest.mark.asyncio
async def test_disconnect_fails_pending_requests():
    _FakeClientSession.ws_messages = [
        _FakeMsg(aiohttp.WSMsgType.TEXT, b"noop"),
        _FakeMsg(aiohttp.WSMsgType.CLOSED, b""),
    ]
    _FakeClientSession.ws_delay_sec = 0.05

    session = _build_session()
    await session.start(code="abc")

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    session._pending[999] = fut  # type: ignore[attr-defined]

    await asyncio.sleep(0.3)

    assert fut.done() is True
    err = fut.exception()
    assert isinstance(err, GatewaySessionError)
    assert "websocket disconnected" in str(err)

    await session.stop()
