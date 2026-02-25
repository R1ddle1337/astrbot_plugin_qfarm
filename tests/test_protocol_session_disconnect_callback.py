from __future__ import annotations

import asyncio

import aiohttp
import pytest

from astrbot_plugin_qfarm.services.protocol.session import GatewaySession, GatewaySessionConfig
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

    def __aiter__(self):
        return self

    async def __anext__(self) -> _FakeMsg:
        if self._delay_sec > 0:
            await asyncio.sleep(self._delay_sec)
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def send_bytes(self, payload: bytes) -> None:
        _ = payload

    async def close(self) -> None:
        self.closed = True


class _FakeClientSession:
    ws_messages: list[_FakeMsg] = []
    ws_delay_sec: float = 0.0

    def __init__(self, *_, headers=None, **__):
        self.headers = headers or {}
        self.closed = False
        self.ws: _FakeWS | None = None

    async def ws_connect(self, _url: str, **_kwargs):
        self.ws = _FakeWS(_FakeClientSession.ws_messages, delay_sec=_FakeClientSession.ws_delay_sec)
        return self.ws

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def patch_client_session(monkeypatch: pytest.MonkeyPatch):
    _FakeClientSession.ws_messages = []
    _FakeClientSession.ws_delay_sec = 0.0
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
async def test_disconnect_callback_called_on_recv_disconnect():
    _FakeClientSession.ws_messages = [
        _FakeMsg(aiohttp.WSMsgType.TEXT, b"noop"),
        _FakeMsg(aiohttp.WSMsgType.CLOSED, b""),
    ]
    _FakeClientSession.ws_delay_sec = 0.02

    session = _build_session()
    reasons: list[str] = []

    async def _on_disconnect(reason: str) -> None:
        reasons.append(reason)

    await session.on_disconnect(_on_disconnect)
    await session.start(code="abc")
    await asyncio.sleep(0.25)

    assert reasons, "disconnect callback should be called"
    assert "websocket" in reasons[0].lower()

    await session.stop()
    assert len(reasons) == 1


@pytest.mark.asyncio
async def test_disconnect_callback_not_called_on_manual_stop():
    _FakeClientSession.ws_messages = [_FakeMsg(aiohttp.WSMsgType.TEXT, b"noop")]
    _FakeClientSession.ws_delay_sec = 1.0

    session = _build_session()
    reasons: list[str] = []

    async def _on_disconnect(reason: str) -> None:
        reasons.append(reason)

    await session.on_disconnect(_on_disconnect)
    await session.start(code="abc")
    await asyncio.sleep(0.05)
    await session.stop()
    await asyncio.sleep(0.05)

    assert reasons == []


@pytest.mark.asyncio
async def test_disconnect_callback_can_be_removed():
    _FakeClientSession.ws_messages = [_FakeMsg(aiohttp.WSMsgType.CLOSED, b"")]
    _FakeClientSession.ws_delay_sec = 0.01

    session = _build_session()
    called = 0

    async def _on_disconnect(_reason: str) -> None:
        nonlocal called
        called += 1

    await session.on_disconnect(_on_disconnect)
    await session.off_disconnect(_on_disconnect)
    await session.start(code="abc")
    await asyncio.sleep(0.15)

    assert called == 0
    await session.stop()
