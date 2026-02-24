from __future__ import annotations

from ..protocol.session import GatewaySession
from ..protocol.proto import sharepb_pb2


class ShareService:
    def __init__(self, session: GatewaySession, *, rpc_timeout_sec: int = 10) -> None:
        self.session = session
        self.rpc_timeout_sec = max(1, int(rpc_timeout_sec))

    async def check_can_share(self) -> sharepb_pb2.CheckCanShareReply:
        req = sharepb_pb2.CheckCanShareRequest()
        body = await self.session.call(
            "gamepb.sharepb.ShareService",
            "CheckCanShare",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = sharepb_pb2.CheckCanShareReply()
        reply.ParseFromString(body)
        return reply

    async def report_share(self, shared: bool = True) -> sharepb_pb2.ReportShareReply:
        req = sharepb_pb2.ReportShareRequest(shared=bool(shared))
        body = await self.session.call(
            "gamepb.sharepb.ShareService",
            "ReportShare",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = sharepb_pb2.ReportShareReply()
        reply.ParseFromString(body)
        return reply

    async def claim_share_reward(self, claimed: bool = True) -> sharepb_pb2.ClaimShareRewardReply:
        req = sharepb_pb2.ClaimShareRewardRequest(claimed=bool(claimed))
        body = await self.session.call(
            "gamepb.sharepb.ShareService",
            "ClaimShareReward",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = sharepb_pb2.ClaimShareRewardReply()
        reply.ParseFromString(body)
        return reply
