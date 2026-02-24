from __future__ import annotations

from typing import Any

from ..protocol.session import GatewaySession
from ..protocol.proto import mallpb_pb2


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


class MallService:
    def __init__(self, session: GatewaySession, *, rpc_timeout_sec: int = 10) -> None:
        self.session = session
        self.rpc_timeout_sec = max(1, int(rpc_timeout_sec))

    async def get_mall_list_by_slot_type(self, slot_type: int = 1) -> mallpb_pb2.GetMallListBySlotTypeResponse:
        req = mallpb_pb2.GetMallListBySlotTypeRequest(slot_type=_to_int(slot_type, 1))
        body = await self.session.call(
            "gamepb.mallpb.MallService",
            "GetMallListBySlotType",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = mallpb_pb2.GetMallListBySlotTypeResponse()
        reply.ParseFromString(body)
        return reply

    async def purchase(self, goods_id: int, count: int = 1) -> mallpb_pb2.PurchaseResponse:
        req = mallpb_pb2.PurchaseRequest(
            goods_id=_to_int(goods_id, 0),
            count=max(1, _to_int(count, 1)),
        )
        body = await self.session.call(
            "gamepb.mallpb.MallService",
            "Purchase",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = mallpb_pb2.PurchaseResponse()
        reply.ParseFromString(body)
        return reply

    async def get_mall_goods_list(self, slot_type: int = 1) -> list[mallpb_pb2.MallGoods]:
        reply = await self.get_mall_list_by_slot_type(slot_type)
        rows: list[mallpb_pb2.MallGoods] = []
        for raw in list(reply.goods_list or []):
            try:
                item = mallpb_pb2.MallGoods()
                item.ParseFromString(raw)
                rows.append(item)
            except Exception:
                continue
        return rows
