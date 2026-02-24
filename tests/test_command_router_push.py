from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from astrbot_plugin_qfarm.services.command_router import QFarmCommandRouter
from astrbot_plugin_qfarm.services.rate_limiter import RateLimiter
from astrbot_plugin_qfarm.services.state_store import QFarmStateStore


class _DummyProcessManager:
    def status(self) -> dict[str, Any]:
        return {}


class _FakeApi:
    def __init__(self) -> None:
        self.push_settings = {
            "enabled": True,
            "channel": "webhook",
            "endpoint": "https://example.com/webhook",
            "token": "abcdef1234",
        }
        self.save_calls: list[tuple[str, dict[str, Any]]] = []
        self.test_calls: list[tuple[str, str, str]] = []
        self.test_result: dict[str, Any] = {"ok": True, "message": "accepted"}

    async def get_accounts(self) -> dict[str, Any]:
        return {
            "accounts": [
                {"id": "acc-1", "name": "test", "platform": "qq", "qq": "10001"},
            ]
        }

    async def get_push_settings(self, account_id: str) -> dict[str, Any]:
        _ = account_id
        return dict(self.push_settings)

    async def save_push_settings(self, account_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        payload = dict(patch)
        self.save_calls.append((account_id, payload))
        self.push_settings.update(payload)
        return {"ok": True}

    async def send_push_test(self, account_id: str, title: str = "", content: str = "") -> dict[str, Any]:
        self.test_calls.append((account_id, title, content))
        return dict(self.test_result)


def _build_router(tmp_path: Path, api: _FakeApi) -> QFarmCommandRouter:
    store = QFarmStateStore(tmp_path)
    store.bind_account("u1", "acc-1", "test")
    return QFarmCommandRouter(
        api_client=api,  # type: ignore[arg-type]
        state_store=store,
        rate_limiter=RateLimiter(
            read_cooldown_sec=0.0,
            write_cooldown_sec=0.0,
            global_concurrency=5,
            account_write_serialized=True,
        ),
        process_manager=_DummyProcessManager(),  # type: ignore[arg-type]
        is_super_admin=lambda _: False,
    )


@pytest.mark.asyncio
async def test_push_view_masks_token(tmp_path: Path):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    replies = await router._cmd_push("u1", ["查看"])

    assert replies
    text = replies[0].text
    assert "【推送配置】" in text
    assert "开关: on" in text
    assert "通道: webhook" in text
    assert "地址: https://example.com/webhook" in text
    assert "令牌: ab***34" in text
    assert "abcdef1234" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "expected_patch", "expected_text"),
    [
        (["设置", "开关", "on"], {"enabled": True}, "推送开关已更新: on"),
        (["设置", "通道", "webhook"], {"channel": "webhook"}, "推送通道已更新: webhook"),
        (["设置", "地址", "https://hook.local/p"], {"endpoint": "https://hook.local/p"}, "推送地址已更新: https://hook.local/p"),
    ],
)
async def test_push_set_valid_params(tmp_path: Path, args: list[str], expected_patch: dict[str, Any], expected_text: str):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    replies = await router._cmd_push("u1", args)

    assert replies and replies[0].text == expected_text
    assert api.save_calls[-1] == ("acc-1", expected_patch)


@pytest.mark.asyncio
async def test_push_set_token_masks_in_reply(tmp_path: Path):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    replies = await router._cmd_push("u1", ["设置", "令牌", "new-secret-token"])

    assert replies and "推送令牌已更新: ne***en" in replies[0].text
    assert "new-secret-token" not in replies[0].text
    assert api.save_calls[-1] == ("acc-1", {"token": "new-secret-token"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["设置"], "用法: qfarm 推送 设置"),
        (["设置", "开关", "bad"], "推送开关参数非法"),
        (["设置", "通道", "smtp"], "推送通道参数非法"),
        (["设置", "未知", "x"], "推送设置参数非法"),
    ],
)
async def test_push_set_invalid_params(tmp_path: Path, args: list[str], expected: str):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    replies = await router._cmd_push("u1", args)

    assert replies and expected in replies[0].text
    assert not api.save_calls


@pytest.mark.asyncio
async def test_push_clear(tmp_path: Path):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    replies = await router._cmd_push("u1", ["清空"])

    assert replies and replies[0].text == "推送配置已清空。"
    assert api.save_calls[-1] == ("acc-1", {"enabled": False, "endpoint": "", "token": ""})


@pytest.mark.asyncio
async def test_push_test_success_reply(tmp_path: Path):
    api = _FakeApi()
    api.test_result = {"ok": True, "message": "ok"}
    router = _build_router(tmp_path, api)

    replies = await router._cmd_push("u1", ["测试"])

    assert replies and replies[0].text == "推送测试已发送: ok"
    assert api.test_calls == [("acc-1", "", "")]


@pytest.mark.asyncio
async def test_push_test_failed_reply(tmp_path: Path):
    api = _FakeApi()
    api.test_result = {"ok": False, "message": "webhook 404"}
    router = _build_router(tmp_path, api)

    replies = await router._cmd_push("u1", ["测试"])

    assert replies and replies[0].text == "推送测试失败: webhook 404"
    assert api.test_calls == [("acc-1", "", "")]
