from __future__ import annotations

from ..protocol.session import GatewaySession
from ..protocol.proto import qqvippb_pb2


class VipService:
    def __init__(self, session: GatewaySession, *, rpc_timeout_sec: int = 10) -> None:
        self.session = session
        self.rpc_timeout_sec = max(1, int(rpc_timeout_sec))

    async def get_daily_gift_status(self) -> qqvippb_pb2.GetDailyGiftStatusReply:
        req = qqvippb_pb2.GetDailyGiftStatusRequest()
        body = await self.session.call(
            "gamepb.qqvippb.QQVipService",
            "GetDailyGiftStatus",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = qqvippb_pb2.GetDailyGiftStatusReply()
        reply.ParseFromString(body)
        return reply

    async def claim_daily_gift(self) -> qqvippb_pb2.ClaimDailyGiftReply:
        req = qqvippb_pb2.ClaimDailyGiftRequest()
        body = await self.session.call(
            "gamepb.qqvippb.QQVipService",
            "ClaimDailyGift",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = qqvippb_pb2.ClaimDailyGiftReply()
        reply.ParseFromString(body)
        return reply
