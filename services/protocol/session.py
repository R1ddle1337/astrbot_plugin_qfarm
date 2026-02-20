from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .gate_codec import decode_event_message, decode_gate_message, encode_request
from .notify_dispatcher import NotifyDispatcher


class GatewaySessionError(RuntimeError):
    pass


@dataclass(slots=True)
class GatewaySessionConfig:
    gateway_ws_url: str
    platform: str = "qq"
    os: str = "iOS"
    client_version: str = "1.6.0.5_20251224"
    rpc_timeout_sec: int = 10
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
        "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
        "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13)"
    )
    origin: str = "https://gate-obt.nqf.qq.com"


class GatewaySession:
    def __init__(
        self,
        config: GatewaySessionConfig,
        *,
        logger: Any | None = None,
    ) -> None:
        self.config = config
        self.logger = logger

        self._http: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._recv_task: asyncio.Task | None = None

        self._client_seq = 1
        self._server_seq = 0
        self._pending: dict[int, asyncio.Future[bytes]] = {}
        self._send_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closed = True

        self.notify_dispatcher = NotifyDispatcher()

    @property
    def connected(self) -> bool:
        ws = self._ws
        return bool(ws is not None and not ws.closed)

    async def start(self, *, code: str) -> None:
        if self.connected:
            return
        if not code:
            raise GatewaySessionError("missing login code")
        self._closed = False
        self._client_seq = 1
        self._server_seq = 0
        self._pending.clear()
        self._http = aiohttp.ClientSession(headers={"User-Agent": self.config.user_agent})
        url = self._build_ws_url(code=code)
        try:
            self._ws = await self._http.ws_connect(
                url,
                heartbeat=0,
                origin=self.config.origin,
                autoclose=False,
                autoping=False,
            )
        except Exception as e:
            await self._hard_close()
            raise GatewaySessionError(f"websocket connect failed: {e}") from e
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def stop(self) -> None:
        async with self._close_lock:
            await self._hard_close()

    async def reconnect(self, *, code: str) -> None:
        await self.stop()
        await self.start(code=code)

    async def call(self, service: str, method: str, body: bytes, timeout_sec: int | None = None) -> bytes:
        if not self.connected:
            raise GatewaySessionError("websocket is not connected")
        timeout = max(1, int(timeout_sec or self.config.rpc_timeout_sec))
        async with self._send_lock:
            seq = self._client_seq
            self._client_seq += 1
            payload = encode_request(
                service,
                method,
                body,
                client_seq=seq,
                server_seq=self._server_seq,
            )
            fut: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
            self._pending[seq] = fut
            ws = self._ws
            if ws is None or ws.closed:
                self._pending.pop(seq, None)
                raise GatewaySessionError("websocket is closed")
            await ws.send_bytes(payload)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as e:
            self._pending.pop(seq, None)
            raise GatewaySessionError(f"request timeout: {service}.{method}") from e

    async def _recv_loop(self) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    await self._handle_binary(bytes(msg.data))
                elif msg.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED}:
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        except Exception:
            pass
        finally:
            await self._fail_all_pending("websocket disconnected")
            await self._hard_close()

    async def _handle_binary(self, data: bytes) -> None:
        parsed = decode_gate_message(data)
        if parsed.meta.server_seq > self._server_seq:
            self._server_seq = parsed.meta.server_seq

        if parsed.meta.message_type == 2:
            seq = parsed.meta.client_seq
            fut = self._pending.pop(seq, None)
            if fut is None or fut.done():
                return
            if parsed.meta.error_code != 0:
                fut.set_exception(
                    GatewaySessionError(
                        f"{parsed.meta.service_name}.{parsed.meta.method_name} "
                        f"error={parsed.meta.error_code} {parsed.meta.error_message}"
                    )
                )
                return
            fut.set_result(parsed.body)
            return

        if parsed.meta.message_type == 3:
            try:
                event_type, event_body = decode_event_message(parsed.body)
                await self.notify_dispatcher.emit(event_type, event_body)
            except Exception:
                return

    async def _fail_all_pending(self, reason: str) -> None:
        pending = list(self._pending.items())
        self._pending.clear()
        for _, fut in pending:
            if not fut.done():
                fut.set_exception(GatewaySessionError(reason))

    async def _hard_close(self) -> None:
        self._closed = True
        recv_task = self._recv_task
        self._recv_task = None
        if recv_task is not None and not recv_task.done():
            recv_task.cancel()
            try:
                await recv_task
            except Exception:
                pass
        ws = self._ws
        self._ws = None
        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass
        http = self._http
        self._http = None
        if http is not None and not http.closed:
            await http.close()
        await self.notify_dispatcher.clear()

    def _build_ws_url(self, *, code: str) -> str:
        query = urlencode(
            {
                "platform": self.config.platform,
                "os": self.config.os,
                "ver": self.config.client_version,
                "code": code,
                "openID": "",
            }
        )
        return f"{self.config.gateway_ws_url}?{query}"
