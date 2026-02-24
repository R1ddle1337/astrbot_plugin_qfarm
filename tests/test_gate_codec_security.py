from __future__ import annotations

import pytest

from astrbot_plugin_qfarm.services.protocol.gate_codec import (
    MAX_GATE_MESSAGE_BYTES,
    decode_event_message,
    decode_gate_message,
    encode_request,
)


def test_decode_gate_message_rejects_oversized_payload():
    payload = b"x" * (MAX_GATE_MESSAGE_BYTES + 1)
    with pytest.raises(ValueError) as exc:
        decode_gate_message(payload)
    assert "too large" in str(exc.value)


def test_decode_gate_message_rejects_invalid_payload():
    with pytest.raises(ValueError):
        decode_gate_message(b"\x00\x01")


def test_decode_event_message_rejects_oversized_payload():
    payload = b"x" * (MAX_GATE_MESSAGE_BYTES + 1)
    with pytest.raises(ValueError) as exc:
        decode_event_message(payload)
    assert "too large" in str(exc.value)


def test_decode_event_message_roundtrip_ok():
    raw = encode_request("UserService", "Ping", b"{}", client_seq=1, server_seq=0)
    message = decode_gate_message(raw)
    assert message.meta.service_name == "UserService"
    assert message.meta.method_name == "Ping"
