from __future__ import annotations

import asyncio

import pytest

from astrbot_plugin_qfarm.services.api_client import QFarmApiClient, QFarmApiError


class _ErrorBackend:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def get_accounts(self) -> dict[str, object]:
        raise self.error


class _SlowBackend:
    async def get_accounts(self) -> dict[str, object]:
        await asyncio.sleep(1.2)
        return {"accounts": []}


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
async def test_api_client_error_contains_source_and_general_code():
    client = QFarmApiClient(backend=_ErrorBackend(RuntimeError("后端异常")))  # type: ignore[arg-type]

    with pytest.raises(QFarmApiError) as exc:
        await client.get_accounts()

    err = exc.value
    assert err.code == "general"
    assert err.source == "RuntimeError"
    assert "后端异常" in str(err)
    assert "source=RuntimeError" in str(err)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_error", "expected_code"),
    [
        (RuntimeError("runtime not ready"), "runtime_not_ready"),
        (ConnectionError("session disconnected"), "session_disconnected"),
        (RuntimeError("扫码登录超时"), "qr_timeout"),
        (RuntimeError("网关鉴权失败(HTTP 400)"), "auth_invalid"),
        (TimeoutError("timed out"), "timeout"),
    ],
)
async def test_api_client_classifies_error_codes(raw_error: Exception, expected_code: str):
    client = QFarmApiClient(backend=_ErrorBackend(raw_error))  # type: ignore[arg-type]

    with pytest.raises(QFarmApiError) as exc:
        await client.get_accounts()

    assert exc.value.code == expected_code


@pytest.mark.asyncio
async def test_api_client_timeout_error_contains_timeout_code_and_source():
    client = QFarmApiClient(backend=_SlowBackend(), request_timeout_sec=1)  # type: ignore[arg-type]

    with pytest.raises(QFarmApiError) as exc:
        await client.get_accounts()

    err = exc.value
    assert err.code == "timeout"
    assert err.source == "TimeoutError"
    assert "请求超时" in str(err)


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
