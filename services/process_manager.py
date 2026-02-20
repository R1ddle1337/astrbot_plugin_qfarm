from __future__ import annotations

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
        managed_mode: bool = True,
        logger: Any | None = None,
    ) -> None:
        self.managed_mode = bool(managed_mode)
        self.backend = QFarmRuntimeManager(
            plugin_root=Path(plugin_root),
            data_dir=Path(data_dir),
            gateway_ws_url=gateway_ws_url,
            client_version=client_version,
            platform=platform,
            heartbeat_interval_sec=heartbeat_interval_sec,
            rpc_timeout_sec=rpc_timeout_sec,
            start_retry_max_attempts=start_retry_max_attempts,
            start_retry_base_delay_sec=start_retry_base_delay_sec,
            start_retry_max_delay_sec=start_retry_max_delay_sec,
            auto_start_concurrency=auto_start_concurrency,
            logger=logger,
        )

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
