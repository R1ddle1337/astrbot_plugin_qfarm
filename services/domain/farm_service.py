from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from ..protocol.session import GatewaySession
from ..protocol.proto import plantpb_pb2, shoppb_pb2
from .analytics_service import AnalyticsService
from .config_data import GameConfigData

PHASE_NAMES = {
    0: "未知",
    1: "种子",
    2: "发芽",
    3: "小叶",
    4: "大叶",
    5: "开花",
    6: "成熟",
    7: "枯萎",
}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _to_time_sec(raw: int) -> int:
    n = _to_int(raw, 0)
    if n <= 0:
        return 0
    if n > 1_000_000_000_000:
        return n // 1000
    return n


@dataclass(slots=True)
class LandAnalyzeResult:
    harvestable: list[int]
    growing: list[int]
    empty: list[int]
    dead: list[int]
    need_water: list[int]
    need_weed: list[int]
    need_bug: list[int]
    unlockable: list[int]
    upgradable: list[int]
    lands_detail: list[dict[str, Any]]


class FarmService:
    def __init__(
        self,
        session: GatewaySession,
        config_data: GameConfigData,
        analytics: AnalyticsService,
        *,
        logger: Any | None = None,
        rpc_timeout_sec: int = 10,
    ) -> None:
        self.session = session
        self.config_data = config_data
        self.analytics = analytics
        self.logger = logger
        self.rpc_timeout_sec = max(1, int(rpc_timeout_sec))

    async def get_all_lands(self, host_gid: int = 0) -> plantpb_pb2.AllLandsReply:
        req = plantpb_pb2.AllLandsRequest(host_gid=int(host_gid))
        body = await self.session.call(
            "gamepb.plantpb.PlantService",
            "AllLands",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = plantpb_pb2.AllLandsReply()
        reply.ParseFromString(body)
        return reply

    async def harvest(self, land_ids: list[int], host_gid: int) -> plantpb_pb2.HarvestReply:
        req = plantpb_pb2.HarvestRequest(
            land_ids=[int(v) for v in land_ids],
            host_gid=int(host_gid),
            is_all=True,
        )
        body = await self.session.call(
            "gamepb.plantpb.PlantService",
            "Harvest",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = plantpb_pb2.HarvestReply()
        reply.ParseFromString(body)
        return reply

    async def water(self, land_ids: list[int], host_gid: int) -> plantpb_pb2.WaterLandReply:
        req = plantpb_pb2.WaterLandRequest(land_ids=[int(v) for v in land_ids], host_gid=int(host_gid))
        body = await self.session.call(
            "gamepb.plantpb.PlantService",
            "WaterLand",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = plantpb_pb2.WaterLandReply()
        reply.ParseFromString(body)
        return reply

    async def weed(self, land_ids: list[int], host_gid: int) -> plantpb_pb2.WeedOutReply:
        req = plantpb_pb2.WeedOutRequest(land_ids=[int(v) for v in land_ids], host_gid=int(host_gid))
        body = await self.session.call(
            "gamepb.plantpb.PlantService",
            "WeedOut",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = plantpb_pb2.WeedOutReply()
        reply.ParseFromString(body)
        return reply

    async def bug(self, land_ids: list[int], host_gid: int) -> plantpb_pb2.InsecticideReply:
        req = plantpb_pb2.InsecticideRequest(land_ids=[int(v) for v in land_ids], host_gid=int(host_gid))
        body = await self.session.call(
            "gamepb.plantpb.PlantService",
            "Insecticide",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = plantpb_pb2.InsecticideReply()
        reply.ParseFromString(body)
        return reply

    async def fertilize(self, land_ids: list[int], fertilizer_id: int) -> int:
        ok = 0
        for land_id in land_ids:
            req = plantpb_pb2.FertilizeRequest(
                land_ids=[int(land_id)],
                fertilizer_id=int(fertilizer_id),
            )
            try:
                await self.session.call(
                    "gamepb.plantpb.PlantService",
                    "Fertilize",
                    req.SerializeToString(),
                    timeout_sec=self.rpc_timeout_sec,
                )
                ok += 1
            except Exception:
                break
            if len(land_ids) > 1:
                await asyncio.sleep(0.05)
        return ok

    async def remove_plant(self, land_ids: list[int]) -> None:
        req = plantpb_pb2.RemovePlantRequest(land_ids=[int(v) for v in land_ids])
        await self.session.call(
            "gamepb.plantpb.PlantService",
            "RemovePlant",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )

    async def upgrade_land(self, land_id: int) -> None:
        req = plantpb_pb2.UpgradeLandRequest(land_id=int(land_id))
        await self.session.call(
            "gamepb.plantpb.PlantService",
            "UpgradeLand",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )

    async def unlock_land(self, land_id: int, do_shared: bool = False) -> None:
        req = plantpb_pb2.UnlockLandRequest(land_id=int(land_id), do_shared=bool(do_shared))
        await self.session.call(
            "gamepb.plantpb.PlantService",
            "UnlockLand",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )

    async def plant(self, seed_id: int, land_ids: list[int]) -> int:
        # 保留 Node 特殊编码行为对应的 map 结构，一次一块地发送，兼容服务端限制。
        ok = 0
        for land_id in land_ids:
            req = plantpb_pb2.PlantRequest()
            req.land_and_seed[int(land_id)] = int(seed_id)
            try:
                await self.session.call(
                    "gamepb.plantpb.PlantService",
                    "Plant",
                    req.SerializeToString(),
                    timeout_sec=self.rpc_timeout_sec,
                )
                ok += 1
            except Exception:
                continue
            if len(land_ids) > 1:
                await asyncio.sleep(0.05)
        return ok

    async def get_shop_info(self, shop_id: int = 2) -> shoppb_pb2.ShopInfoReply:
        req = shoppb_pb2.ShopInfoRequest(shop_id=int(shop_id))
        body = await self.session.call(
            "gamepb.shoppb.ShopService",
            "ShopInfo",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = shoppb_pb2.ShopInfoReply()
        reply.ParseFromString(body)
        return reply

    async def buy_goods(self, goods_id: int, num: int, price: int) -> shoppb_pb2.BuyGoodsReply:
        req = shoppb_pb2.BuyGoodsRequest(
            goods_id=int(goods_id),
            num=int(num),
            price=int(price),
        )
        body = await self.session.call(
            "gamepb.shoppb.ShopService",
            "BuyGoods",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = shoppb_pb2.BuyGoodsReply()
        reply.ParseFromString(body)
        return reply

    def analyze_lands(self, lands: list[plantpb_pb2.LandInfo], *, now_sec: int | None = None) -> LandAnalyzeResult:
        now = int(now_sec or time.time())
        harvestable: list[int] = []
        growing: list[int] = []
        empty: list[int] = []
        dead: list[int] = []
        need_water: list[int] = []
        need_weed: list[int] = []
        need_bug: list[int] = []
        unlockable: list[int] = []
        upgradable: list[int] = []
        details: list[dict[str, Any]] = []

        for land in lands:
            land_id = _to_int(land.id, 0)
            level = _to_int(land.level, 0)
            if not bool(land.unlocked):
                details.append(
                    {
                        "id": land_id,
                        "unlocked": False,
                        "status": "locked",
                        "plantName": "",
                        "phaseName": "未解锁",
                        "level": level,
                        "needWater": False,
                        "needWeed": False,
                        "needBug": False,
                    }
                )
                if bool(land.could_unlock):
                    unlockable.append(land_id)
                continue

            if bool(land.could_upgrade):
                upgradable.append(land_id)

            if not land.HasField("plant") or not land.plant.phases:
                details.append(
                    {
                        "id": land_id,
                        "unlocked": True,
                        "status": "empty",
                        "plantName": "",
                        "phaseName": "空地",
                        "level": level,
                        "needWater": False,
                        "needWeed": False,
                        "needBug": False,
                    }
                )
                empty.append(land_id)
                continue

            plant = land.plant
            phase = self._current_phase(plant, now)
            phase_val = _to_int(phase.phase, 0) if phase else 0
            phase_name = PHASE_NAMES.get(phase_val, "未知")
            plant_name = self.config_data.get_plant_name(_to_int(plant.id, 0))
            need_w = _to_int(plant.dry_num, 0) > 0
            need_g = len(list(plant.weed_owners)) > 0
            need_b = len(list(plant.insect_owners)) > 0
            mature_in_sec = 0

            if need_w:
                need_water.append(land_id)
            if need_g:
                need_weed.append(land_id)
            if need_b:
                need_bug.append(land_id)

            # 自有农场成熟判定只看阶段，不能依赖 stealable（该字段用于好友偷菜语义）
            if phase_val == plantpb_pb2.MATURE:
                status = "harvestable"
                harvestable.append(land_id)
            elif phase_val == plantpb_pb2.DEAD:
                status = "dead"
                dead.append(land_id)
            else:
                status = "growing"
                growing.append(land_id)
                mature_in_sec = self._mature_left_sec(plant, now)

            details.append(
                {
                    "id": land_id,
                    "unlocked": True,
                    "status": status,
                    "plantName": plant_name,
                    "phaseName": phase_name,
                    "level": level,
                    "needWater": need_w,
                    "needWeed": need_g,
                    "needBug": need_b,
                    "matureInSec": mature_in_sec,
                }
            )

        return LandAnalyzeResult(
            harvestable=harvestable,
            growing=growing,
            empty=empty,
            dead=dead,
            need_water=need_water,
            need_weed=need_weed,
            need_bug=need_bug,
            unlockable=unlockable,
            upgradable=upgradable,
            lands_detail=details,
        )

    def build_lands_view(self, lands: list[plantpb_pb2.LandInfo]) -> dict[str, Any]:
        analyzed = self.analyze_lands(lands)
        return {
            "lands": analyzed.lands_detail,
            "summary": {
                "harvestable": len(analyzed.harvestable),
                "growing": len(analyzed.growing),
                "empty": len(analyzed.empty),
                "dead": len(analyzed.dead),
                "needWater": len(analyzed.need_water),
                "needWeed": len(analyzed.need_weed),
                "needBug": len(analyzed.need_bug),
            },
        }

    async def get_available_seeds(self, current_level: int) -> list[dict[str, Any]]:
        shop = await self.get_shop_info(2)
        rows: list[dict[str, Any]] = []
        for goods in shop.goods_list:
            seed_id = _to_int(goods.item_id, 0)
            required_level = 0
            for cond in goods.conds:
                if _to_int(cond.type, 0) == shoppb_pb2.MIN_LEVEL:
                    required_level = _to_int(cond.param, 0)
            limit_count = _to_int(goods.limit_count, 0)
            bought_num = _to_int(goods.bought_num, 0)
            rows.append(
                {
                    "seedId": seed_id,
                    "goodsId": _to_int(goods.id, 0),
                    "name": self.config_data.get_plant_name_by_seed(seed_id),
                    "price": _to_int(goods.price, 0),
                    "requiredLevel": required_level,
                    "locked": (not bool(goods.unlocked)) or current_level < required_level,
                    "soldOut": limit_count > 0 and bought_num >= limit_count,
                    "image": self.config_data.get_seed_image(seed_id),
                }
            )
        rows.sort(key=lambda x: (x["requiredLevel"], x["seedId"]))
        return rows

    async def choose_seed(
        self,
        *,
        current_level: int,
        strategy: str,
        preferred_seed_id: int,
    ) -> dict[str, Any] | None:
        seeds = await self.get_available_seeds(current_level)
        available = [s for s in seeds if not s["locked"] and not s["soldOut"]]
        if not available:
            return None

        strategy = str(strategy or "preferred").strip().lower()
        if strategy == "preferred" and preferred_seed_id > 0:
            for row in available:
                if int(row["seedId"]) == int(preferred_seed_id):
                    return row
        if strategy in {"max_exp", "max_fert_exp", "max_profit", "max_fert_profit"}:
            sort_map = {
                "max_exp": "exp",
                "max_fert_exp": "fert",
                "max_profit": "profit",
                "max_fert_profit": "fert_profit",
            }
            ranking = self.analytics.get_plant_rankings(sort_map[strategy])
            seed_map = {int(r["seedId"]): r for r in available}
            for row in ranking:
                seed_id = _to_int(row.get("seedId"), 0)
                if seed_id in seed_map and _to_int(row.get("level"), 0) <= current_level:
                    return seed_map[seed_id]

        available.sort(key=lambda x: (_to_int(x["requiredLevel"]), _to_int(x["seedId"])), reverse=True)
        return available[0]

    @staticmethod
    def _current_phase(plant: plantpb_pb2.PlantInfo, now_sec: int) -> plantpb_pb2.PlantPhaseInfo | None:
        candidate: plantpb_pb2.PlantPhaseInfo | None = None
        candidate_begin = -1
        for phase in plant.phases:
            begin = _to_time_sec(phase.begin_time)
            if begin <= 0:
                continue
            if begin <= now_sec and begin >= candidate_begin:
                candidate = phase
                candidate_begin = begin
        if candidate is not None:
            return candidate
        if plant.phases:
            return plant.phases[0]
        return None

    @staticmethod
    def _mature_left_sec(plant: plantpb_pb2.PlantInfo, now_sec: int) -> int:
        mature_at = 0
        for phase in plant.phases:
            if _to_int(phase.phase, 0) != plantpb_pb2.MATURE:
                continue
            begin = _to_time_sec(phase.begin_time)
            if begin > 0 and (mature_at == 0 or begin < mature_at):
                mature_at = begin
        if mature_at <= 0:
            return 0
        return max(0, mature_at - now_sec)
