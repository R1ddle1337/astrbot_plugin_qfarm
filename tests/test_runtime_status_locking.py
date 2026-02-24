from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.runtime.runtime_manager import QFarmRuntimeManager


def _build_manager(tmp_path: Path) -> QFarmRuntimeManager:
    return QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        logger=None,
    )


@pytest.mark.asyncio
async def test_runtime_status_updates_keep_runtime_json_valid(tmp_path: Path):
    manager = _build_manager(tmp_path)

    async def _update(idx: int) -> None:
        await manager._set_runtime_status(
            "acc-1",
            runtimeState="running",
            startRetryCount=idx,
            lastStartError=f"e{idx}",
        )

    await asyncio.gather(*(_update(i) for i in range(20)))

    raw = json.loads(manager.runtime_path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert isinstance(raw.get("status"), dict)
    row = raw["status"].get("acc-1")
    assert isinstance(row, dict)
    assert row.get("runtimeState") == "running"
