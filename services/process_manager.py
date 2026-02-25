from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from .runtime.runtime_manager import QFarmRuntimeManager


class NodeProcessManager:
    """兼容旧命名：当前已改为纯 Python 运行时管理器。"""

    def __init__(
        self,
        *,
        plugin_root: Path,
        data_dir: Path,
        gateway_ws_url: str,
        client_version: str,
        platform: str = "qq",
        heartbeat_interval_sec: int = 25,
        rpc_timeout_sec: int = 10,
        start_retry_max_attempts: int = 3,
        start_retry_base_delay_sec: float = 1.0,
        start_retry_max_delay_sec: float = 8.0,
        auto_start_concurrency: int = 5,
        persist_runtime_logs: bool = True,
        runtime_log_max_entries: int = 3000,
        runtime_log_flush_interval_sec: float = 2.0,
        runtime_log_flush_batch: int = 80,
        qr_login_mode: str = "auto",
        qr_login_poll_timeout_sec: int = 120,
        qr_login_auto_retry_times: int = 1,
        qr_login_retry_backoff_sec: float = 2.0,
        runtime_heartbeat_fail_limit: int = 2,
        automation_friend_error_backoff_sec: float = 5.0,
        default_automation: dict[str, Any] | None = None,
        default_push: dict[str, Any] | None = None,
        managed_mode: bool = True,
        logger: Any | None = None,
    ) -> None:
        self.managed_mode = bool(managed_mode)
        automation_payload = dict(default_automation or {})
        automation_payload.setdefault("friend_error_backoff_sec", float(automation_friend_error_backoff_sec))
        qr_login_payload = {
            "mode": str(qr_login_mode or "auto"),
            "poll_timeout_sec": max(10, int(qr_login_poll_timeout_sec)),
            "auto_retry_times": max(0, int(qr_login_auto_retry_times)),
            "retry_backoff_sec": max(0.1, float(qr_login_retry_backoff_sec)),
        }
        runtime_payload = {
            "heartbeat_fail_limit": max(1, int(runtime_heartbeat_fail_limit)),
        }

        runtime_kwargs = {
            "plugin_root": Path(plugin_root),
            "data_dir": Path(data_dir),
            "gateway_ws_url": gateway_ws_url,
            "client_version": client_version,
            "platform": platform,
            "heartbeat_interval_sec": heartbeat_interval_sec,
            "rpc_timeout_sec": rpc_timeout_sec,
            "start_retry_max_attempts": start_retry_max_attempts,
            "start_retry_base_delay_sec": start_retry_base_delay_sec,
            "start_retry_max_delay_sec": start_retry_max_delay_sec,
            "auto_start_concurrency": auto_start_concurrency,
            "persist_runtime_logs": persist_runtime_logs,
            "runtime_log_max_entries": runtime_log_max_entries,
            "runtime_log_flush_interval_sec": runtime_log_flush_interval_sec,
            "runtime_log_flush_batch": runtime_log_flush_batch,
            "default_automation": automation_payload,
            "default_push": default_push,
            "qr_login": qr_login_payload,
            "qr_login_mode": qr_login_payload["mode"],
            "qr_login_poll_timeout_sec": qr_login_payload["poll_timeout_sec"],
            "qr_login_auto_retry_times": qr_login_payload["auto_retry_times"],
            "qr_login_retry_backoff_sec": qr_login_payload["retry_backoff_sec"],
            "runtime": runtime_payload,
            "runtime_heartbeat_fail_limit": runtime_payload["heartbeat_fail_limit"],
            "automation_friend_error_backoff_sec": automation_payload["friend_error_backoff_sec"],
            "logger": logger,
        }
        accepted = set(inspect.signature(QFarmRuntimeManager.__init__).parameters)
        accepted.discard("self")
        passthrough_kwargs = {key: value for key, value in runtime_kwargs.items() if key in accepted}
        self.backend = QFarmRuntimeManager(**passthrough_kwargs)

    def status(self) -> dict[str, Any]:
        data = self.backend.service_status()
        data["managed_mode"] = self.managed_mode
        return data

    async def start(self) -> None:
        if not self.managed_mode:
            raise RuntimeError("当前为外部服务模式，无法由插件启动。")
        await self.backend.start()

    async def stop(self) -> None:
        await self.backend.stop()

    async def restart(self) -> None:
        if not self.managed_mode:
            raise RuntimeError("当前为外部服务模式，无法由插件重启。")
        await self.backend.restart()
