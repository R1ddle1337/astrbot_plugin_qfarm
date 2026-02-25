from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
async def test_kickout_keeps_account_and_sets_failed_status(tmp_path: Path):
    manager = _build_manager(tmp_path)
    manager._accounts = {
        "accounts": [
            {
                "id": "1",
                "name": "test",
                "platform": "qq",
                "code": "abc",
                "createdAt": 1,
                "updatedAt": 1,
            }
        ],
        "nextId": 2,
    }
    await manager._set_runtime_status("1", runtimeState="running", lastStartError="")

    fake_runtime = SimpleNamespace(stop=AsyncMock(return_value=None))
    manager._runtimes["1"] = fake_runtime  # type: ignore[assignment]
    manager.delete_account = AsyncMock(return_value={})  # type: ignore[method-assign]

    await manager._on_runtime_kicked("1", "kicked by remote")

    fake_runtime.stop.assert_awaited_once()
    manager.delete_account.assert_not_awaited()  # type: ignore[attr-defined]
    assert "1" not in manager._runtimes

    accounts = await manager.get_accounts()
    assert any(str(row.get("id")) == "1" for row in accounts.get("accounts", []))

    status = await manager.get_status("1")
    assert status["runtimeState"] == "failed"
    assert "重新扫码绑定" in str(status["lastStartError"])

    logs = await manager.get_logs("", module="system", event="kickout_hold", limit=20)
    assert any(str((row.get("meta") or {}).get("event")) == "kickout_hold" for row in logs)

    account_logs = await manager.get_account_logs(20)
    assert any(str(row.get("action")) == "kickout_hold" for row in account_logs)
