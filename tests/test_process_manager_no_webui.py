from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.process_manager import NodeProcessManager


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 12345
        self.returncode = None
        self.stdout = None
        self.stderr = None

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return int(self.returncode)

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


@pytest.mark.asyncio
async def test_process_manager_injects_no_webui_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    project_root = tmp_path / "qqfarm文档"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "client.js").write_text("// test entry", encoding="utf-8")

    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    manager = NodeProcessManager(
        plugin_root=tmp_path,
        node_command="node",
        service_port=3456,
        service_bind_host="127.0.0.1",
        admin_password="secret",
        disable_webui=True,
        managed_mode=True,
    )
    await manager.start()
    await manager.stop()

    kwargs = captured["kwargs"]  # type: ignore[assignment]
    env = kwargs["env"]  # type: ignore[index]
    assert env["QFARM_ADMIN_PORT"] == "3456"
    assert env["QFARM_ADMIN_HOST"] == "127.0.0.1"
    assert env["QFARM_DISABLE_WEBUI"] == "1"
    assert env["ADMIN_PASSWORD"] == "secret"


@pytest.mark.asyncio
async def test_process_manager_can_enable_webui_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    project_root = tmp_path / "qqfarm文档"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "client.js").write_text("// test entry", encoding="utf-8")

    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    manager = NodeProcessManager(
        plugin_root=tmp_path,
        node_command="node",
        service_port=4567,
        service_bind_host="0.0.0.0",
        disable_webui=False,
        managed_mode=True,
    )
    await manager.start()
    await manager.stop()

    kwargs = captured["kwargs"]  # type: ignore[assignment]
    env = kwargs["env"]  # type: ignore[index]
    assert env["QFARM_ADMIN_HOST"] == "0.0.0.0"
    assert env["QFARM_DISABLE_WEBUI"] == "0"
