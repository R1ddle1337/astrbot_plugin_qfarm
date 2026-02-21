from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.runtime import runtime_manager as runtime_manager_module


class _FailStartRuntime:
    stop_called = 0

    def __init__(self, *, account, **_: object) -> None:
        self.account = dict(account)

    async def start(self) -> None:
        raise RuntimeError("websocket disconnected")

    async def stop(self) -> None:
        _FailStartRuntime.stop_called += 1


@pytest.mark.asyncio
async def test_start_failure_will_cleanup_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _FailStartRuntime.stop_called = 0
    monkeypatch.setattr(runtime_manager_module, "AccountRuntime", _FailStartRuntime)
    manager = runtime_manager_module.QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        start_retry_max_attempts=1,
        logger=None,
    )
    manager._accounts = {
        "accounts": [{"id": "1", "name": "A", "platform": "qq", "code": "code-1"}],
        "nextId": 2,
    }

    with pytest.raises(RuntimeError):
        await manager.start_account("1")

    assert _FailStartRuntime.stop_called == 1
    assert "1" not in manager._runtimes
