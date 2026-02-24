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
        self.last_logs_call: dict[str, Any] | None = None

    async def get_accounts(self) -> dict[str, Any]:
        return {
            "accounts": [
                {"id": "acc-1", "name": "test", "platform": "qq", "qq": "10001"},
            ]
        }

    async def get_logs(
        self,
        account_id: str,
        *,
        limit: int = 50,
        module: str = "",
        event: str = "",
        keyword: str = "",
        isWarn: str = "",
        timeFrom: str = "",
        timeTo: str = "",
    ) -> list[dict[str, Any]]:
        self.last_logs_call = {
            "account_id": account_id,
            "limit": limit,
            "module": module,
            "event": event,
            "keyword": keyword,
            "isWarn": isWarn,
            "timeFrom": timeFrom,
            "timeTo": timeTo,
        }
        return [
            {"time": "2026-02-24 00:00:00", "msg": "daily summary", "tag": "task/daily_summary", "isWarn": False},
            {"time": "2026-02-24 00:00:01", "msg": "failed", "tag": "push/deliver", "isWarn": True},
        ]


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
async def test_logs_default_warn_first_with_limit_20(tmp_path: Path):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    replies = await router._cmd_logs("u1", [])

    assert replies
    assert api.last_logs_call is not None
    assert api.last_logs_call["limit"] == 20
    assert api.last_logs_call["isWarn"] == "1"
    assert "(isWarn=1)" in replies[0].text


@pytest.mark.asyncio
async def test_logs_verbose_defaults_to_limit_50_full_level(tmp_path: Path):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    replies = await router._cmd_logs("u1", ["详细"])

    assert replies
    assert api.last_logs_call is not None
    assert api.last_logs_call["limit"] == 50
    assert api.last_logs_call["isWarn"] == ""
    assert "(isWarn=1)" not in replies[0].text


@pytest.mark.asyncio
async def test_logs_explicit_options_override_defaults(tmp_path: Path):
    api = _FakeApi()
    router = _build_router(tmp_path, api)

    await router._cmd_logs("u1", ["30", "module=task", "event=daily_summary", "isWarn=0"])

    assert api.last_logs_call is not None
    assert api.last_logs_call["limit"] == 30
    assert api.last_logs_call["module"] == "task"
    assert api.last_logs_call["event"] == "daily_summary"
    assert api.last_logs_call["isWarn"] == "0"
