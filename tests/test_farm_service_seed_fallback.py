from __future__ import annotations

import pytest

from astrbot_plugin_qfarm.services.domain.config_data import SeedInfo
from astrbot_plugin_qfarm.services.domain.farm_service import FarmService


class _SessionFail:
    async def call(self, *args, **kwargs):  # pragma: no cover
        _ = (args, kwargs)
        raise RuntimeError("shop unavailable")


class _ConfigData:
    def get_plant_name_by_seed(self, seed_id: int) -> str:
        return f"seed-{seed_id}"

    def get_seed_image(self, seed_id: int) -> str:
        return f"/img/{seed_id}.png"

    def get_all_seeds(self, current_level: int):  # pragma: no cover - level unused in fallback construction
        _ = current_level
        return [
            SeedInfo(seed_id=20001, name="白萝卜", required_level=1, price=10, image="/img/20001.png"),
            SeedInfo(seed_id=20010, name="玉米", required_level=5, price=50, image="/img/20010.png"),
        ]


class _Analytics:
    def get_plant_rankings(self, sort_by: str):  # pragma: no cover
        _ = sort_by
        return []


@pytest.mark.asyncio
async def test_get_available_seeds_fallback_to_local_config_when_shop_failed():
    service = FarmService(
        session=_SessionFail(),  # type: ignore[arg-type]
        config_data=_ConfigData(),  # type: ignore[arg-type]
        analytics=_Analytics(),  # type: ignore[arg-type]
        rpc_timeout_sec=10,
    )
    rows = await service.get_available_seeds(current_level=20)

    assert len(rows) == 2
    assert rows[0]["seedId"] == 20001
    assert rows[0]["locked"] is False
    assert rows[0]["soldOut"] is False
    assert rows[0]["unknownMeta"] is True
    assert rows[1]["seedId"] == 20010
