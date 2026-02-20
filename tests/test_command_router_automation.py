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
    def __init__(self, apply_on_save: bool = True) -> None:
        self.apply_on_save = apply_on_save
        self.current_mode = "none"
        self.save_calls: list[tuple[str, dict[str, Any]]] = []
        self.automation_calls: list[tuple[str, str, str]] = []

    async def get_accounts(self) -> dict[str, Any]:
        return {
            "accounts": [
                {
                    "id": "acc-1",
                    "name": "测试账号",
                    "platform": "qq",
                    "qq": "10001",
                }
            ]
        }

    async def save_settings(self, account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.save_calls.append((account_id, payload))
        if self.apply_on_save:
            automation = payload.get("automation", {})
            self.current_mode = str(automation.get("fertilizer") or self.current_mode)
        return {}

    async def get_settings(self, account_id: str) -> dict[str, Any]:
        return {"automation": {"fertilizer": self.current_mode}}

    async def set_automation(self, account_id: str, key: str, value: str) -> dict[str, Any]:
        self.automation_calls.append((account_id, key, value))
        if key == "fertilizer":
            self.current_mode = value
        return {}


def _build_router(tmp_path: Path, api: _FakeApi) -> QFarmCommandRouter:
    store = QFarmStateStore(tmp_path)
    store.bind_account("u1", "acc-1", "测试账号")
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
async def test_fertilizer_prefers_settings_save(tmp_path: Path):
    api = _FakeApi(apply_on_save=True)
    router = _build_router(tmp_path, api)

    replies = await router._cmd_automation("u1", ["施肥", "organic"])
    assert replies and replies[0].text == "施肥模式已更新: organic"
    assert api.save_calls == [("acc-1", {"automation": {"fertilizer": "organic"}})]
    assert api.automation_calls == []


@pytest.mark.asyncio
async def test_fertilizer_fallback_to_automation_when_settings_not_applied(tmp_path: Path):
    api = _FakeApi(apply_on_save=False)
    router = _build_router(tmp_path, api)

    replies = await router._cmd_automation("u1", ["施肥", "both"])
    assert replies and "兼容回退已启用" in replies[0].text
    assert api.save_calls == [("acc-1", {"automation": {"fertilizer": "both"}})]
    assert api.automation_calls == [("acc-1", "fertilizer", "both")]
