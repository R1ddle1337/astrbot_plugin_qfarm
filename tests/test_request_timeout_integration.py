from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.api_client import QFarmApiClient, QFarmApiError
from astrbot_plugin_qfarm.services.runtime.runtime_manager import QFarmRuntimeManager


class _SlowBackend(QFarmRuntimeManager):
    async def get_accounts(self) -> dict[str, object]:
        await asyncio.sleep(1.2)
        return {"accounts": []}


@pytest.mark.asyncio
async def test_request_timeout_sec_is_enforced(tmp_path: Path):
    backend = _SlowBackend(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        logger=None,
    )
    client = QFarmApiClient(backend, request_timeout_sec=1)

    with pytest.raises(QFarmApiError) as exc:
        await client.get_accounts()

    assert "请求超时" in str(exc.value)
