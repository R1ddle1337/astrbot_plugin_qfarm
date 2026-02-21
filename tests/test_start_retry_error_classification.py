from __future__ import annotations

from pathlib import Path

from astrbot_plugin_qfarm.services.runtime.runtime_manager import QFarmRuntimeManager


def _build_manager(tmp_path: Path) -> QFarmRuntimeManager:
    return QFarmRuntimeManager(
        plugin_root=tmp_path,
        data_dir=tmp_path / "data",
        gateway_ws_url="wss://example.invalid/ws",
        client_version="1.0.0",
        logger=None,
    )


def test_invalid_response_status_400_is_normalized_and_non_retryable(tmp_path: Path):
    manager = _build_manager(tmp_path)
    normalized = manager._normalize_start_error(
        "websocket connect failed: 400, message='Invalid response status', url='wss://example'"
    )
    assert "网关鉴权失败(HTTP 400)" in normalized
    assert "重新绑定" in normalized
    assert manager._is_retryable_start_error(normalized) is False


def test_network_disconnect_remains_retryable(tmp_path: Path):
    manager = _build_manager(tmp_path)
    assert manager._is_retryable_start_error("websocket disconnected") is True
