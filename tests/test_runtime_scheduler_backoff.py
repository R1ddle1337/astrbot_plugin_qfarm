from __future__ import annotations

import time

import pytest

from astrbot_plugin_qfarm.services.runtime import account_runtime as account_runtime_module
from astrbot_plugin_qfarm.services.runtime.account_runtime import AccountRuntime


@pytest.mark.asyncio
async def test_scheduler_sets_farm_backoff_after_cycle_error(monkeypatch: pytest.MonkeyPatch):
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.running = True
    runtime.login_ready = True
    runtime.connected = True
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


async def _noop(*_: object, **__: object) -> dict[str, object]:
    return {}
