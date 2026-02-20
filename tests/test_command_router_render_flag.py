from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from astrbot_plugin_qfarm.services.command_router import QFarmCommandRouter, RouterReply
from astrbot_plugin_qfarm.services.rate_limiter import RateLimiter
from astrbot_plugin_qfarm.services.state_store import QFarmStateStore


class _DummyApi:
    def __init__(self) -> None:
        self.theme_calls: list[str] = []

    async def set_theme(self, theme: str) -> dict[str, Any]:
        self.theme_calls.append(theme)
        return {}


class _DummyProcessManager:
    def status(self) -> dict[str, Any]:
        return {}


def _build_router(tmp_path: Path, api: _DummyApi) -> QFarmCommandRouter:
    return QFarmCommandRouter(
        api_client=api,  # type: ignore[arg-type]
        state_store=QFarmStateStore(tmp_path),
        rate_limiter=RateLimiter(
            read_cooldown_sec=0.0,
            write_cooldown_sec=0.0,
            global_concurrency=10,
            account_write_serialized=True,
        ),
        process_manager=_DummyProcessManager(),  # type: ignore[arg-type]
        is_super_admin=lambda _: False,
    )


def test_mark_render_candidates_only_for_normal_text(tmp_path: Path):
    router = _build_router(tmp_path, _DummyApi())
    replies = [
        RouterReply(text="【农场状态】\n金币: 100"),
        RouterReply(text="用法: qfarm 状态"),
        RouterReply(text="操作失败: 超时"),
        RouterReply(text="权限不足：你不在用户白名单中。"),
        RouterReply(text="写操作过于频繁，请 1.0s 后再试。"),
    ]
    marked = router._mark_render_candidates(replies)
    assert marked[0].prefer_image is True
    assert marked[1].prefer_image is False
    assert marked[2].prefer_image is False
    assert marked[3].prefer_image is False
    assert marked[4].prefer_image is False


@pytest.mark.asyncio
async def test_theme_command_syncs_render_theme(tmp_path: Path):
    api = _DummyApi()
    router = _build_router(tmp_path, api)
    replies = await router._cmd_theme(["dark"])
    assert replies[0].text == "面板主题已更新: dark"
    assert api.theme_calls == ["dark"]
    assert router.state_store.get_render_theme() == "dark"
