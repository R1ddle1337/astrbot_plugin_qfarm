from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
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


def test_runtime_logs_concurrent_writes_remain_parseable(tmp_path: Path):
    manager = QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        persist_runtime_logs=True,
        runtime_log_max_entries=512,
        runtime_log_flush_interval_sec=60.0,
        runtime_log_flush_batch=1,
        logger=None,
    )

    def _write_log(index: int) -> None:
        manager._on_runtime_log(
            account_id="a1",
            tag="farm",
            message=f"concurrent-{index}",
            is_warn=False,
            meta={"idx": index},
        )

    total = 120
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_write_log, idx) for idx in range(total)]
        for future in futures:
            future.result()

    manager._persist_runtime_logs(force=True)
    raw = json.loads(manager.runtime_logs_path.read_text(encoding="utf-8"))
    global_rows = raw.get("global", [])
    assert isinstance(global_rows, list)
    assert len(global_rows) == total
    assert all(str((row or {}).get("msg") or "").strip() for row in global_rows)


def test_runtime_logs_persist_failure_writes_warning(tmp_path: Path):
    class _Logger:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def warning(self, message: str) -> None:
            self.messages.append(str(message))

    logger = _Logger()
    manager = QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        persist_runtime_logs=True,
        runtime_log_max_entries=16,
        logger=logger,
    )
    manager._on_runtime_log("a1", "farm", "will-fail", False, {"idx": 1})

    def _raise_write_error(_path: Path, _data: dict[str, object]) -> None:
        raise OSError("disk full")

    manager._save_json_atomic = _raise_write_error  # type: ignore[assignment]
    manager._persist_runtime_logs(force=True)

    assert manager._runtime_logs_dirty is True
    assert logger.messages
    assert "persist failed" in logger.messages[-1]
