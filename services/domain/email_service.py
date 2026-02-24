from __future__ import annotations

from typing import Any

from ..protocol.session import GatewaySession
from ..protocol.proto import emailpb_pb2


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


class EmailService:
    def __init__(self, session: GatewaySession, *, rpc_timeout_sec: int = 10) -> None:
        self.session = session
        self.rpc_timeout_sec = max(1, int(rpc_timeout_sec))

    async def get_email_list(self, box_type: int = 1) -> emailpb_pb2.GetEmailListReply:
        req = emailpb_pb2.GetEmailListRequest(box_type=_to_int(box_type, 1))
        body = await self.session.call(
            "gamepb.emailpb.EmailService",
            "GetEmailList",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = emailpb_pb2.GetEmailListReply()
        reply.ParseFromString(body)
        return reply

    async def claim_email(self, box_type: int = 1, email_id: str = "") -> emailpb_pb2.ClaimEmailReply:
        req = emailpb_pb2.ClaimEmailRequest(
            box_type=_to_int(box_type, 1),
            email_id=str(email_id or ""),
        )
        body = await self.session.call(
            "gamepb.emailpb.EmailService",
            "ClaimEmail",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = emailpb_pb2.ClaimEmailReply()
        reply.ParseFromString(body)
        return reply

    async def batch_claim_email(self, box_type: int = 1, email_id: str = "") -> emailpb_pb2.BatchClaimEmailReply:
        req = emailpb_pb2.BatchClaimEmailRequest(
            box_type=_to_int(box_type, 1),
            email_id=str(email_id or ""),
        )
        body = await self.session.call(
            "gamepb.emailpb.EmailService",
            "BatchClaimEmail",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = emailpb_pb2.BatchClaimEmailReply()
        reply.ParseFromString(body)
        return reply
