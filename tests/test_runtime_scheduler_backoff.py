from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import pytest

_RUNTIME_PACKAGE = "astrbot_plugin_qfarm.services.runtime"
_RUNTIME_DIR = Path(__file__).resolve().parents[1] / "services" / "runtime"
_ACCOUNT_RUNTIME_NAME = f"{_RUNTIME_PACKAGE}.account_runtime"

runtime_pkg = ModuleType(_RUNTIME_PACKAGE)
runtime_pkg.__path__ = [str(_RUNTIME_DIR)]
sys.modules[_RUNTIME_PACKAGE] = runtime_pkg
spec = importlib.util.spec_from_file_location(_ACCOUNT_RUNTIME_NAME, _RUNTIME_DIR / "account_runtime.py")
if spec is None or spec.loader is None:
    raise RuntimeError("failed to load account_runtime module")
account_runtime_module = importlib.util.module_from_spec(spec)
sys.modules[_ACCOUNT_RUNTIME_NAME] = account_runtime_module
spec.loader.exec_module(account_runtime_module)

AccountRuntime = account_runtime_module.AccountRuntime


@pytest.mark.asyncio
async def test_scheduler_sets_farm_backoff_after_cycle_error(monkeypatch: pytest.MonkeyPatch):
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.running = True
    runtime.login_ready = True
    runtime.connected = True
    runtime.session = _SessionStub(connected=True)
    runtime._next_farm_at = 0.0
    runtime._next_friend_at = time.time() + 3600
    runtime._automation = lambda: {"farm": True, "task": True, "friend": False}
    runtime.check_and_claim_tasks = _noop  # type: ignore[method-assign]
    runtime.run_daily_routines = _noop  # type: ignore[method-assign]
    runtime._in_friend_quiet_hours = lambda: False
    runtime._auto_friend_cycle = _noop  # type: ignore[method-assign]
    runtime._rand_interval = lambda _: 30
    runtime._debug_log = lambda *args, **kwargs: None

    async def _raise_once(_: str) -> dict[str, object]:
        runtime.running = False
        raise RuntimeError("mock farm failure")

    async def _fast_sleep(_: float) -> None:
        return

    runtime.do_farm_operation = _raise_once  # type: ignore[method-assign]
    monkeypatch.setattr(account_runtime_module.asyncio, "sleep", _fast_sleep)

    now = time.time()
    await runtime._scheduler_loop()

    assert runtime._next_farm_at >= now + 4.5
    assert runtime._next_farm_at <= now + 6.5


@pytest.mark.asyncio
async def test_scheduler_sets_friend_backoff_with_exponential_retry(monkeypatch: pytest.MonkeyPatch):
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.running = True
    runtime.login_ready = True
    runtime.connected = True
    runtime.session = _SessionStub(connected=True)
    runtime._next_farm_at = time.time() + 3600
    runtime._next_friend_at = 0.0
    runtime._automation = lambda: {"farm": False, "task": False, "friend": True}
    runtime.check_and_claim_tasks = _noop  # type: ignore[method-assign]
    runtime.run_daily_routines = _noop  # type: ignore[method-assign]
    runtime.do_farm_operation = _noop  # type: ignore[method-assign]
    runtime._in_friend_quiet_hours = lambda: False
    runtime._rand_interval = lambda _: 30
    runtime._debug_log = lambda *args, **kwargs: None

    async def _friend_fail() -> dict[str, object]:
        raise RuntimeError("mock friend failure")

    sleep_ticks = {"count": 0}

    async def _fast_sleep(sec: float) -> None:
        if abs(sec - 1.0) < 0.0001:
            sleep_ticks["count"] += 1
            if sleep_ticks["count"] == 1:
                runtime._next_friend_at = 0.0
            if sleep_ticks["count"] >= 2:
                runtime.running = False
        return

    runtime._auto_friend_cycle = _friend_fail  # type: ignore[method-assign]
    monkeypatch.setattr(account_runtime_module.asyncio, "sleep", _fast_sleep)

    await runtime._scheduler_loop()

    remain = runtime._next_friend_at - time.time()
    assert remain >= 15.0
    assert remain <= 30.0


@pytest.mark.asyncio
async def test_scheduler_reconnects_immediately_when_session_disconnected(monkeypatch: pytest.MonkeyPatch):
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.running = True
    runtime.login_ready = True
    runtime.connected = True
    runtime.session = _SessionStub(connected=False)
    runtime._debug_log = lambda *args, **kwargs: None

    connect_calls = {"count": 0}

    async def _connect() -> None:
        connect_calls["count"] += 1
        runtime.login_ready = True
        runtime.connected = True
        runtime.session.connected = True
        runtime.running = False

    sleep_calls: list[float] = []

    async def _fast_sleep(sec: float) -> None:
        sleep_calls.append(sec)
        return

    runtime._connect_and_login = _connect  # type: ignore[method-assign]
    monkeypatch.setattr(account_runtime_module.asyncio, "sleep", _fast_sleep)

    await runtime._scheduler_loop()

    assert connect_calls["count"] == 1
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_heartbeat_marks_offline_after_fail_limit(monkeypatch: pytest.MonkeyPatch):
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.running = True
    runtime.login_ready = True
    runtime.connected = True
    runtime.heartbeat_interval_sec = 1
    runtime.heartbeat_fail_limit = 2
    runtime.user_state = {"gid": 10001}
    runtime.session_config = _SessionConfigStub(client_version="1.0.0-test")
    runtime._debug_log = lambda *args, **kwargs: None

    runtime.user = _UserStub(heartbeat=AsyncMock(side_effect=RuntimeError("mock heartbeat failure")))
    runtime.session = _SessionStub(connected=True)
    async def _stop_session() -> None:
        runtime.session.connected = False

    runtime.session.stop = AsyncMock(side_effect=_stop_session)

    sleep_ticks = {"count": 0}

    async def _fast_sleep(_: float) -> None:
        sleep_ticks["count"] += 1
        if sleep_ticks["count"] >= 4:
            runtime.running = False
        return

    monkeypatch.setattr(account_runtime_module.asyncio, "sleep", _fast_sleep)

    await runtime._heartbeat_loop()

    assert runtime.user.heartbeat.await_count == 2
    runtime.session.stop.assert_awaited_once()
    assert runtime.connected is False
    assert runtime.login_ready is False


async def _noop(*_: object, **__: object) -> dict[str, object]:
    return {}


class _SessionStub:
    def __init__(self, connected: bool) -> None:
        self.connected = bool(connected)

    async def stop(self) -> None:
        self.connected = False


class _SessionConfigStub:
    def __init__(self, client_version: str) -> None:
        self.client_version = client_version


class _UserStub:
    def __init__(self, heartbeat: AsyncMock) -> None:
        self.heartbeat = heartbeat
