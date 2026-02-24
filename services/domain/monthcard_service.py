from __future__ import annotations

from typing import Any

from ..protocol.session import GatewaySession
from ..protocol.proto import mallpb_pb2


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


class MonthCardService:
    def __init__(self, session: GatewaySession, *, rpc_timeout_sec: int = 10) -> None:
        self.session = session
        self.rpc_timeout_sec = max(1, int(rpc_timeout_sec))

    async def get_month_card_infos(self) -> mallpb_pb2.GetMonthCardInfosReply:
        req = mallpb_pb2.GetMonthCardInfosRequest()
        body = await self.session.call(
            "gamepb.mallpb.MallService",
            "GetMonthCardInfos",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = mallpb_pb2.GetMonthCardInfosReply()
        reply.ParseFromString(body)
        return reply

    async def claim_month_card_reward(self, goods_id: int) -> mallpb_pb2.ClaimMonthCardRewardReply:
        req = mallpb_pb2.ClaimMonthCardRewardRequest(goods_id=_to_int(goods_id, 0))
        body = await self.session.call(
            "gamepb.mallpb.MallService",
            "ClaimMonthCardReward",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = mallpb_pb2.ClaimMonthCardRewardReply()
        reply.ParseFromString(body)
        return reply
