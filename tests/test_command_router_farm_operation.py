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
        return {"accounts": [{"id": "acc-1", "name": "测试账号", "platform": "qq", "qq": "10001"}]}

    async def do_farm_operation(self, account_id: str, op_type: str) -> dict[str, Any]:
        assert account_id == "acc-1"
        assert op_type == "plant"
        return {
            "hadWork": False,
            "actions": [],
            "summary": {
                "harvestable": 0,
                "empty": 7,
                "dead": 0,
                "unlockable": 15,
                "upgradable": 0,
            },
            "plantTargetCount": 7,
            "plantedCount": 0,
            "plantSkipReason": "种子库存不足且金币不足，无法购买种子",
            "plantFailures": [{"landId": 1, "error": "items=GatewaySessionError; map=GatewaySessionError"}],
        }


def _build_router(tmp_path: Path) -> QFarmCommandRouter:
    store = QFarmStateStore(tmp_path)
    store.bind_account("u1", "acc-1", "测试账号")
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
async def test_cmd_farm_operate_plant_reports_skip_reason(tmp_path: Path):
    router = _build_router(tmp_path)
    replies = await router._cmd_farm("u1", ["操作", "plant"])
    assert replies
    text = replies[0].text
    assert "农田操作完成: plant" in text
    assert "播种结果: 0/7" in text
    assert "未种植原因: 种子库存不足且金币不足，无法购买种子" in text
    assert "失败示例: 地块#1 items=GatewaySessionError; map=GatewaySessionError" in text
