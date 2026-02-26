from __future__ import annotations

import asyncio
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
async def test_rebind_suggested_log_halts_runtime_and_sets_failed(tmp_path: Path):
    manager = _build_manager(tmp_path)
    manager._accounts = {
        "accounts": [{"id": "1", "name": "A", "platform": "qq", "code": "code-1"}],
        "nextId": 2,
    }
    runtime = SimpleNamespace(stop=AsyncMock(return_value=None))
    manager._runtimes["1"] = runtime  # type: ignore[assignment]
    await manager._set_runtime_status("1", runtimeState="running", lastStartError="")

    manager._on_runtime_log(
        "1",
        "scheduler",
        "reconnect failed, backoff=30.0s: websocket connect failed: 网关鉴权失败(HTTP 400)，登录凭据可能已失效",
        True,
        {
            "module": "system",
            "event": "reconnect_error",
            "result": "error",
            "errorCode": "ws_auth_400",
            "rebindSuggested": True,
            "codeHint": "len=6,tail=de-1",
        },
    )

    await asyncio.sleep(0.05)

    runtime.stop.assert_awaited_once()
    assert "1" not in manager._runtimes
    status = await manager.get_status("1")
    assert status["runtimeState"] == "failed"
    assert "请重新扫码绑定" in str(status.get("lastStartError") or "")
    assert status.get("currentCodeHint") == "len=6,tail=de-1"
    assert status.get("lastHoldSourceCodeHint") == "len=6,tail=de-1"
    account_logs = await manager.get_account_logs(limit=50)
    assert any(str(row.get("action")) == "rebind_required_hold" for row in account_logs)


@pytest.mark.asyncio
async def test_non_rebind_log_does_not_halt_runtime(tmp_path: Path):
    manager = _build_manager(tmp_path)
    manager._accounts = {
        "accounts": [{"id": "1", "name": "A", "platform": "qq", "code": "code-1"}],
        "nextId": 2,
    }
    runtime = SimpleNamespace(stop=AsyncMock(return_value=None))
    manager._runtimes["1"] = runtime  # type: ignore[assignment]
    await manager._set_runtime_status("1", runtimeState="running", lastStartError="")

    manager._on_runtime_log(
        "1",
        "heartbeat",
        "heartbeat failed (1/2): request timeout: gamepb.userpb.UserService.Heartbeat",
        True,
        {
            "module": "system",
            "event": "heartbeat_error",
            "result": "error",
            "errorCode": "rpc_timeout",
            "rebindSuggested": False,
        },
    )

    await asyncio.sleep(0.05)

    runtime.stop.assert_not_called()
    assert "1" in manager._runtimes


@pytest.mark.asyncio
async def test_rebind_hold_skips_stale_code_hint_event(tmp_path: Path):
    manager = _build_manager(tmp_path)
    manager._accounts = {
        "accounts": [{"id": "1", "name": "A", "platform": "qq", "code": "new-valid-code-9999"}],
        "nextId": 2,
    }
    runtime = SimpleNamespace(stop=AsyncMock(return_value=None))
    manager._runtimes["1"] = runtime  # type: ignore[assignment]
    await manager._set_runtime_status("1", runtimeState="running", lastStartError="")

    manager._on_runtime_log(
        "1",
        "scheduler",
        "reconnect failed from stale session",
        True,
        {
            "module": "system",
            "event": "reconnect_error",
            "result": "error",
            "errorCode": "ws_auth_400",
            "rebindSuggested": True,
            "codeHint": "len=8,tail=old1",
        },
    )

    await asyncio.sleep(0.05)

    runtime.stop.assert_not_called()
    assert "1" in manager._runtimes
    logs = await manager.get_logs("", module="system", event="rebind_hold_skip_stale", limit=20)
    assert any(str((row.get("meta") or {}).get("event")) == "rebind_hold_skip_stale" for row in logs)


@pytest.mark.asyncio
async def test_clear_rebind_hold_state_cancels_pending_task_and_clears_old_error(tmp_path: Path):
    manager = _build_manager(tmp_path)
    manager._accounts = {
        "accounts": [{"id": "1", "name": "A", "platform": "qq", "code": "code-1"}],
        "nextId": 2,
    }
    await manager._set_runtime_status(
        "1",
        runtimeState="failed",
        lastStartError="登录凭据可能已失效，已停止自动重连，请重新扫码绑定。",
    )

    task = asyncio.create_task(asyncio.sleep(10))
    manager._rebind_hold_tasks["1"] = task
    manager._rebind_hold_accounts.add("1")

    await manager._clear_rebind_hold_state("1", clear_status_error=True)

    assert task.cancelled() is True
    assert "1" not in manager._rebind_hold_tasks
    assert "1" not in manager._rebind_hold_accounts
    status = await manager.get_status("1")
    assert status["runtimeState"] == "stopped"
    assert status["lastStartError"] == ""
