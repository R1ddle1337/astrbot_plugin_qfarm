from __future__ import annotations

from types import SimpleNamespace

import pytest

import astrbot_plugin_qfarm.services.runtime.account_runtime as account_runtime_module
from astrbot_plugin_qfarm.services.protocol.proto import userpb_pb2
from astrbot_plugin_qfarm.services.runtime.account_runtime import AccountRuntime


class _ConfigDataStub:
    @staticmethod
    def get_level_exp_progress(level: int, exp: int) -> dict[str, int]:
        return {"level": int(level), "exp": int(exp)}


class _FriendStub:
    @staticmethod
    def get_operation_limits() -> list[dict[str, int]]:
        return []


@pytest.mark.asyncio
async def test_get_status_next_checks_uses_ceil_and_contains_last_farm(monkeypatch: pytest.MonkeyPatch):
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.config_data = _ConfigDataStub()
    runtime.connected = True
    runtime.login_ready = True
    runtime.session = SimpleNamespace(connected=True)
    runtime.user_state = {"name": "A", "level": 10, "gold": 150, "coupon": 20, "exp": 80, "platform": "qq"}
    runtime.started_at = 90.0
    runtime.operations = {"harvest": 2}
    runtime.initial_state = {"exp": 70, "gold": 120, "coupon": 10}
    runtime.last_gain = {"exp": 3, "gold": 4}
    runtime.friend = _FriendStub()
    runtime._automation = lambda: {"farm": True}
    runtime.settings = {"preferredSeedId": 5566}
    runtime.settings_revision = 7
    runtime._next_farm_at = 100.01
    runtime._next_friend_at = 100.99
    runtime._daily_routines_snapshot = lambda: {"email_rewards": {"done": True}}
    runtime._last_farm_result = {
        "mode": "plant",
        "plantTargetCount": 5,
        "plantedCount": 2,
        "noActionReason": "",
        "plantSkipReason": "种子库存不足",
    }
    monkeypatch.setattr(account_runtime_module.time, "time", lambda: 100.0)

    status = await runtime.get_status()

    assert status["nextChecks"] == {"farmRemainSec": 1, "friendRemainSec": 1}
    assert status["lastFarm"]["mode"] == "plant"
    assert status["lastFarm"]["plantTargetCount"] == 5
    assert status["lastFarm"]["plantedCount"] == 2
    assert status["lastFarm"]["noActionReason"] == ""
    assert status["lastFarm"]["plantSkipReason"] == "种子库存不足"
    assert status["lastFarm"]["seedDecision"] == ""
    assert status["lastFarm"]["seedDecisionReason"] == ""
    assert status["lastFarm"]["preferredSeedId"] == 0
    assert status["lastFarm"]["selectedSeedId"] == 0
    assert status["lastFarm"]["selectedSeedName"] == ""


@pytest.mark.asyncio
async def test_basic_notify_ignores_non_positive_level_and_logs_debug_event():
    runtime = AccountRuntime.__new__(AccountRuntime)
    logs: list[tuple[str, str, str, bool, dict[str, object]]] = []

    def _log_callback(account_id: str, tag: str, message: str, is_warn: bool, meta: dict[str, object]) -> None:
        logs.append((account_id, tag, message, is_warn, meta))

    runtime.account = {"id": "acc-1"}
    runtime.logger = None
    runtime.log_callback = _log_callback
    runtime.user_state = {"level": 6, "gold": 100, "exp": 200}

    notify = userpb_pb2.BasicNotify()
    notify.basic.level = 0
    notify.basic.gold = 180
    notify.basic.exp = 260
    await runtime._on_notify("gamepb.userpb.BasicNotify", notify.SerializeToString())

    assert runtime.user_state["level"] == 6
    assert runtime.user_state["gold"] == 180
    assert runtime.user_state["exp"] == 260
    assert any(
        entry[4].get("module") == "farm" and entry[4].get("event") == "basic_level_ignored"
        for entry in logs
    )


@pytest.mark.asyncio
async def test_basic_notify_updates_level_when_positive():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.account = {"id": "acc-1"}
    runtime.logger = None
    runtime.log_callback = None
    runtime.user_state = {"level": 2, "gold": 10, "exp": 20}

    notify = userpb_pb2.BasicNotify()
    notify.basic.level = 9
    await runtime._on_notify("BasicNotify", notify.SerializeToString())

    assert runtime.user_state["level"] == 9


@pytest.mark.asyncio
async def test_connect_and_login_missing_code_has_rebind_hint():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.account = {"id": "acc-1", "code": ""}

    with pytest.raises(RuntimeError, match="code.*重新扫码绑定"):
        await runtime._connect_and_login()
