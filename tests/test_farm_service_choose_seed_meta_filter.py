from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_qfarm.services.domain.farm_service import FarmService


class _Analytics:
    def get_plant_rankings(self, sort_by: str):  # pragma: no cover - not used in these cases
        _ = sort_by
        return []


@pytest.mark.asyncio
async def test_choose_seed_skips_unknown_meta_candidates():
    service = FarmService.__new__(FarmService)
    service.analytics = _Analytics()  # type: ignore[assignment]
    service.get_available_seeds = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "seedId": 20002,
                "requiredLevel": 1,
                "locked": False,
                "soldOut": False,
                "unknownMeta": True,
            },
            {
                "seedId": 20010,
                "requiredLevel": 5,
                "locked": False,
                "soldOut": False,
                "unknownMeta": False,
            },
        ]
    )

    picked = await service.choose_seed(current_level=20, strategy="preferred", preferred_seed_id=0)

    assert picked is not None
    assert int(picked["seedId"]) == 20010


@pytest.mark.asyncio
async def test_choose_seed_returns_none_when_only_unknown_meta_rows():
    service = FarmService.__new__(FarmService)
    service.analytics = _Analytics()  # type: ignore[assignment]
    service.get_available_seeds = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "seedId": 20002,
                "requiredLevel": 1,
                "locked": False,
                "soldOut": False,
                "unknownMeta": True,
            },
        ]
    )

    picked = await service.choose_seed(current_level=20, strategy="max_profit", preferred_seed_id=20002)

    assert picked is None
