from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.runtime.runtime_manager import QFarmRuntimeManager


class _FakeQRLogin:
    def __init__(self, code: str) -> None:
        self.code = code

    async def create(self) -> dict[str, str]:
        return {
            "code": self.code,
            "url": f"https://h5.qzone.qq.com/qqq/code/{self.code}?_proxy=1&from=ide",
        }


@pytest.mark.asyncio
async def test_qr_cache_cleanup_removes_expired_and_keeps_current(tmp_path: Path):
    manager = QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        logger=None,
    )
    manager.qr_login = _FakeQRLogin("bind-cleanup")

    stale_file = manager.qr_cache_dir / "stale.png"
    stale_file.write_bytes(b"\x89PNG\r\n\x1a\nstale")
    old_ts = time.time() - (manager.qr_cache_ttl_sec + 120)
    os.utime(stale_file, (old_ts, old_ts))

    first_data = await manager.qr_create()
    first_path = Path(str(first_data.get("qrcode") or ""))
    assert not stale_file.exists()
    assert first_path.exists()

    second_data = await manager.qr_create()
    second_path = Path(str(second_data.get("qrcode") or ""))
    assert first_path.exists()
    assert second_path.exists()
    assert first_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert second_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
