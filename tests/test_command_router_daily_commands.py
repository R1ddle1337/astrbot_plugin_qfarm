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
    def __init__(self) -> None:
        self.daily_calls: list[tuple[str, str, bool]] = []
        self.mall_buy_calls: list[tuple[str, int, int]] = []

    async def get_accounts(self) -> dict[str, Any]:
        return {
            "accounts": [
                {"id": "acc-1", "name": "test", "platform": "qq", "qq": "10001"},
            ]
        }

    async def get_email_list(self, account_id: str, box_type: int) -> dict[str, Any]:
        _ = account_id
        return {
            "boxType": int(box_type),
            "emails": [
                {"id": f"mail-{box_type}", "title": "daily", "hasReward": True, "claimed": False},
            ],
        }

    async def run_daily_routine(self, account_id: str, routine: str, force: bool = False) -> dict[str, Any]:
        self.daily_calls.append((account_id, routine, bool(force)))
        return {
            "routine": routine,
            "claimed": True,
            "rewardItems": 1,
            "state": {"doneDateKey": "2026-02-24", "lastCheckAt": 1, "lastClaimAt": 1, "lastResult": "ok"},
        }

    async def get_mall_goods(self, account_id: str, slot_type: int = 1) -> dict[str, Any]:
        _ = (account_id, slot_type)
        return {
            "slotType": 1,
            "goods": [
                {"goodsId": 1002, "name": "organic", "isFree": False, "isLimited": False},
                {"goodsId": 2001, "name": "gift", "isFree": True, "isLimited": False},
            ],
        }

    async def purchase_mall_goods(self, account_id: str, goods_id: int, count: int = 1) -> dict[str, Any]:
        self.mall_buy_calls.append((account_id, int(goods_id), int(count)))
        return {"goodsId": int(goods_id), "count": int(count)}

    async def get_monthcard_infos(self, account_id: str) -> dict[str, Any]:
        _ = account_id
        return {"infos": [{"goodsId": 11, "canClaim": True, "reward": {"id": 1001, "count": 88}}]}

    async def get_vip_daily_status(self, account_id: str) -> dict[str, Any]:
        _ = account_id
        return {"canClaim": True, "hasGift": True}

    async def check_can_share(self, account_id: str) -> dict[str, Any]:
        _ = account_id
        return {"canShare": True}



def _build_router(tmp_path: Path, api: _FakeApi) -> QFarmCommandRouter:
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
async def test_email_claim_command_calls_daily_routine(tmp_path: Path):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    replies = await router._cmd_email("u1", ["claim"])

    assert api.daily_calls == [("acc-1", "email", True)]
    assert replies and "邮件" in replies[0].text


@pytest.mark.asyncio
async def test_mall_buy_command_calls_purchase(tmp_path: Path):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    replies = await router._cmd_mall("u1", ["buy", "1002", "3"])

    assert api.mall_buy_calls == [("acc-1", 1002, 3)]
    assert replies and "1002" in replies[0].text


@pytest.mark.asyncio
async def test_monthcard_view_command_outputs_goods(tmp_path: Path):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    replies = await router._cmd_monthcard("u1", ["view"])

    assert replies and "goodsId=11" in replies[0].text


@pytest.mark.asyncio
async def test_vip_status_command_outputs_flags(tmp_path: Path):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    replies = await router._cmd_vip("u1", ["status"])

    assert replies and "canClaim=True" in replies[0].text


@pytest.mark.asyncio
async def test_share_claim_command_calls_daily_routine(tmp_path: Path):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    replies = await router._cmd_share("u1", ["claim"])

    assert api.daily_calls == [("acc-1", "share", True)]
    assert replies and "分享" in replies[0].text
