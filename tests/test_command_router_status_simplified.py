from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from astrbot_plugin_qfarm.services.command_router import QFarmCommandRouter
from astrbot_plugin_qfarm.services.rate_limiter import RateLimiter
from astrbot_plugin_qfarm.services.state_store import QFarmStateStore


class _DummyProcessManager:
    def status(self) -> dict[str, Any]:
        return {}


def _default_status_payload() -> dict[str, Any]:
    return {
        "runtimeState": "running",
        "startRetryCount": 1,
        "lastStartError": "timeout",
        "status": {"name": "农场主", "level": 33, "gold": 1000, "exp": 888, "coupon": 12},
        "connection": {"connected": True},
        "nextChecks": {"farmRemainSec": 15, "friendRemainSec": 40},
        "automation": {"farm": True, "friend": True, "task": True},
        "operations": {"harvest": 3, "plant": 2},
        "sessionExpGained": 50,
        "sessionGoldGained": 200,
        "sessionCouponGained": 1,
        "expProgress": {"current": 888, "needed": 1000},
    }


class _FakeApi:
    def __init__(self, status_payload: dict[str, Any] | None = None) -> None:
        self._status_payload = status_payload or _default_status_payload()

    async def get_accounts(self) -> dict[str, Any]:
        return {
            "accounts": [
                {"id": "acc-1", "name": "test", "platform": "qq", "qq": "10001"},
            ]
        }

    async def get_status(self, account_id: str) -> dict[str, Any]:
        _ = account_id
        return deepcopy(self._status_payload)


def _build_router(tmp_path: Path, status_payload: dict[str, Any] | None = None) -> QFarmCommandRouter:
    store = QFarmStateStore(tmp_path)
    store.bind_account("u1", "acc-1", "test")
    return QFarmCommandRouter(
        api_client=_FakeApi(status_payload=status_payload),  # type: ignore[arg-type]
        state_store=store,
        rate_limiter=RateLimiter(
            read_cooldown_sec=0.0,
            write_cooldown_sec=0.0,
            global_concurrency=5,
            account_write_serialized=True,
        ),
        process_manager=_DummyProcessManager(),  # type: ignore[arg-type]
        is_super_admin=lambda _: False,
    )


@pytest.mark.asyncio
async def test_status_default_is_brief(tmp_path: Path):
    router = _build_router(tmp_path)

    replies = await router._cmd_status("u1", [])

    assert replies
    text = replies[0].text
    assert "【农场状态】" in text
    assert "资源: 金币1000 经验888 点券12" in text
    assert "倒计时: 农田15s 好友40s" in text
    assert "会话收益" not in text
    assert "操作计数" not in text


@pytest.mark.asyncio
async def test_status_verbose_keeps_extended_fields(tmp_path: Path):
    router = _build_router(tmp_path)

    replies = await router._cmd_status("u1", ["详细"])

    assert replies
    text = replies[0].text
    assert "会话净变化" in text
    assert "会话收益" not in text
    assert "操作计数" in text
    assert "启动重试次数: 1" in text


@pytest.mark.asyncio
async def test_status_verbose_remain_zero_shows_slot_waiting_message(tmp_path: Path):
    status_payload = _default_status_payload()
    status_payload["nextChecks"] = {"farmRemainSec": 0, "friendRemainSec": 30}
    router = _build_router(tmp_path, status_payload=status_payload)

    replies = await router._cmd_status("u1", ["详细"])

    assert replies
    text = replies[0].text
    assert "调度说明: 已到时，等待调度槽位（1s轮询）。" in text


@pytest.mark.asyncio
async def test_status_verbose_negative_coupon_adds_organic_fertilizer_note(tmp_path: Path):
    status_payload = _default_status_payload()
    status_payload["sessionCouponGained"] = -6
    router = _build_router(tmp_path, status_payload=status_payload)

    replies = await router._cmd_status("u1", ["详细"])

    assert replies
    text = replies[0].text
    assert "会话净变化" in text
    assert "点券 -6" in text
    assert "负值通常来自 mall_organic_fertilizer 自动购买消耗。" in text


@pytest.mark.asyncio
async def test_status_verbose_shows_last_farm_reason_preferring_plant_skip_reason(tmp_path: Path):
    status_payload = _default_status_payload()
    status_payload["lastFarm"] = {
        "plantSkipReason": "种子库存不足",
        "noActionReason": "当前地块状态无需执行本轮操作",
        "explain": {
            "plantSkipReason": "种子库存不足(来自 explain)",
            "noActionReason": "无需动作(来自 explain)",
        },
    }
    router = _build_router(tmp_path, status_payload=status_payload)

    replies = await router._cmd_status("u1", ["详细"])

    assert replies
    text = replies[0].text
    assert "最近农田说明: 种子库存不足" in text
    assert "最近农田说明: 当前地块状态无需执行本轮操作" not in text


@pytest.mark.asyncio
async def test_status_verbose_shows_last_farm_no_action_reason_when_plant_missing(tmp_path: Path):
    status_payload = _default_status_payload()
    status_payload["lastFarm"] = {
        "explain": {
            "noActionReason": "当前地块状态无需执行本轮操作",
        }
    }
    router = _build_router(tmp_path, status_payload=status_payload)

    replies = await router._cmd_status("u1", ["详细"])

    assert replies
    text = replies[0].text
    assert "最近农田说明: 当前地块状态无需执行本轮操作" in text


@pytest.mark.asyncio
async def test_status_verbose_shows_last_farm_plant_diagnostics(tmp_path: Path):
    status_payload = _default_status_payload()
    status_payload["lastFarm"] = {
        "plantTargetCount": 6,
        "plantedCount": 4,
        "plantSkipReason": "部分地块库存不足",
    }
    router = _build_router(tmp_path, status_payload=status_payload)

    replies = await router._cmd_status("u1", ["详细"])

    assert replies
    text = replies[0].text
    assert "最近种植诊断: target=6 planted=4" in text


@pytest.mark.asyncio
async def test_status_verbose_shows_seed_decision_diagnostics(tmp_path: Path):
    status_payload = _default_status_payload()
    status_payload["lastFarm"] = {
        "plantTargetCount": 3,
        "plantedCount": 2,
        "seedDecision": "strategy_fallback_bag",
        "seedDecisionReason": "偏好种子暂不可用，已回退",
        "preferredSeedId": 20002,
        "selectedSeedId": 20010,
        "selectedSeedName": "生姜",
    }
    router = _build_router(tmp_path, status_payload=status_payload)

    replies = await router._cmd_status("u1", ["详细"])

    assert replies
    text = replies[0].text
    assert "本轮选种决策: strategy_fallback_bag / 偏好种子暂不可用，已回退" in text
    assert "偏好种子: 20002" in text
    assert "本轮实际种子: 生姜(20010)" in text
