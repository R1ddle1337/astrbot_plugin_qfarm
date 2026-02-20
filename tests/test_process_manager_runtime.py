from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.process_manager import NodeProcessManager


def _build_manager(tmp_path: Path, managed_mode: bool) -> NodeProcessManager:
    return NodeProcessManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://gate-obt.nqf.qq.com/prod/ws",
        client_version="1.6.0.5_20251224",
        platform="qq",
        heartbeat_interval_sec=25,
        rpc_timeout_sec=10,
        managed_mode=managed_mode,
    )


def test_process_manager_status_contains_python_mode(tmp_path: Path):
    manager = _build_manager(tmp_path, managed_mode=True)
    status = manager.status()
    assert status["managed_mode"] is True
    assert status["mode"] == "python"
    assert status["runtimeCount"] == 0


@pytest.mark.asyncio
async def test_process_manager_rejects_start_when_not_managed(tmp_path: Path):
    manager = _build_manager(tmp_path, managed_mode=False)
    with pytest.raises(RuntimeError):
        await manager.start()
