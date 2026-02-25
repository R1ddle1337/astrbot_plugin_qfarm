from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_qfarm.services.runtime.account_runtime import AccountRuntime


class _ConfigDataStub:
    @staticmethod
    def get_plant_name_by_seed(seed_id: int) -> str:
        return {
            20002: "萝卜",
            20010: "生姜",
        }.get(int(seed_id), f"seed-{seed_id}")


def _build_runtime(
    *,
    seeds: list[dict[str, int | bool]],
    bag_items: list[SimpleNamespace],
    choose_seed_result: dict[str, int] | None,
    buy_reply: object,
    user_gold: int = 1000,
) -> AccountRuntime:
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.account = {"id": "acc-1"}
    runtime.logger = None
    runtime.log_callback = None
    runtime.config_data = _ConfigDataStub()
    runtime.user_state = {"level": 20, "gold": int(user_gold)}
    runtime.settings = {
        "strategy": "max_profit",
        "preferredSeedId": 20002,
        "automation": {"fertilizer": "none"},
    }
    runtime.operations = {}
    runtime.warehouse = SimpleNamespace(
        get_bag=AsyncMock(return_value="bag"),
        get_bag_items=lambda _bag: list(bag_items),
    )
    runtime.farm = SimpleNamespace(
        remove_plant=AsyncMock(return_value=None),
        get_available_seeds=AsyncMock(return_value=list(seeds)),
        choose_seed=AsyncMock(return_value=choose_seed_result),
        buy_goods=AsyncMock(return_value=buy_reply),
        plant=AsyncMock(return_value=1),
        fertilize=AsyncMock(return_value=0),
        last_plant_failures=[],
        last_plant_error="",
    )
    return runtime


@pytest.mark.asyncio
async def test_auto_plant_prefers_preferred_seed_from_bag_even_if_strategy_differs():
    runtime = _build_runtime(
        seeds=[
            {"seedId": 20002, "goodsId": 9001, "price": 20, "requiredLevel": 1, "locked": False, "soldOut": False},
            {"seedId": 20010, "goodsId": 9010, "price": 30, "requiredLevel": 5, "locked": False, "soldOut": False},
        ],
        bag_items=[SimpleNamespace(id=20002, count=3), SimpleNamespace(id=20010, count=8)],
        choose_seed_result={"seedId": 20010, "goodsId": 9010, "price": 30},
        buy_reply=SimpleNamespace(get_items=[]),
    )
    runtime.farm.plant = AsyncMock(return_value=2)

    planted = await runtime._auto_plant([], [1, 2])

    assert planted == 2
    runtime.farm.choose_seed.assert_not_called()
    runtime.farm.buy_goods.assert_not_called()
    runtime.farm.plant.assert_awaited_once_with(20002, [1, 2])
    assert runtime._last_seed_decision == "preferred_bag"


@pytest.mark.asyncio
async def test_auto_plant_falls_back_when_preferred_seed_unavailable():
    runtime = _build_runtime(
        seeds=[
            {"seedId": 20002, "goodsId": 9001, "price": 20, "requiredLevel": 1, "locked": False, "soldOut": True},
            {"seedId": 20010, "goodsId": 9010, "price": 30, "requiredLevel": 5, "locked": False, "soldOut": True},
        ],
        bag_items=[SimpleNamespace(id=20010, count=3)],
        choose_seed_result=None,
        buy_reply=SimpleNamespace(get_items=[]),
    )
    runtime.farm.plant = AsyncMock(return_value=1)

    planted = await runtime._auto_plant([], [7])

    assert planted == 1
    runtime.farm.choose_seed.assert_awaited_once()
    runtime.farm.plant.assert_awaited_once_with(20010, [7])
    assert runtime._last_seed_decision == "strategy_fallback_bag"
    assert "偏好种子 20002 当前不可用" in runtime._last_seed_decision_reason


@pytest.mark.asyncio
async def test_auto_plant_buys_preferred_seed_when_shop_available():
    runtime = _build_runtime(
        seeds=[
            {"seedId": 20002, "goodsId": 9001, "price": 10, "requiredLevel": 1, "locked": False, "soldOut": False},
            {"seedId": 20010, "goodsId": 9010, "price": 20, "requiredLevel": 5, "locked": False, "soldOut": False},
        ],
        bag_items=[],
        choose_seed_result={"seedId": 20010, "goodsId": 9010, "price": 20},
        buy_reply=SimpleNamespace(get_items=[SimpleNamespace(id=20002, count=1)]),
        user_gold=100,
    )
    runtime.farm.plant = AsyncMock(return_value=1)

    planted = await runtime._auto_plant([], [3])

    assert planted == 1
    runtime.farm.choose_seed.assert_not_called()
    runtime.farm.buy_goods.assert_awaited_once_with(9001, 1, 10)
    runtime.farm.plant.assert_awaited_once_with(20002, [3])
    assert runtime._last_seed_decision == "preferred_shop"


@pytest.mark.asyncio
async def test_auto_plant_reports_meta_unavailable_not_insufficient_gold_when_unknown_meta():
    runtime = _build_runtime(
        seeds=[
            {
                "seedId": 20002,
                "goodsId": 0,
                "price": 0,
                "requiredLevel": 1,
                "locked": False,
                "soldOut": False,
                "unknownMeta": True,
            }
        ],
        bag_items=[],
        choose_seed_result={"seedId": 20002, "goodsId": 0, "price": 0, "unknownMeta": True},
        buy_reply=SimpleNamespace(get_items=[]),
        user_gold=100000,
    )
    runtime.settings["preferredSeedId"] = 0
    runtime.settings["strategy"] = "preferred"
    runtime.farm.plant = AsyncMock(return_value=0)

    planted = await runtime._auto_plant([], [1])

    assert planted == 0
    assert "商店元数据不可用" in runtime._last_plant_skip_reason
    assert "金币不足" not in runtime._last_plant_skip_reason
    runtime.farm.buy_goods.assert_not_called()


@pytest.mark.asyncio
async def test_auto_plant_reports_insufficient_gold_when_purchase_meta_valid():
    runtime = _build_runtime(
        seeds=[
            {
                "seedId": 20002,
                "goodsId": 9001,
                "price": 2,
                "requiredLevel": 1,
                "locked": False,
                "soldOut": False,
                "unknownMeta": False,
            }
        ],
        bag_items=[],
        choose_seed_result={"seedId": 20002, "goodsId": 9001, "price": 2, "unknownMeta": False},
        buy_reply=SimpleNamespace(get_items=[]),
        user_gold=1,
    )
    runtime.settings["preferredSeedId"] = 0
    runtime.settings["strategy"] = "preferred"
    runtime.farm.plant = AsyncMock(return_value=0)

    planted = await runtime._auto_plant([], [9])

    assert planted == 0
    assert "金币不足" in runtime._last_plant_skip_reason
    assert "商店元数据不可用" not in runtime._last_plant_skip_reason
    runtime.farm.buy_goods.assert_not_called()


@pytest.mark.asyncio
async def test_preferred_seed_lookup_ignores_unknown_meta_rows(monkeypatch):
    monkeypatch.setattr(
        "astrbot_plugin_qfarm.services.runtime.account_runtime.asyncio.sleep",
        AsyncMock(return_value=None),
    )
    runtime = _build_runtime(
        seeds=[],
        bag_items=[],
        choose_seed_result=None,
        buy_reply=SimpleNamespace(get_items=[]),
        user_gold=100000,
    )
    runtime.farm.get_available_seeds = AsyncMock(
        return_value=[
            {
                "seedId": 20002,
                "goodsId": 0,
                "price": 0,
                "requiredLevel": 1,
                "locked": False,
                "soldOut": False,
                "unknownMeta": True,
            }
        ]
    )

    picked = await runtime._pick_preferred_seed_from_shop(current_level=20, preferred_seed_id=20002)

    assert picked is None
    assert "元数据不可用" in runtime._last_seed_decision_reason


@pytest.mark.asyncio
async def test_auto_plant_retries_seed_catalog_and_buys_after_meta_recovered(monkeypatch):
    monkeypatch.setattr(
        "astrbot_plugin_qfarm.services.runtime.account_runtime.asyncio.sleep",
        AsyncMock(return_value=None),
    )
    runtime = _build_runtime(
        seeds=[],
        bag_items=[],
        choose_seed_result=None,
        buy_reply=SimpleNamespace(get_items=[SimpleNamespace(id=20002, count=1)]),
        user_gold=100000,
    )
    runtime.settings["strategy"] = "max_profit"
    runtime.settings["preferredSeedId"] = 20002
    runtime.farm.get_available_seeds = AsyncMock(
        side_effect=[
            [
                {
                    "seedId": 20002,
                    "goodsId": 0,
                    "price": 0,
                    "requiredLevel": 1,
                    "locked": False,
                    "soldOut": False,
                    "unknownMeta": True,
                }
            ],
            [
                {
                    "seedId": 20002,
                    "goodsId": 9001,
                    "price": 2,
                    "requiredLevel": 1,
                    "locked": False,
                    "soldOut": False,
                    "unknownMeta": False,
                }
            ],
        ]
    )
    runtime.farm.plant = AsyncMock(return_value=1)

    planted = await runtime._auto_plant([], [3])

    assert planted == 1
    assert runtime.farm.get_available_seeds.await_count >= 2
    runtime.farm.buy_goods.assert_awaited_once_with(9001, 1, 2)
    runtime.farm.plant.assert_awaited_once_with(20002, [3])
    assert runtime._last_seed_decision == "preferred_shop"
