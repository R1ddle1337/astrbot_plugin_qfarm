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


def test_error_template_keeps_prefix_and_adds_code(tmp_path: Path):
    router = _build_router(tmp_path)
    text = router._format_failure_message("操作失败", "请求超时(15s)，请稍后重试。")
    assert text.startswith("操作失败:")
    assert "[E_TIMEOUT]" in text


def test_error_template_internal_uses_internal_code(tmp_path: Path):
    router = _build_router(tmp_path)
    text = router._format_failure_message("命令执行异常", "boom", internal=True)
    assert text.startswith("命令执行异常:")
    assert "[E_INTERNAL]" in text
