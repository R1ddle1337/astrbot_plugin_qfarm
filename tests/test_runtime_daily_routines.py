from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_qfarm.services.runtime.account_runtime import AccountRuntime


@pytest.mark.asyncio
async def test_daily_routine_state_marks_done_and_persists():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime._daily_routines = {}
    runtime.runtime_state_persist = AsyncMock(return_value=None)
    runtime._debug_log = lambda *args, **kwargs: None

    can_run_first = await runtime._routine_can_run("email_rewards", cooldown_sec=300, force=False)
    can_run_second = await runtime._routine_can_run("email_rewards", cooldown_sec=300, force=False)
    await runtime._mark_routine_done("email_rewards", result="ok", claimed=True, error="")

    state = runtime._routine_state("email_rewards")
    assert can_run_first is True
    assert can_run_second is False
    assert state["doneDateKey"]
    assert state["lastClaimAt"] > 0
    assert state["lastResult"] == "ok"
    assert runtime.runtime_state_persist.await_count >= 2


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
    runtime._run_share_routine = AsyncMock(return_value={"routine": "daily_share"})
    runtime._run_monthcard_routine = AsyncMock(return_value={"routine": "month_card_gift"})
    runtime._run_vip_routine = AsyncMock(return_value={"routine": "vip_daily_gift"})

    result = await runtime.run_daily_routines(force=True)

    runtime._run_email_routine.assert_awaited_once()
    runtime._run_mall_free_gifts_routine.assert_awaited_once()
    runtime._run_mall_organic_routine.assert_awaited_once()
    runtime._run_monthcard_routine.assert_awaited_once()
    runtime._run_share_routine.assert_not_awaited()
    runtime._run_vip_routine.assert_not_awaited()
    assert result["force"] is True
    assert "mallFreeGifts" in result
    assert "monthcard" in result
    assert result["statusCode"] == "none"
    assert any(kwargs.get("event") == "daily_summary" for _, kwargs in logs)
