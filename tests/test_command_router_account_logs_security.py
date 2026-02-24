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
    pass


class _Event:
    def __init__(self, message: str, user_id: str = "u1") -> None:
        self.message_str = message
        self._user_id = user_id

    def get_sender_id(self) -> str:
        return self._user_id

    def get_group_id(self) -> str:
        return ""


def _build_router(tmp_path: Path, *, is_super_admin: bool) -> QFarmCommandRouter:
    store = QFarmStateStore(tmp_path, static_allowed_users=["u1"])
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
        is_super_admin=lambda _: is_super_admin,
    )


@pytest.mark.asyncio
async def test_account_logs_requires_super_admin(tmp_path: Path):
    router = _build_router(tmp_path, is_super_admin=False)

    replies = await router.handle(_Event("qfarm 账号日志"))

    assert replies
    assert "仅超级管理员可用" in replies[0].text


@pytest.mark.asyncio
async def test_account_logs_allows_super_admin(tmp_path: Path):
    router = _build_router(tmp_path, is_super_admin=True)
    router._dispatch = AsyncMock(return_value=[RouterReply(text="ok")])  # type: ignore[method-assign]

    replies = await router.handle(_Event("qfarm 账号日志"))

    assert replies and replies[0].text == "ok"
