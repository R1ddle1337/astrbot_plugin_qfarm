from __future__ import annotations

import time

from astrbot_plugin_qfarm.services.domain.farm_service import FarmService
from astrbot_plugin_qfarm.services.protocol.proto import plantpb_pb2


class _DummySession:
    async def call(self, *args, **kwargs):  # pragma: no cover
        _ = (args, kwargs)
        raise RuntimeError("not used in this test")


class _DummyConfigData:
    def get_plant_name(self, plant_id: int) -> str:
        return f"plant-{plant_id}"


class _DummyAnalytics:
    pass


def test_mature_land_is_harvestable_even_when_not_stealable():
    service = FarmService(
        session=_DummySession(),  # type: ignore[arg-type]
        config_data=_DummyConfigData(),  # type: ignore[arg-type]
        analytics=_DummyAnalytics(),  # type: ignore[arg-type]
        rpc_timeout_sec=10,
    )

    now = int(time.time())
    land = plantpb_pb2.LandInfo(id=1, unlocked=True, level=1)
    land.plant.id = 1020001
    land.plant.stealable = False
    phase = land.plant.phases.add()
    phase.phase = plantpb_pb2.MATURE
    phase.begin_time = now - 10

    analyzed = service.analyze_lands([land], now_sec=now)

    assert analyzed.harvestable == [1]
    assert 1 not in analyzed.growing
    assert analyzed.lands_detail[0]["status"] == "harvestable"
