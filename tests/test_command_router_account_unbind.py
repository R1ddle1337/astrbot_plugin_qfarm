from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_qfarm.services.command_router import QFarmCommandRouter
from astrbot_plugin_qfarm.services.rate_limiter import RateLimiter
from astrbot_plugin_qfarm.services.state_store import QFarmStateStore


class _DummyProcessManager:
    def status(self) -> dict[str, Any]:
        return {}


class _FakeApi:
    def __init__(self, *, delete_side_effect: Exception | None = None) -> None:
        if delete_side_effect is None:
            self.delete_account = AsyncMock(return_value={"ok": True})
        else:
            self.delete_account = AsyncMock(side_effect=delete_side_effect)


def _build_router(tmp_path: Path, *, delete_side_effect: Exception | None = None) -> tuple[QFarmCommandRouter, QFarmStateStore]:
    store = QFarmStateStore(tmp_path)
    store.bind_account("u1", "acc-1", "test")
    router = QFarmCommandRouter(
        api_client=_FakeApi(delete_side_effect=delete_side_effect),  # type: ignore[arg-type]
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
    return router, store


@pytest.mark.asyncio
async def test_account_unbind_removes_local_binding_after_remote_delete_success(tmp_path: Path):
    router, store = _build_router(tmp_path)

    replies = await router._cmd_account(event=None, user_id="u1", args=["解绑"])

    assert replies
    assert "解绑成功" in replies[0].text
    assert store.get_bound_account("u1") is None
    router.api.delete_account.assert_awaited_once_with("acc-1")  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_account_unbind_delete_failure_keeps_local_binding(tmp_path: Path):
    router, store = _build_router(tmp_path, delete_side_effect=RuntimeError("backend unavailable"))

    replies = await router._cmd_account(event=None, user_id="u1", args=["解绑"])

    assert replies
    assert "解绑失败" in replies[0].text
    assert "已保留本地绑定" in replies[0].text
    assert store.get_bound_account("u1") == "acc-1"
    router.api.delete_account.assert_awaited_once_with("acc-1")  # type: ignore[attr-defined]
