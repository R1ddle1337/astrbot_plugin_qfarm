from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.runtime.runtime_manager import QFarmRuntimeManager


@pytest.mark.asyncio
async def test_runtime_logs_persist_and_reload(tmp_path: Path):
    manager = QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        persist_runtime_logs=True,
        runtime_log_max_entries=5,
        logger=None,
    )

    for index in range(8):
        manager._on_runtime_log(
            account_id="a1",
            tag="farm",
            message=f"log-{index}",
            is_warn=False,
            meta={"idx": index},
        )
    assert len(manager._global_logs) == 5

    rows = await manager.get_logs("a1", limit=10)
    assert len(rows) == 5
    assert any("log-7" in str(row.get("msg")) for row in rows)

    manager_reloaded = QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        persist_runtime_logs=True,
        runtime_log_max_entries=5,
        logger=None,
    )
    rows_reloaded = await manager_reloaded.get_logs("a1", limit=10)
    assert len(rows_reloaded) == 5
    assert any("log-7" in str(row.get("msg")) for row in rows_reloaded)
