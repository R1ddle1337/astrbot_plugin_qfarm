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
    assert router._is_write_command(["登录"]) is True
    assert router._is_write_command(["退出登录"]) is True
    assert router._is_write_command(["启动"]) is True
    assert router._is_write_command(["停止"]) is True
    assert router._is_write_command(["重连"]) is True
    assert router._is_write_command(["种满"]) is True


def test_read_commands_still_read_only(tmp_path: Path):
    router = _build_router(tmp_path)
    assert router._is_write_command(["状态"]) is False
    assert router._is_write_command(["日志", "50"]) is False
    assert router._is_write_command(["帮助"]) is False
