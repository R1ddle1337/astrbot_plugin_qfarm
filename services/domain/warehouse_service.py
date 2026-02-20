from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

from ..protocol.session import GatewaySession
from ..protocol.proto import corepb_pb2, itempb_pb2
from .config_data import GameConfigData


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


class WarehouseService:
    SELL_BATCH_SIZE = 15

    def __init__(self, session: GatewaySession, config_data: GameConfigData, *, rpc_timeout_sec: int = 10) -> None:
        self.session = session
        self.config_data = config_data
        self.rpc_timeout_sec = max(1, int(rpc_timeout_sec))

    async def get_bag(self) -> itempb_pb2.BagReply:
        req = itempb_pb2.BagRequest()
        body = await self.session.call(
            "gamepb.itempb.ItemService",
            "Bag",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = itempb_pb2.BagReply()
        reply.ParseFromString(body)
        return reply

    async def sell_items(self, items: list[dict[str, int]]) -> itempb_pb2.SellReply:
        payload = [self._to_sell_item(row) for row in items if _to_int(row.get("count"), 0) > 0]
        req = itempb_pb2.SellRequest(items=payload)
        body = await self.session.call(
            "gamepb.itempb.ItemService",
            "Sell",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = itempb_pb2.SellReply()
        reply.ParseFromString(body)
        return reply

    async def get_bag_detail(self) -> dict[str, Any]:
        reply = await self.get_bag()
        raw_items = self.get_bag_items(reply)
        merged: OrderedDict[int, dict[str, Any]] = OrderedDict()
        for item in raw_items:
            item_id = _to_int(item.id, 0)
            count = _to_int(item.count, 0)
            if item_id <= 0 or count <= 0:
                continue
            row = merged.get(item_id)
            if row is None:
                row = self._build_item_row(item_id)
                row["count"] = 0
                merged[item_id] = row
            row["count"] += count

        items = list(merged.values())
        for row in items:
            interaction_type = str(row.get("interactionType") or "")
            count = _to_int(row.get("count"), 0)
            if interaction_type == "fertilizerbucket" and count > 0:
                hours_floor_1 = int((count / 3600.0) * 10) / 10
                row["hoursText"] = f"{hours_floor_1:.1f}小时"
            else:
                row["hoursText"] = ""
        items.sort(key=lambda x: (-_to_int(x.get("count"), 0), _to_int(x.get("id"), 0)))
        return {"totalKinds": len(items), "items": items}

    async def sell_all_fruits(self) -> dict[str, Any]:
        bag_reply = await self.get_bag()
        raw_items = self.get_bag_items(bag_reply)
        targets = []
        for item in raw_items:
            item_id = _to_int(item.id, 0)
            count = _to_int(item.count, 0)
            uid = _to_int(item.uid, 0)
            if count <= 0 or uid <= 0:
                continue
            if self._is_fruit_item(item_id):
                targets.append({"id": item_id, "count": count, "uid": uid})
        if not targets:
            return {"soldKinds": 0, "goldEarned": 0}

        sold = 0
        gold_total = 0
        for idx in range(0, len(targets), self.SELL_BATCH_SIZE):
            batch = targets[idx : idx + self.SELL_BATCH_SIZE]
            try:
                reply = await self.sell_items(batch)
                sold += len(batch)
                gold_total += self._derive_gold_gain(reply)
            except Exception:
                for row in batch:
                    try:
                        reply = await self.sell_items([row])
                        sold += 1
                        gold_total += self._derive_gold_gain(reply)
                    except Exception:
                        continue
                    await asyncio.sleep(0.1)
            if idx + self.SELL_BATCH_SIZE < len(targets):
                await asyncio.sleep(0.3)
        return {"soldKinds": sold, "goldEarned": max(0, gold_total)}

    async def debug_sell_fruits(self) -> dict[str, Any]:
        before = await self.get_bag_detail()
        sold = await self.sell_all_fruits()
        after = await self.get_bag_detail()
        return {"before": before, "result": sold, "after": after}

    @staticmethod
    def get_bag_items(reply: itempb_pb2.BagReply) -> list[corepb_pb2.Item]:
        if reply.HasField("item_bag"):
            return list(reply.item_bag.items or [])
        return []

    def _build_item_row(self, item_id: int) -> dict[str, Any]:
        item = self.config_data.get_item_by_id(item_id) or {}
        name = str(item.get("name") or "")
        category = "item"
        if item_id in {1, 1001}:
            name = "金币"
            category = "gold"
        elif item_id == 1101:
            name = "经验"
            category = "exp"
        elif self._is_fruit_item(item_id):
            name = name or (self.config_data.get_fruit_name(item_id) + "果实")
            category = "fruit"
        elif self.config_data.get_plant_by_seed(item_id):
            name = name or (self.config_data.get_plant_name_by_seed(item_id) + "种子")
            category = "seed"
        if not name:
            name = f"物品{item_id}"
        return {
            "id": item_id,
            "count": 0,
            "uid": 0,
            "name": name,
            "image": self.config_data.get_seed_image(item_id),
            "category": category,
            "itemType": _to_int(item.get("type"), 0),
            "price": _to_int(item.get("price"), 0),
            "level": _to_int(item.get("level"), 0),
            "interactionType": str(item.get("interaction_type") or ""),
            "hoursText": "",
        }

    def _is_fruit_item(self, item_id: int) -> bool:
        return self.config_data.get_plant_by_fruit(item_id) is not None

    @staticmethod
    def _to_sell_item(row: dict[str, int]) -> corepb_pb2.Item:
        return corepb_pb2.Item(
            id=_to_int(row.get("id"), 0),
            count=_to_int(row.get("count"), 0),
            uid=_to_int(row.get("uid"), 0),
        )

    @staticmethod
    def _derive_gold_gain(reply: itempb_pb2.SellReply) -> int:
        # 协议里通常 get_items 返回本次获得的金币（id=1001）。
        gold = 0
        for item in reply.get_items:
            item_id = _to_int(item.id, 0)
            if item_id in {1, 1001}:
                gold += max(0, _to_int(item.count, 0))
        return gold
