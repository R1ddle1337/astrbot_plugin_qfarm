from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_qfarm.services.command_router import QFarmCommandRouter
from astrbot_plugin_qfarm.services.rate_limiter import RateLimiter
from astrbot_plugin_qfarm.services.state_store import QFarmStateStore


class _DummyProcessManager:
    def status(self) -> dict[str, Any]:
        return {}


class _SeedApi:
    def __init__(self, seeds: list[dict[str, Any]]) -> None:
        self._seeds = seeds
        self.save_settings = AsyncMock(return_value={})
        self.do_farm_operation = AsyncMock(return_value={"hadWork": True, "actions": ["种植1"], "plantedCount": 1})

    async def get_accounts(self) -> dict[str, Any]:
        return {
            "accounts": [
                {"id": "acc-1", "name": "test", "platform": "qq", "qq": "10001"},
            ]
        }

    async def get_seeds(self, account_id: str) -> list[dict[str, Any]]:
        _ = account_id
        return list(self._seeds)


def _build_router(tmp_path: Path, api: _SeedApi) -> QFarmCommandRouter:
    store = QFarmStateStore(tmp_path)
    store.bind_account("u1", "acc-1", "test")
    return QFarmCommandRouter(
        api_client=api,  # type: ignore[arg-type]
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
async def test_settings_seed_rejects_sold_out(tmp_path: Path):
    api = _SeedApi(
        [
            {"seedId": 20002, "name": "测试种子", "locked": False, "soldOut": True, "unknownMeta": False},
        ]
    )
    router = _build_router(tmp_path, api)

    replies = await router._cmd_settings("u1", ["种子", "20002"])

    assert replies
    assert "已售罄" in replies[0].text
    api.save_settings.assert_not_awaited()
    api.do_farm_operation.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_seed_rejects_unknown_meta(tmp_path: Path):
    api = _SeedApi(
        [
            {"seedId": 20002, "name": "测试种子", "locked": False, "soldOut": False, "unknownMeta": True},
        ]
    )
    router = _build_router(tmp_path, api)

    replies = await router._cmd_settings("u1", ["种子", "20002"])

    assert replies
    assert "无法严格校验" in replies[0].text
    api.save_settings.assert_not_awaited()
    api.do_farm_operation.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_seed_valid_triggers_immediate_plant_check(tmp_path: Path):
    api = _SeedApi(
        [
            {"seedId": 20002, "name": "测试种子", "locked": False, "soldOut": False, "unknownMeta": False},
        ]
    )
    router = _build_router(tmp_path, api)

    replies = await router._cmd_settings("u1", ["种子", "20002"])

    assert replies
    text = replies[0].text
    assert "偏好种子已更新: 20002" in text
    assert "已立即触发一次种植校验。" in text
    api.save_settings.assert_awaited_once_with("acc-1", {"seedId": 20002})
    api.do_farm_operation.assert_awaited_once_with("acc-1", "plant")


@pytest.mark.asyncio
async def test_seeds_list_marks_unknown_meta_row(tmp_path: Path):
    api = _SeedApi(
        [
            {"seedId": 20002, "name": "测试种子", "locked": False, "soldOut": False, "unknownMeta": True, "price": 2, "requiredLevel": 1},
        ]
    )
    router = _build_router(tmp_path, api)

    replies = await router._cmd_seeds("u1", ["列表"])

    assert replies
    assert "商店元数据缺失" in replies[0].text
