from __future__ import annotations

from pathlib import Path
from typing import Any

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


class _FakeResponse:
    def __init__(self, status: int, text: str) -> None:
        self.status = int(status)
        self._text = str(text)

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False

    async def text(self) -> str:
        return self._text


class _FakeSession:
    response_status = 200
    response_text = "ok"
    last_post: dict[str, Any] | None = None

    def __init__(self, timeout: object | None = None) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
        _FakeSession.last_post = {"url": url, "json": dict(json), "headers": dict(headers)}
        return _FakeResponse(status=self.response_status, text=self.response_text)


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
    assert current["push"]["allowPrivateEndpoint"] is False
    assert current["push"]["bodyTokenEnabled"] is False
    assert current["push"]["maxConcurrency"] == 8
    assert current["push"]["maxPerMinute"] == 60

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


@pytest.mark.asyncio
async def test_send_push_once_rejects_private_endpoint_by_default(tmp_path: Path):
    manager = _build_manager(tmp_path)
    with pytest.raises(PushDeliverError) as exc:
        await manager._send_push_once(
            account_id="a1",
            push_cfg={
                "enabled": True,
                "channel": "webhook",
                "endpoint": "https://127.0.0.1/hook",
                "token": "abc",
            },
            title="t",
            content="c",
            context={},
        )
    assert exc.value.error_code == "endpoint_private"


@pytest.mark.asyncio
async def test_send_push_once_header_token_only_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manager = _build_manager(tmp_path)
    _FakeSession.response_status = 200
    _FakeSession.response_text = "ok"
    _FakeSession.last_post = None
    monkeypatch.setattr(runtime_manager_module.aiohttp, "ClientSession", _FakeSession)

    payload = await manager._send_push_once(
        account_id="a1",
        push_cfg={
            "enabled": True,
            "channel": "webhook",
            "endpoint": "https://example.com/hook",
            "token": "abc123",
        },
        title="t",
        content="c",
        context={},
    )
    assert payload["httpStatus"] == 200
    assert _FakeSession.last_post is not None
    assert _FakeSession.last_post["headers"]["Authorization"] == "Bearer abc123"
    assert "token" not in _FakeSession.last_post["json"]


@pytest.mark.asyncio
async def test_send_push_once_body_token_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manager = _build_manager(tmp_path)
    _FakeSession.response_status = 200
    _FakeSession.response_text = "ok"
    _FakeSession.last_post = None
    monkeypatch.setattr(runtime_manager_module.aiohttp, "ClientSession", _FakeSession)

    await manager._send_push_once(
        account_id="a1",
        push_cfg={
            "enabled": True,
            "channel": "webhook",
            "endpoint": "https://example.com/hook",
            "token": "abc123",
            "bodyTokenEnabled": True,
        },
        title="t",
        content="c",
        context={},
    )
    assert _FakeSession.last_post is not None
    assert _FakeSession.last_post["json"]["token"] == "abc123"


@pytest.mark.asyncio
async def test_send_push_with_retry_rate_limit_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manager = _build_manager(tmp_path)

    async def _fake_send_push_once(**_: object) -> dict[str, object]:
        return {"httpStatus": 200, "message": "ok"}

    async def _fake_sleep(_: float) -> None:
        return

    monkeypatch.setattr(manager, "_send_push_once", _fake_send_push_once)  # type: ignore[arg-type]
    monkeypatch.setattr(runtime_manager_module.asyncio, "sleep", _fake_sleep)

    push_cfg = {
        "enabled": True,
        "channel": "webhook",
        "endpoint": "https://example.com/hook",
        "token": "t",
        "retryMax": 0,
        "maxPerMinute": 1,
    }
    await manager._send_push_with_retry(
        account_id="a1",
        push_cfg=push_cfg,
        title="a",
        content="b",
        context={},
    )
    with pytest.raises(RuntimeError) as exc:
        await manager._send_push_with_retry(
            account_id="a1",
            push_cfg=push_cfg,
            title="a",
            content="b",
            context={},
        )
    assert "rate limit" in str(exc.value).lower()
