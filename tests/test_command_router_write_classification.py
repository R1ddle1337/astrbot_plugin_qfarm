from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_shortcuts_are_write_commands(tmp_path: Path):
    router = _build_router(tmp_path)
    assert router._is_write_command(["login"]) is True
    assert router._is_write_command(["logout"]) is True
    assert router._is_write_command(["start"]) is True
    assert router._is_write_command(["stop"]) is True
    assert router._is_write_command(["reconnect"]) is True
    assert router._is_write_command(["autoall"]) is True


def test_read_commands_still_read_only(tmp_path: Path):
    router = _build_router(tmp_path)
    assert router._is_write_command(["status"]) is False
    assert router._is_write_command(["logs", "50"]) is False
    assert router._is_write_command(["help"]) is False


def test_daily_module_commands_write_classification(tmp_path: Path):
    router = _build_router(tmp_path)
    assert router._is_write_command(["email", "view"]) is False
    assert router._is_write_command(["email", "claim"]) is True
    assert router._is_write_command(["mall", "list"]) is False
    assert router._is_write_command(["mall", "buy", "1002"]) is True
    assert router._is_write_command(["monthcard", "view"]) is False
    assert router._is_write_command(["monthcard", "claim"]) is True
    assert router._is_write_command(["vip", "status"]) is False
    assert router._is_write_command(["vip", "claim"]) is True
    assert router._is_write_command(["share", "status"]) is False
    assert router._is_write_command(["share", "claim"]) is True
