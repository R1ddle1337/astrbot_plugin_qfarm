from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.runtime.runtime_manager import QFarmRuntimeManager


class _FakeQRLogin:
    async def create(self) -> dict[str, str]:
        return {
            "code": "bind-code-1",
            "url": "https://h5.qzone.qq.com/qqq/code/bind-code-1?_proxy=1&from=ide",
            "qrcode": "https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=legacy",
        }


@pytest.mark.asyncio
async def test_qr_create_generates_local_png(tmp_path: Path):
    manager = QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        logger=None,
    )
    manager.qr_login = _FakeQRLogin()

    data = await manager.qr_create()

    qr_path = Path(str(data.get("qrcode") or ""))
    assert qr_path.exists()
    assert qr_path.is_file()
    assert qr_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "api.qrserver.com" not in str(data.get("qrcode") or "")
    assert data.get("code") == "bind-code-1"
