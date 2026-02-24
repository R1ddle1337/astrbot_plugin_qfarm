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
    pass


def _build_router(tmp_path: Path) -> QFarmCommandRouter:
    return QFarmCommandRouter(
        api_client=_FakeApi(),  # type: ignore[arg-type]
        state_store=QFarmStateStore(tmp_path),
        rate_limiter=RateLimiter(
            read_cooldown_sec=0.0,
            write_cooldown_sec=0.0,
            global_concurrency=5,
            account_write_serialized=True,
        ),
        process_manager=_DummyProcessManager(),  # type: ignore[arg-type]
        is_super_admin=lambda _: True,
    )


def test_help_brief_returns_module_index(tmp_path: Path):
    router = _build_router(tmp_path)

    text = router._help_text()

    assert "模块索引" in text
    assert "qfarm 帮助 服务" in text
    assert "qfarm 帮助 详细" in text


@pytest.mark.asyncio
async def test_help_verbose_from_dispatch(tmp_path: Path):
    router = _build_router(tmp_path)

    replies = await router._dispatch(event=object(), user_id="u1", tokens=["帮助", "详细"])

    assert replies
    assert "命令总览（详细）" in replies[0].text
    assert "qfarm 状态 [详细]" in replies[0].text


@pytest.mark.asyncio
async def test_help_module_push_from_dispatch(tmp_path: Path):
    router = _build_router(tmp_path)

    replies = await router._dispatch(event=object(), user_id="u1", tokens=["帮助", "推送"])

    assert replies
    assert "【推送模块】" in replies[0].text
    assert "qfarm 推送 测试" in replies[0].text
