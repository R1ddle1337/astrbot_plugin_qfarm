from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

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
    message_str = "qfarm 状态"

    @staticmethod
    def get_sender_id() -> str:
        return "u1"

    @staticmethod
    def get_group_id() -> str:
        return ""


@pytest.mark.asyncio
async def test_per_user_inflight_limit_blocks_second_inflight_request(tmp_path: Path):
    store = QFarmStateStore(tmp_path, static_allowed_users=["u1"])
    router = QFarmCommandRouter(
        api_client=_FakeApi(),  # type: ignore[arg-type]
        state_store=store,
        rate_limiter=RateLimiter(
            read_cooldown_sec=0.0,
            write_cooldown_sec=0.0,
            global_concurrency=10,
            account_write_serialized=True,
        ),
        process_manager=_DummyProcessManager(),  # type: ignore[arg-type]
        is_super_admin=lambda _: False,
        per_user_inflight_limit=1,
    )

    async def _slow_dispatch(event: Any, user_id: str, tokens: list[str]) -> list[RouterReply]:
        _ = (event, user_id, tokens)
        await asyncio.sleep(0.2)
        return [RouterReply(text="ok")]

    router._dispatch = _slow_dispatch  # type: ignore[method-assign]

    event = _Event()
    task1 = asyncio.create_task(router.handle(event))
    await asyncio.sleep(0.05)
    second = await router.handle(event)

    assert second
    assert "仍在执行中" in second[0].text

    first = await task1
    assert first
    assert first[0].text == "ok"
