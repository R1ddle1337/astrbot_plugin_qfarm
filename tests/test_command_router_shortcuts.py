from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_qfarm.services.command_router import QFarmCommandRouter, RouterReply
from astrbot_plugin_qfarm.services.rate_limiter import RateLimiter
from astrbot_plugin_qfarm.services.state_store import QFarmStateStore


class _DummyProcessManager:
    def status(self) -> dict[str, Any]:
        return {}


class _FakeApi:
    async def ping(self) -> dict[str, Any]:
        return {}


def _build_router(tmp_path: Path) -> QFarmCommandRouter:
    store = QFarmStateStore(tmp_path)
    return QFarmCommandRouter(
        api_client=_FakeApi(),  # type: ignore[arg-type]
        state_store=store,
        rate_limiter=RateLimiter(
            read_cooldown_sec=0.0,
            write_cooldown_sec=0.0,
            global_concurrency=5,
            account_write_serialized=True,
        ),
        process_manager=_DummyProcessManager(),  # type: ignore[arg-type]
        is_super_admin=lambda _: True,
    )


@pytest.mark.asyncio
async def test_dispatch_login_shortcut_maps_to_bind_scan(tmp_path: Path):
    router = _build_router(tmp_path)
    event = object()
    router._cmd_account = AsyncMock(return_value=[RouterReply(text="ok")])  # type: ignore[method-assign]

    await router._dispatch(event=event, user_id="u1", tokens=["登录"])

    router._cmd_account.assert_awaited_once_with(event, "u1", ["绑定扫码"])


@pytest.mark.asyncio
async def test_dispatch_logout_shortcut_maps_to_unbind(tmp_path: Path):
    router = _build_router(tmp_path)
    event = object()
    router._cmd_account = AsyncMock(return_value=[RouterReply(text="ok")])  # type: ignore[method-assign]

    await router._dispatch(event=event, user_id="u1", tokens=["退出登录"])

    router._cmd_account.assert_awaited_once_with(event, "u1", ["解绑"])


@pytest.mark.asyncio
async def test_dispatch_quick_plant_maps_to_farm_operate_plant(tmp_path: Path):
    router = _build_router(tmp_path)
    router._cmd_farm = AsyncMock(return_value=[RouterReply(text="ok")])  # type: ignore[method-assign]

    await router._dispatch(event=object(), user_id="u1", tokens=["种满"])

    router._cmd_farm.assert_awaited_once_with("u1", ["操作", "plant"])


@pytest.mark.asyncio
async def test_dispatch_autoall_default_maps_to_automation_all_on(tmp_path: Path):
    router = _build_router(tmp_path)
    router._cmd_automation = AsyncMock(return_value=[RouterReply(text="ok")])  # type: ignore[method-assign]

    await router._dispatch(event=object(), user_id="u1", tokens=["全自动"])

    router._cmd_automation.assert_awaited_once_with("u1", ["全开"])


@pytest.mark.asyncio
async def test_dispatch_autoall_off_maps_to_automation_all_off(tmp_path: Path):
    router = _build_router(tmp_path)
    router._cmd_automation = AsyncMock(return_value=[RouterReply(text="ok")])  # type: ignore[method-assign]

    await router._dispatch(event=object(), user_id="u1", tokens=["全自动", "关"])

    router._cmd_automation.assert_awaited_once_with("u1", ["全关"])
