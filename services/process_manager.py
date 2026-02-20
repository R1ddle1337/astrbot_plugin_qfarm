from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any


class NodeProcessManager:
    """托管 qqfarm Node 服务进程。"""

    def __init__(
        self,
        plugin_root: Path,
        node_command: str = "node",
        service_port: int = 3000,
        service_bind_host: str = "127.0.0.1",
        admin_password: str = "",
        disable_webui: bool = True,
        managed_mode: bool = True,
        logger: Any | None = None,
    ) -> None:
        self.plugin_root = Path(plugin_root)
        self.project_root = self.plugin_root / "qqfarm文档"
        self.node_command = str(node_command or "node").strip() or "node"
        self.service_port = int(service_port)
        self.service_bind_host = str(service_bind_host or "127.0.0.1").strip() or "127.0.0.1"
        self.admin_password = str(admin_password or "")
        self.disable_webui = bool(disable_webui)
        self.managed_mode = bool(managed_mode)
        self.logger = logger

        self._process: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._wait_task: asyncio.Task | None = None

    def is_running(self) -> bool:
        if self._process is None:
            return False
        return self._process.returncode is None

    def status(self) -> dict[str, Any]:
        running = self.is_running()
        return {
            "managed_mode": self.managed_mode,
            "running": running,
            "pid": self._process.pid if running and self._process else None,
            "returncode": self._process.returncode if self._process else None,
            "port": self.service_port,
            "bind_host": self.service_bind_host,
            "disable_webui": self.disable_webui,
            "project_root": str(self.project_root),
        }

    async def start(self) -> None:
        if not self.managed_mode:
            raise RuntimeError("当前为外部服务模式，不能由插件启动。")
        if self.is_running():
            return

        client_entry = self.project_root / "client.js"
        if not client_entry.exists():
            raise RuntimeError(f"未找到 Node 入口文件: {client_entry}")

        env = os.environ.copy()
        env["QFARM_ADMIN_PORT"] = str(self.service_port)
        env["QFARM_ADMIN_HOST"] = self.service_bind_host
        env["QFARM_DISABLE_WEBUI"] = "1" if self.disable_webui else "0"
        env["PORT"] = str(self.service_port)
        if self.admin_password:
            env["ADMIN_PASSWORD"] = self.admin_password

        self._process = await asyncio.create_subprocess_exec(
            self.node_command,
            "client.js",
            cwd=str(self.project_root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._log_info(f"qfarm Node 服务已启动，PID={self._process.pid}")
        self._stdout_task = asyncio.create_task(self._stream_output(self._process.stdout, "stdout"))
        self._stderr_task = asyncio.create_task(self._stream_output(self._process.stderr, "stderr"))
        self._wait_task = asyncio.create_task(self._wait_exit())

    async def stop(self) -> None:
        process = self._process
        if process is None:
            return

        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=8.0)
            except asyncio.TimeoutError:
                self._log_warning("qfarm Node 服务停止超时，执行强制 kill。")
                process.kill()
                await process.wait()

        await self._cleanup_tasks()
        self._log_info("qfarm Node 服务已停止。")

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _wait_exit(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            code = await process.wait()
            self._log_warning(f"qfarm Node 服务退出，code={code}")
        except Exception as e:
            self._log_warning(f"qfarm Node 退出监听异常: {e}")

    async def _stream_output(self, stream: asyncio.StreamReader | None, tag: str) -> None:
        if stream is None:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="ignore").rstrip()
                if text:
                    self._log_info(f"[qfarm-node:{tag}] {text}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log_warning(f"读取 qfarm-node {tag} 日志失败: {e}")

    async def _cleanup_tasks(self) -> None:
        for task in (self._stdout_task, self._stderr_task, self._wait_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
        self._stdout_task = None
        self._stderr_task = None
        self._wait_task = None
        self._process = None

    def _log_info(self, message: str) -> None:
        if self.logger and hasattr(self.logger, "info"):
            self.logger.info(message)
        else:
            print(message)

    def _log_warning(self, message: str) -> None:
        if self.logger and hasattr(self.logger, "warning"):
            self.logger.warning(message)
        elif self.logger and hasattr(self.logger, "warn"):
            self.logger.warn(message)
        else:
            print(message)
