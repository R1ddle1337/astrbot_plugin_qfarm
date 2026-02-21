from __future__ import annotations

import pytest

from astrbot_plugin_qfarm.services.domain.farm_service import FarmService
from astrbot_plugin_qfarm.services.protocol.proto import plantpb_pb2


class _DummyConfigData:
    pass


class _DummyAnalytics:
    pass


class _CaptureSession:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    async def call(self, service: str, method: str, body: bytes, timeout_sec: int):  # pragma: no cover
        _ = (service, method, timeout_sec)
        self.calls.append(body)
        return b""


@pytest.mark.asyncio
async def test_plant_prefers_items_payload():
    session = _CaptureSession()
    service = FarmService(
        session=session,  # type: ignore[arg-type]
        config_data=_DummyConfigData(),  # type: ignore[arg-type]
        analytics=_DummyAnalytics(),  # type: ignore[arg-type]
        rpc_timeout_sec=10,
    )

    ok = await service.plant(20001, [1, 2])

    assert ok == 2
    assert len(session.calls) == 2

    req1 = plantpb_pb2.PlantRequest()
    req1.ParseFromString(session.calls[0])
    assert len(req1.items) == 1
    assert req1.items[0].seed_id == 20001
    assert list(req1.items[0].land_ids) == [1]
    assert len(req1.land_and_seed) == 0

    req2 = plantpb_pb2.PlantRequest()
    req2.ParseFromString(session.calls[1])
    assert len(req2.items) == 1
    assert req2.items[0].seed_id == 20001
    assert list(req2.items[0].land_ids) == [2]
    assert len(req2.land_and_seed) == 0


class _AlwaysFailSession:
    async def call(self, service: str, method: str, body: bytes, timeout_sec: int):  # pragma: no cover
        _ = (service, method, body, timeout_sec)
        raise RuntimeError()


@pytest.mark.asyncio
async def test_plant_failure_error_text_never_empty():
    session = _AlwaysFailSession()
    service = FarmService(
        session=session,  # type: ignore[arg-type]
        config_data=_DummyConfigData(),  # type: ignore[arg-type]
        analytics=_DummyAnalytics(),  # type: ignore[arg-type]
        rpc_timeout_sec=10,
    )

    ok = await service.plant(20001, [1])

    assert ok == 0
    assert service.last_plant_error
    assert "RuntimeError" in service.last_plant_error
    assert service.last_plant_failures
