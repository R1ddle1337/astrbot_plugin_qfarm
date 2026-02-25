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
async def test_daily_routine_state_marks_done_and_persists():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime._daily_routines = {}
    runtime.runtime_state_persist = AsyncMock(return_value=None)
    runtime._debug_log = lambda *args, **kwargs: None

    can_run_first = await runtime._routine_can_run("email_rewards", cooldown_sec=300, force=False)
    can_run_second = await runtime._routine_can_run("email_rewards", cooldown_sec=300, force=False)
    assert runtime.runtime_state_persist.await_count == 0

    await runtime._mark_routine_done("email_rewards", result="ok", claimed=True, error="")
    can_run_after_done = await runtime._routine_can_run("email_rewards", cooldown_sec=300, force=False)

    state = runtime._routine_state("email_rewards")
    assert can_run_first is True
    assert can_run_second is True
    assert can_run_after_done is False
    assert state["doneDateKey"]
    assert state["lastCheckAt"] > 0
    assert state["lastClaimAt"] > 0
    assert state["lastResult"] == "ok"
    assert runtime.runtime_state_persist.await_count >= 1


@pytest.mark.asyncio
async def test_daily_routine_error_uses_short_backoff():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime._daily_routines = {}
    runtime.runtime_state_persist = AsyncMock(return_value=None)
    runtime._debug_log = lambda *args, **kwargs: None

    await runtime._mark_routine_error("email_rewards", "mock error")
    state = runtime._routine_state("email_rewards")
    assert state["lastResult"] == "error"
    assert state["lastCheckAt"] > 0

    can_run_too_early = await runtime._routine_can_run("email_rewards", cooldown_sec=600, force=False)
    runtime._daily_routines["email_rewards"]["lastCheckAt"] = int(time.time() * 1000) - 31_000
    can_run_after_backoff = await runtime._routine_can_run("email_rewards", cooldown_sec=600, force=False)

    assert can_run_too_early is False
    assert can_run_after_backoff is True


@pytest.mark.asyncio
async def test_run_daily_routines_respects_automation_switches():
    runtime = AccountRuntime.__new__(AccountRuntime)
    logs: list[tuple[tuple[object, ...], dict[str, object]]] = []
    runtime._automation = lambda: {
        "email": True,
        "mall": True,
        "share": False,
        "monthcard": True,
        "vip": False,
    }
    runtime._debug_log = lambda *args, **kwargs: logs.append((args, kwargs))
    runtime._run_email_routine = AsyncMock(return_value={"routine": "email_rewards"})
    runtime._run_mall_free_gifts_routine = AsyncMock(return_value={"routine": "mall_free_gifts"})
    runtime._run_mall_organic_routine = AsyncMock(return_value={"routine": "mall_organic_fertilizer"})
    runtime._run_fertilizer_gift_routine = AsyncMock(return_value={"routine": "fertilizer_gift_use"})
    runtime._run_share_routine = AsyncMock(return_value={"routine": "daily_share"})
    runtime._run_monthcard_routine = AsyncMock(return_value={"routine": "month_card_gift"})
    runtime._run_vip_routine = AsyncMock(return_value={"routine": "vip_daily_gift"})

    result = await runtime.run_daily_routines(force=True)

    runtime._run_email_routine.assert_awaited_once()
    runtime._run_mall_free_gifts_routine.assert_awaited_once()
    runtime._run_mall_organic_routine.assert_awaited_once()
    runtime._run_fertilizer_gift_routine.assert_awaited_once()
    runtime._run_monthcard_routine.assert_awaited_once()
    runtime._run_share_routine.assert_not_awaited()
    runtime._run_vip_routine.assert_not_awaited()
    assert result["force"] is True
    assert "mallFreeGifts" in result
    assert "fertilizerGift" in result
    assert "monthcard" in result
    assert result["statusCode"] == "none"
    assert any(kwargs.get("event") == "daily_summary" for _, kwargs in logs)


@pytest.mark.asyncio
async def test_fertilizer_gift_routine_marks_done_on_success():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime._daily_routines = {}
    runtime.runtime_state_persist = AsyncMock(return_value=None)
    runtime._debug_log = lambda *args, **kwargs: None
    runtime.warehouse = _WarehouseStub(
        use_fertilizer_gifts=AsyncMock(
            return_value={"mode": "batch", "usedKinds": 2, "usedCount": 8, "failedKinds": 0, "error": ""}
        )
    )

    result = await runtime._run_fertilizer_gift_routine(force=False)

    state = runtime._routine_state("fertilizer_gift_use")
    assert result["usedKinds"] == 2
    assert result["usedCount"] == 8
    assert result["error"] == ""
    assert state["lastResult"] == "ok"
    assert state["doneDateKey"]


@pytest.mark.asyncio
async def test_fertilizer_gift_routine_marks_error_when_all_failed():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime._daily_routines = {}
    runtime.runtime_state_persist = AsyncMock(return_value=None)
    runtime._debug_log = lambda *args, **kwargs: None
    runtime.warehouse = _WarehouseStub(
        use_fertilizer_gifts=AsyncMock(
            return_value={"mode": "fallback", "usedKinds": 0, "usedCount": 0, "failedKinds": 1, "error": "rpc error"}
        )
    )

    result = await runtime._run_fertilizer_gift_routine(force=False)

    state = runtime._routine_state("fertilizer_gift_use")
    assert result["usedKinds"] == 0
    assert result["usedCount"] == 0
    assert result["error"] == "rpc error"
    assert state["lastResult"] == "error"
    assert state["lastError"] == "rpc error"


class _WarehouseStub:
    def __init__(self, use_fertilizer_gifts: AsyncMock) -> None:
        self.use_fertilizer_gifts = use_fertilizer_gifts
