from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.runtime import runtime_manager as runtime_manager_module


class _FakeRuntime:
    fail_plan: dict[str, list[str | None]] = {}
    attempts: dict[str, int] = {}

    def __init__(self, *, account, **_: object) -> None:
        self.account = dict(account)
        self.account_id = str(self.account.get("id"))
        self.running = False

    async def start(self) -> None:
        current = _FakeRuntime.attempts.get(self.account_id, 0) + 1
        _FakeRuntime.attempts[self.account_id] = current
        plan = _FakeRuntime.fail_plan.get(self.account_id, [])
        if current <= len(plan):
            error = plan[current - 1]
            if error:
                raise RuntimeError(error)
        self.running = True

    async def stop(self) -> None:
        self.running = False

    async def get_status(self) -> dict[str, object]:
        return {
            "connection": {"connected": self.running},
            "status": {
                "name": "fake",
                "level": 1,
                "gold": 0,
                "coupon": 0,
                "exp": 0,
                "platform": "qq",
            },
            "uptime": 0,
            "operations": {},
            "sessionExpGained": 0,
            "sessionGoldGained": 0,
            "sessionCouponGained": 0,
            "lastExpGain": 0,
            "lastGoldGain": 0,
            "limits": {},
            "automation": {},
            "preferredSeed": 0,
            "expProgress": {"current": 0, "needed": 0, "level": 0},
            "configRevision": 0,
            "nextChecks": {"farmRemainSec": 0, "friendRemainSec": 0},
        }

    def apply_settings(self, settings: dict[str, object], revision: int) -> None:
        _ = (settings, revision)


@pytest.fixture
def build_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime_manager_module, "AccountRuntime", _FakeRuntime)

    def _factory(max_attempts: int = 3):
        _FakeRuntime.fail_plan = {}
        _FakeRuntime.attempts = {}
        manager = runtime_manager_module.QFarmRuntimeManager(
            plugin_root=tmp_path,
            data_dir=tmp_path / "data",
            gateway_ws_url="wss://example.invalid/ws",
            client_version="1.0.0",
            platform="qq",
            heartbeat_interval_sec=25,
            rpc_timeout_sec=10,
            start_retry_max_attempts=max_attempts,
            start_retry_base_delay_sec=0.01,
            start_retry_max_delay_sec=0.02,
            auto_start_concurrency=5,
            logger=None,
        )
        return manager

    return _factory


@pytest.mark.asyncio
async def test_start_account_retry_success(build_manager):
    manager = build_manager(max_attempts=3)
    manager._accounts = {
        "accounts": [{"id": "1", "name": "A", "platform": "qq", "code": "code-1"}],
        "nextId": 2,
    }

    _FakeRuntime.fail_plan = {
        "1": ["websocket disconnected", "websocket disconnected", None],
    }

    await manager.start_account("1")
    status = await manager.get_status("1")
    assert status["runtimeState"] == "running"
    assert status["startRetryCount"] == 2
    assert status["lastStartError"] == ""
    assert _FakeRuntime.attempts["1"] == 3


@pytest.mark.asyncio
async def test_start_account_retry_failed(build_manager):
    manager = build_manager(max_attempts=3)
    manager._accounts = {
        "accounts": [{"id": "1", "name": "A", "platform": "qq", "code": "code-1"}],
        "nextId": 2,
    }
    _FakeRuntime.fail_plan = {
        "1": ["websocket disconnected", "websocket disconnected", "websocket disconnected"],
    }

    with pytest.raises(RuntimeError) as exc:
        await manager.start_account("1")

    text = str(exc.value)
    assert "重试3/3" in text
    assert "websocket disconnected" in text

    status = await manager.get_status("1")
    assert status["runtimeState"] == "failed"
    assert status["startRetryCount"] == 3
    assert "websocket disconnected" in status["lastStartError"]


@pytest.mark.asyncio
async def test_start_account_non_retryable_error(build_manager):
    manager = build_manager(max_attempts=5)
    manager._accounts = {
        "accounts": [{"id": "1", "name": "A", "platform": "qq", "code": "code-1"}],
        "nextId": 2,
    }
    _FakeRuntime.fail_plan = {"1": ["missing login code"]}

    with pytest.raises(RuntimeError):
        await manager.start_account("1")

    assert _FakeRuntime.attempts["1"] == 1
    status = await manager.get_status("1")
    assert status["runtimeState"] == "failed"
    assert status["startRetryCount"] == 1


@pytest.mark.asyncio
async def test_auto_start_isolated_per_account(build_manager):
    manager = build_manager(max_attempts=2)
    manager._accounts = {
        "accounts": [
            {"id": "1", "name": "A", "platform": "qq", "code": "code-1"},
            {"id": "2", "name": "B", "platform": "qq", "code": "code-2"},
        ],
        "nextId": 3,
    }
    _FakeRuntime.fail_plan = {
        "1": ["websocket disconnected", "websocket disconnected"],
        "2": [None],
    }

    await manager.start()

    accounts = await manager.get_accounts()
    by_id = {str(row["id"]): row for row in accounts["accounts"]}
    assert by_id["1"]["runtimeState"] == "failed"
    assert by_id["2"]["runtimeState"] == "running"

    service = manager.service_status()
    assert service["failedCount"] == 1
    assert service["runtimeCount"] == 1
