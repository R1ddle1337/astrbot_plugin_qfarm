from __future__ import annotations

import json
from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.runtime.runtime_manager import QFarmRuntimeManager


def _read_runtime_log_file(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_runtime_logs_flush_on_batch_and_stop(tmp_path: Path):
    manager = QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        persist_runtime_logs=True,
        runtime_log_max_entries=20,
        runtime_log_flush_interval_sec=60.0,
        runtime_log_flush_batch=3,
        logger=None,
    )

    for idx in range(2):
        manager._on_runtime_log("a1", "farm", f"row-{idx}", False, {"idx": idx})

    raw = _read_runtime_log_file(manager.runtime_logs_path)
    assert len(raw.get("global", [])) == 0

    manager._on_runtime_log("a1", "farm", "row-2", False, {"idx": 2})
    raw = _read_runtime_log_file(manager.runtime_logs_path)
    assert len(raw.get("global", [])) == 3

    manager._on_runtime_log("a1", "farm", "row-3", False, {"idx": 3})
    await manager.stop()
    raw = _read_runtime_log_file(manager.runtime_logs_path)
    assert len(raw.get("global", [])) == 4
