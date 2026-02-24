from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from astrbot_plugin_qfarm.services.command_router import QFarmCommandRouter
from astrbot_plugin_qfarm.services.rate_limiter import RateLimiter
from astrbot_plugin_qfarm.services.state_store import QFarmStateStore


class _DummyProcessManager:
    def status(self) -> dict[str, Any]:
        return {}


class _FakeApi:
    async def get_accounts(self) -> dict[str, Any]:
        return {
            "accounts": [
                {"id": "acc-1", "name": "test", "platform": "qq", "qq": "10001"},
            ]
        }

    async def get_status(self, account_id: str) -> dict[str, Any]:
        _ = account_id
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


def _build_router(tmp_path: Path) -> QFarmCommandRouter:
    store = QFarmStateStore(tmp_path)
    store.bind_account("u1", "acc-1", "test")
    return QFarmCommandRouter(
        api_client=_FakeApi(),  # type: ignore[arg-type]
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
    assert "会话收益" in text
    assert "操作计数" in text
    assert "启动重试次数: 1" in text
