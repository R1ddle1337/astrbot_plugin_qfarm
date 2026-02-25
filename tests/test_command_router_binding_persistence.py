from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from astrbot_plugin_qfarm.services.command_router import QFarmApiError, QFarmCommandRouter
from astrbot_plugin_qfarm.services.rate_limiter import RateLimiter
from astrbot_plugin_qfarm.services.state_store import QFarmStateStore


class _DummyProcessManager:
    def status(self) -> dict[str, Any]:
        return {}


class _MissingAccountApi:
    async def get_accounts(self) -> dict[str, Any]:
        return {"accounts": []}


def _build_router(tmp_path: Path) -> tuple[QFarmCommandRouter, QFarmStateStore]:
    store = QFarmStateStore(tmp_path)
    store.bind_account("u1", "acc-1", "test")
    router = QFarmCommandRouter(
        api_client=_MissingAccountApi(),  # type: ignore[arg-type]
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
async def test_require_bound_account_keeps_binding_when_backend_account_missing(tmp_path: Path):
    router, store = _build_router(tmp_path)

    with pytest.raises(QFarmApiError, match="已保留本地绑定"):
        await router._require_bound_account("u1")

    assert store.get_bound_account("u1") == "acc-1"


@pytest.mark.asyncio
async def test_cmd_account_view_keeps_binding_when_backend_account_missing(tmp_path: Path):
    router, store = _build_router(tmp_path)

    replies = await router._cmd_account(event=None, user_id="u1", args=["查看"])

    assert replies
    assert "已保留本地绑定" in replies[0].text
    assert store.get_bound_account("u1") == "acc-1"
