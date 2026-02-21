from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_qfarm.services.domain.farm_service import LandAnalyzeResult
from astrbot_plugin_qfarm.services.runtime.account_runtime import AccountRuntime


class _FakeFarm:
    def __init__(self) -> None:
        self.harvest_calls: list[tuple[list[int], int]] = []

    async def get_all_lands(self, host_gid: int = 0) -> SimpleNamespace:
        _ = host_gid
        return SimpleNamespace(lands=[], operation_limits=[])

    def analyze_lands(self, lands):
        _ = lands
        return LandAnalyzeResult(
            harvestable=[1, 2],
            growing=[],
            empty=[4],
            dead=[3],
            need_water=[],
            need_weed=[],
            need_bug=[],
            unlockable=[],
            upgradable=[],
            lands_detail=[],
        )

    async def harvest(self, land_ids: list[int], gid: int):
        self.harvest_calls.append((list(land_ids), int(gid)))
        return {}

    async def weed(self, land_ids: list[int], gid: int):
        _ = (land_ids, gid)
        return {}

    async def bug(self, land_ids: list[int], gid: int):
        _ = (land_ids, gid)
        return {}

    async def water(self, land_ids: list[int], gid: int):
        _ = (land_ids, gid)
        return {}

    async def upgrade_land(self, land_id: int):
        _ = land_id
        return None


class _FakeFriend:
    def update_operation_limits(self, limits):
        _ = limits


@pytest.mark.asyncio
async def test_do_farm_operation_all_triggers_harvest_and_plant_flow():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.account = {"id": "acc-1"}
    runtime.user_state = {"gid": 9527}
    runtime.settings = {"automation": {"land_upgrade": True, "sell": True}}
    runtime.operations = {}
    runtime.logger = None
    runtime.log_callback = None
    runtime.farm = _FakeFarm()
    runtime.friend = _FakeFriend()
    runtime._auto_plant = AsyncMock(return_value=3)  # type: ignore[method-assign]
    runtime._auto_sell = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await runtime._do_farm_operation("all")

    assert runtime.farm.harvest_calls == [([1, 2], 9527)]
    runtime._auto_plant.assert_awaited_once_with([3, 1, 2], [4])
    runtime._auto_sell.assert_awaited_once()
    assert runtime.operations.get("harvest") == 2
    assert result["hadWork"] is True


@pytest.mark.asyncio
async def test_auto_plant_continues_when_remove_plant_failed():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.account = {"id": "acc-1"}
    runtime.user_state = {"level": 12}
    runtime.settings = {"strategy": "preferred", "preferredSeedId": 0, "automation": {"fertilizer": "none"}}
    runtime.operations = {}
    runtime.logger = None
    runtime.log_callback = None
    runtime.farm = SimpleNamespace(
        remove_plant=AsyncMock(side_effect=RuntimeError("remove fail")),
        choose_seed=AsyncMock(return_value={"seedId": 1001, "goodsId": 0, "price": 0}),
        buy_goods=AsyncMock(return_value={}),
        plant=AsyncMock(return_value=2),
        fertilize=AsyncMock(return_value=0),
    )

    planted = await runtime._auto_plant([1, 2], [2, 3])

    assert planted == 2
    runtime.farm.remove_plant.assert_awaited_once_with([1, 2])
    runtime.farm.plant.assert_awaited_once_with(1001, [2, 3, 1])
    assert runtime.operations.get("plant") == 2


@pytest.mark.asyncio
async def test_auto_plant_continues_when_buy_goods_failed():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.account = {"id": "acc-1"}
    runtime.user_state = {"level": 12}
    runtime.settings = {"strategy": "preferred", "preferredSeedId": 0, "automation": {"fertilizer": "none"}}
    runtime.operations = {}
    runtime.logger = None
    runtime.log_callback = None
    runtime.farm = SimpleNamespace(
        remove_plant=AsyncMock(return_value=None),
        choose_seed=AsyncMock(return_value={"seedId": 1002, "goodsId": 5566, "price": 88}),
        buy_goods=AsyncMock(side_effect=RuntimeError("insufficient gold")),
        plant=AsyncMock(return_value=1),
        fertilize=AsyncMock(return_value=0),
    )

    planted = await runtime._auto_plant([], [9])

    assert planted == 1
    runtime.farm.buy_goods.assert_awaited_once_with(5566, 1, 88)
    runtime.farm.plant.assert_awaited_once_with(1002, [9])
    assert runtime.operations.get("plant") == 1


@pytest.mark.asyncio
async def test_auto_plant_caps_buy_count_by_seed_stock_and_gold():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.account = {"id": "acc-1"}
    runtime.user_state = {"level": 30, "gold": 199}
    runtime.settings = {"strategy": "preferred", "preferredSeedId": 0, "automation": {"fertilizer": "none"}}
    runtime.operations = {}
    runtime.logger = None
    runtime.log_callback = None
    runtime.warehouse = SimpleNamespace(
        get_bag=AsyncMock(return_value="bag"),
        get_bag_items=lambda _bag: [SimpleNamespace(id=30001, count=1)],
    )
    runtime.farm = SimpleNamespace(
        remove_plant=AsyncMock(return_value=None),
        choose_seed=AsyncMock(return_value={"seedId": 30001, "goodsId": 5566, "price": 100}),
        buy_goods=AsyncMock(return_value=SimpleNamespace(get_items=[])),
        plant=AsyncMock(return_value=2),
        fertilize=AsyncMock(return_value=0),
    )

    planted = await runtime._auto_plant([], [11, 12, 13, 14])

    assert planted == 2
    runtime.farm.buy_goods.assert_awaited_once_with(5566, 1, 100)
    runtime.farm.plant.assert_awaited_once_with(30001, [11, 12])
    assert runtime.operations.get("plant") == 2
