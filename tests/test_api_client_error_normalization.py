from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.api_client import QFarmApiClient, QFarmApiError
from astrbot_plugin_qfarm.services.runtime.runtime_manager import QFarmRuntimeManager


class _ErrorBackend(QFarmRuntimeManager):
    async def get_accounts(self) -> dict[str, object]:
        raise RuntimeError("后端异常")


@pytest.mark.asyncio
async def test_api_client_error_contains_source_hint(tmp_path: Path):
    backend = _ErrorBackend(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        logger=None,
    )
    client = QFarmApiClient(backend)

    with pytest.raises(QFarmApiError) as exc:
        await client.get_accounts()

    message = str(exc.value)
    assert "后端异常" in message
    assert "source=RuntimeError" in message
