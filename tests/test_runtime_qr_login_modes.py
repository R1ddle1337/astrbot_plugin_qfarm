from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from astrbot_plugin_qfarm.services.runtime.runtime_manager import QFarmRuntimeManager


class _FakeQRLogin:
    def __init__(self) -> None:
        self.create_calls: list[str] = []
        self.check_calls: list[dict[str, Any]] = []

    async def create(self, mode: str = "auto") -> dict[str, str]:
        self.create_calls.append(mode)
        return {
            "code": "bind-code-1",
            "url": "https://h5.qzone.qq.com/qqq/code/bind-code-1?_proxy=1&from=ide",
            "mode": mode,
        }

    async def check(
        self,
        code: str,
        mode: str = "auto",
        *,
        poll_timeout: float | None = None,
        auto_retry: bool = False,
        retry_backoff: float = 1.0,
    ) -> dict[str, Any]:
        self.check_calls.append(
            {
                "code": code,
                "mode": mode,
                "poll_timeout": poll_timeout,
                "auto_retry": auto_retry,
                "retry_backoff": retry_backoff,
            }
        )
        return {"status": "Wait", "mode": mode}


class _LegacyFakeQRLogin:
    def __init__(self) -> None:
        self.create_count = 0
        self.check_codes: list[str] = []

    async def create(self) -> dict[str, str]:
        self.create_count += 1
        return {
            "code": "legacy-code",
            "url": "https://h5.qzone.qq.com/qqq/code/legacy-code?_proxy=1&from=ide",
        }

    async def check(self, code: str) -> dict[str, str]:
        self.check_codes.append(code)
        return {"status": "Wait"}


@pytest.mark.asyncio
async def test_runtime_qr_login_config_is_forwarded(tmp_path: Path):
    manager = QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        qr_login={
            "mode": "miniapp",
            "poll_timeout": 9,
            "auto_retry": True,
            "retry_backoff": 0.25,
        },
        logger=None,
    )
    fake = _FakeQRLogin()
    manager.qr_login = fake

    create_data = await manager.qr_create()
    assert fake.create_calls == ["miniapp"]
    assert Path(str(create_data.get("qrcode") or "")).exists()

    await manager.qr_check("bind-code-1")
    assert len(fake.check_calls) == 1
    call = fake.check_calls[0]
    assert call["mode"] == "miniapp"
    assert float(call["poll_timeout"]) == pytest.approx(9.0)
    assert call["auto_retry"] is True
    assert float(call["retry_backoff"]) == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_runtime_qr_mode_and_retry_can_be_overridden_per_call(tmp_path: Path):
    manager = QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        qr_login={"mode": "miniapp", "poll_timeout": 10, "auto_retry": True, "retry_backoff": 0.2},
        logger=None,
    )
    fake = _FakeQRLogin()
    manager.qr_login = fake

    await manager.qr_create(mode="pc")
    await manager.qr_check(
        "pc-qrsig",
        mode="pc",
        poll_timeout=33,
        auto_retry=False,
        retry_backoff=1.5,
    )

    assert fake.create_calls[-1] == "pc"
    call = fake.check_calls[-1]
    assert call["mode"] == "pc"
    assert float(call["poll_timeout"]) == pytest.approx(33.0)
    assert call["auto_retry"] is False
    assert float(call["retry_backoff"]) == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_runtime_qr_login_legacy_interface_still_works(tmp_path: Path):
    manager = QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        qr_login={"mode": "pc", "poll_timeout": 8, "auto_retry": True, "retry_backoff": 0.3},
        logger=None,
    )
    legacy = _LegacyFakeQRLogin()
    manager.qr_login = legacy

    data = await manager.qr_create(mode="pc")
    assert legacy.create_count == 1
    assert Path(str(data.get("qrcode") or "")).exists()

    result = await manager.qr_check("legacy-code", mode="pc", poll_timeout=5, auto_retry=True, retry_backoff=0.2)
    assert result.get("status") == "Wait"
    assert legacy.check_codes == ["legacy-code"]
