from __future__ import annotations

from dataclasses import dataclass

from google.protobuf.message import DecodeError

from .proto import game_pb2

MAX_GATE_MESSAGE_BYTES = 512 * 1024


@dataclass(slots=True)
class GateMeta:
    service_name: str
    method_name: str
    message_type: int
    client_seq: int
    server_seq: int
    error_code: int
    error_message: str


@dataclass(slots=True)
class GateMessage:
    meta: GateMeta
    body: bytes


def encode_request(
    service_name: str,
    method_name: str,
    body: bytes,
    *,
    client_seq: int,
    server_seq: int,
) -> bytes:
    msg = game_pb2.Message(
        meta=game_pb2.Meta(
            service_name=service_name,
            method_name=method_name,
            message_type=game_pb2.Request,
            client_seq=int(client_seq),
            server_seq=int(server_seq),
        ),
        body=body or b"",
    )
    return msg.SerializeToString()


def decode_gate_message(data: bytes) -> GateMessage:
    raw_data = _validate_message_bytes(data, "gate")
    raw = game_pb2.Message()
    try:
        raw.ParseFromString(raw_data)
    except DecodeError as e:
        raise ValueError(f"invalid gate message: {e}") from e
    if raw.meta is None:
        raise ValueError("gate message missing meta")
    meta = GateMeta(
        service_name=raw.meta.service_name,
        method_name=raw.meta.method_name,
        message_type=int(raw.meta.message_type),
        client_seq=int(raw.meta.client_seq),
        server_seq=int(raw.meta.server_seq),
        error_code=int(raw.meta.error_code),
        error_message=raw.meta.error_message,
    )
    return GateMessage(meta=meta, body=bytes(raw.body or b""))


def decode_event_message(data: bytes) -> tuple[str, bytes]:
    raw_data = _validate_message_bytes(data, "event")
    event = game_pb2.EventMessage()
    try:
        event.ParseFromString(raw_data)
    except DecodeError as e:
        raise ValueError(f"invalid event message: {e}") from e
    message_type = str(event.message_type or "").strip()
    if not message_type:
        raise ValueError("event message missing message_type")
    return message_type, bytes(event.body or b"")


def _validate_message_bytes(data: bytes, label: str) -> bytes:
    raw = bytes(data or b"")
    size = len(raw)
    if size > MAX_GATE_MESSAGE_BYTES:
        raise ValueError(f"{label} message too large: {size} > {MAX_GATE_MESSAGE_BYTES}")
    return raw
