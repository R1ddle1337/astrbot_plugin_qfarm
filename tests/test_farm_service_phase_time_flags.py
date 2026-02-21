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

    def get_seed_id_by_plant(self, plant_id: int) -> int:
        _ = plant_id
        return 20001

    def get_seed_image(self, seed_id: int) -> str:
        return f"/seed/{seed_id}.png"


class _DummyAnalytics:
    pass


def test_need_flags_support_phase_time_trigger():
    service = FarmService(
        session=_DummySession(),  # type: ignore[arg-type]
        config_data=_DummyConfigData(),  # type: ignore[arg-type]
        analytics=_DummyAnalytics(),  # type: ignore[arg-type]
        rpc_timeout_sec=10,
    )

    now = int(time.time())
    land = plantpb_pb2.LandInfo(id=7, unlocked=True, level=2)
    land.plant.id = 1020007
    phase = land.plant.phases.add()
    phase.phase = plantpb_pb2.GERMINATION
    phase.begin_time = now - 100
    phase.dry_time = now - 1
    phase.weeds_time = now - 2
    phase.insect_time = now - 3

    analyzed = service.analyze_lands([land], now_sec=now)

    assert analyzed.growing == [7]
    assert analyzed.need_water == [7]
    assert analyzed.need_weed == [7]
    assert analyzed.need_bug == [7]

    detail = analyzed.lands_detail[0]
    assert detail["status"] == "growing"
    assert detail["seedId"] == 20001
    assert detail["seedImage"] == "/seed/20001.png"
