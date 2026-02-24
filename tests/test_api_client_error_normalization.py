from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.api_client import QFarmApiClient, QFarmApiError
from astrbot_plugin_qfarm.services.runtime.runtime_manager import QFarmRuntimeManager


class _ErrorBackend(QFarmRuntimeManager):
    async def get_accounts(self) -> dict[str, object]:
        raise RuntimeError("后端异常")


class _PushBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    async def get_push_settings(self, account_id: str) -> dict[str, object]:
        self.calls.append(("get", account_id, None))
        return {"enabled": True, "channel": "webhook"}

    async def save_push_settings(self, account_id: str, patch: dict[str, object]) -> dict[str, object]:
        self.calls.append(("save", account_id, dict(patch)))
        return {"ok": True}

    async def send_push_test(self, account_id: str, title: str = "", content: str = "") -> dict[str, object]:
        self.calls.append(("test", account_id, {"title": title, "content": content}))
        return {"ok": True, "message": "sent"}


@pytest.mark.asyncio
async def test_api_client_error_contains_source_hint(tmp_path: Path):
    backend = _ErrorBackend(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        logger=None,
    )
    client = QFarmApiClient(backend)

    with pytest.raises(QFarmApiError) as exc:
        await client.get_accounts()

    message = str(exc.value)
    assert "后端异常" in message
    assert "source=RuntimeError" in message


@pytest.mark.asyncio
async def test_api_client_push_methods_forward_to_backend():
    backend = _PushBackend()
    client = QFarmApiClient(backend=backend)  # type: ignore[arg-type]

    settings = await client.get_push_settings("acc-1")
    saved = await client.save_push_settings("acc-1", {"enabled": False})
    tested = await client.send_push_test("acc-1")

    assert settings == {"enabled": True, "channel": "webhook"}
    assert saved == {"ok": True}
    assert tested == {"ok": True, "message": "sent"}
    assert backend.calls == [
        ("get", "acc-1", None),
        ("save", "acc-1", {"enabled": False}),
        ("test", "acc-1", {"title": "", "content": ""}),
    ]
