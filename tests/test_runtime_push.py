from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.runtime import runtime_manager as runtime_manager_module
from astrbot_plugin_qfarm.services.runtime.runtime_manager import PushDeliverError, QFarmRuntimeManager


def _build_manager(tmp_path: Path, *, default_push: dict[str, object] | None = None) -> QFarmRuntimeManager:
    return QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        default_push=default_push,  # type: ignore[arg-type]
        logger=None,
    )


@pytest.mark.asyncio
async def test_push_settings_merge_and_save(tmp_path: Path):
    manager = _build_manager(
        tmp_path,
        default_push={
            "enabled": True,
            "channel": "webhook",
            "endpoint": "https://default.local/hook",
            "token": "token-default",
            "retryMax": 2,
        },
    )
    current = await manager.get_push_settings("acc-1")
    assert current["push"]["enabled"] is True
    assert current["push"]["channel"] == "webhook"
    assert current["push"]["endpoint"] == "https://default.local/hook"
    assert current["push"]["token"] == "token-default"

    saved = await manager.save_push_settings(
        "acc-1",
        {
            "enabled": False,
            "channel": "webhook",
            "endpoint": "https://new.local/hook",
            "token": "token-new",
        },
    )
    assert saved["push"]["enabled"] is False
    assert saved["push"]["endpoint"] == "https://new.local/hook"
    assert saved["push"]["token"] == "token-new"

    settings = await manager.get_settings("acc-1")
    assert settings["push"]["endpoint"] == "https://new.local/hook"


def test_should_auto_push_entry_filter(tmp_path: Path):
    manager = _build_manager(tmp_path)
    assert manager._should_auto_push_entry(
        {
            "accountId": "a1",
            "meta": {"module": "task", "event": "daily_summary", "result": "ok"},
        }
    )
    assert manager._should_auto_push_entry(
        {
            "accountId": "a1",
            "meta": {"module": "task", "event": "email_rewards", "result": "error"},
        }
    )
    assert not manager._should_auto_push_entry(
        {
            "accountId": "a1",
            "meta": {"module": "task", "event": "email_rewards", "result": "ok"},
        }
    )
    assert not manager._should_auto_push_entry(
        {
            "accountId": "a1",
            "meta": {"module": "push", "event": "deliver", "result": "error"},
        }
    )


def test_on_runtime_log_schedules_auto_push(tmp_path: Path):
    manager = _build_manager(tmp_path)
    scheduled: list[dict[str, object]] = []
    manager._schedule_auto_push = lambda entry: scheduled.append(entry)  # type: ignore[method-assign]

    manager._on_runtime_log(
        "a1",
        "daily",
        "summary",
        False,
        {"module": "task", "event": "daily_summary", "result": "ok"},
    )
    assert len(scheduled) == 1

    manager._on_runtime_log(
        "a1",
        "push",
        "deliver failed",
        True,
        {"module": "push", "event": "deliver", "result": "error"},
    )
    assert len(scheduled) == 1


@pytest.mark.asyncio
async def test_send_push_with_retry_records_push_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manager = _build_manager(tmp_path)
    attempts = {"count": 0}

    async def _fake_send_push_once(**_: object) -> dict[str, object]:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PushDeliverError("mock http error", http_status=500, error_code="http_500")
        return {"httpStatus": 200, "message": "ok"}

    async def _fake_sleep(_: float) -> None:
        return

    monkeypatch.setattr(manager, "_send_push_once", _fake_send_push_once)  # type: ignore[arg-type]
    monkeypatch.setattr(runtime_manager_module.asyncio, "sleep", _fake_sleep)

    result = await manager._send_push_with_retry(
        account_id="a1",
        push_cfg={
            "enabled": True,
            "channel": "webhook",
            "endpoint": "https://example.local/hook",
            "token": "token",
            "retryMax": 2,
            "autoEvents": "core",
        },
        title="test",
        content="payload",
        context={"reason": "unit_test", "module": "task", "event": "daily_summary", "result": "ok"},
    )

    assert result["ok"] is True
    assert result["attempt"] == 3
    assert attempts["count"] == 3
    push_rows = [
        row
        for row in manager._global_logs
        if str((row.get("meta") or {}).get("module") or "") == "push"
        and str((row.get("meta") or {}).get("event") or "") == "deliver"
    ]
    assert len(push_rows) == 3
    assert str((push_rows[-1].get("meta") or {}).get("result") or "") == "ok"


@pytest.mark.asyncio
async def test_send_push_test_returns_error_when_endpoint_missing(tmp_path: Path):
    manager = _build_manager(tmp_path)
    payload = await manager.send_push_test("a1")
    assert payload["ok"] is False
    assert "endpoint" in str(payload["message"]).lower()
