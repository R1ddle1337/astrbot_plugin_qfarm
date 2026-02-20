from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..protocol.session import GatewaySession
from ..protocol.proto import friendpb_pb2, plantpb_pb2, visitpb_pb2
from .config_data import GameConfigData


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


class FriendService:
    OP_NAMES = {
        10001: "收获",
        10002: "铲除",
        10003: "放草",
        10004: "放虫",
        10005: "除草",
        10006: "除虫",
        10007: "浇水",
        10008: "偷菜",
    }

    def __init__(
        self,
        session: GatewaySession,
        config_data: GameConfigData,
        *,
        rpc_timeout_sec: int = 10,
    ) -> None:
        self.session = session
        self.config_data = config_data
        self.rpc_timeout_sec = max(1, int(rpc_timeout_sec))
        self._operation_limits: dict[int, dict[str, int]] = {}
        self._last_reset_day = ""

    async def get_all_friends(self) -> friendpb_pb2.GetAllReply:
        req = friendpb_pb2.GetAllRequest()
        body = await self.session.call(
            "gamepb.friendpb.FriendService",
            "GetAll",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = friendpb_pb2.GetAllReply()
        reply.ParseFromString(body)
        return reply

    async def get_applications(self) -> friendpb_pb2.GetApplicationsReply:
        req = friendpb_pb2.GetApplicationsRequest()
        body = await self.session.call(
            "gamepb.friendpb.FriendService",
            "GetApplications",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = friendpb_pb2.GetApplicationsReply()
        reply.ParseFromString(body)
        return reply

    async def accept_friends(self, gids: list[int]) -> friendpb_pb2.AcceptFriendsReply:
        req = friendpb_pb2.AcceptFriendsRequest(friend_gids=[_to_int(g, 0) for g in gids if _to_int(g, 0) > 0])
        body = await self.session.call(
            "gamepb.friendpb.FriendService",
            "AcceptFriends",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = friendpb_pb2.AcceptFriendsReply()
        reply.ParseFromString(body)
        return reply

    async def enter_friend_farm(self, friend_gid: int) -> visitpb_pb2.EnterReply:
        req = visitpb_pb2.EnterRequest(
            host_gid=_to_int(friend_gid, 0),
            reason=visitpb_pb2.ENTER_REASON_FRIEND,
        )
        body = await self.session.call(
            "gamepb.visitpb.VisitService",
            "Enter",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = visitpb_pb2.EnterReply()
        reply.ParseFromString(body)
        return reply

    async def leave_friend_farm(self, friend_gid: int) -> None:
        req = visitpb_pb2.LeaveRequest(host_gid=_to_int(friend_gid, 0))
        try:
            await self.session.call(
                "gamepb.visitpb.VisitService",
                "Leave",
                req.SerializeToString(),
                timeout_sec=self.rpc_timeout_sec,
            )
        except Exception:
            return

    async def help_water(self, friend_gid: int, land_ids: list[int]) -> plantpb_pb2.WaterLandReply:
        req = plantpb_pb2.WaterLandRequest(
            land_ids=[_to_int(v, 0) for v in land_ids if _to_int(v, 0) > 0],
            host_gid=_to_int(friend_gid, 0),
        )
        body = await self.session.call(
            "gamepb.plantpb.PlantService",
            "WaterLand",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = plantpb_pb2.WaterLandReply()
        reply.ParseFromString(body)
        self.update_operation_limits(reply.operation_limits)
        return reply

    async def help_weed(self, friend_gid: int, land_ids: list[int]) -> plantpb_pb2.WeedOutReply:
        req = plantpb_pb2.WeedOutRequest(
            land_ids=[_to_int(v, 0) for v in land_ids if _to_int(v, 0) > 0],
            host_gid=_to_int(friend_gid, 0),
        )
        body = await self.session.call(
            "gamepb.plantpb.PlantService",
            "WeedOut",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = plantpb_pb2.WeedOutReply()
        reply.ParseFromString(body)
        self.update_operation_limits(reply.operation_limits)
        return reply

    async def help_bug(self, friend_gid: int, land_ids: list[int]) -> plantpb_pb2.InsecticideReply:
        req = plantpb_pb2.InsecticideRequest(
            land_ids=[_to_int(v, 0) for v in land_ids if _to_int(v, 0) > 0],
            host_gid=_to_int(friend_gid, 0),
        )
        body = await self.session.call(
            "gamepb.plantpb.PlantService",
            "Insecticide",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = plantpb_pb2.InsecticideReply()
        reply.ParseFromString(body)
        self.update_operation_limits(reply.operation_limits)
        return reply

    async def steal_harvest(self, friend_gid: int, land_ids: list[int]) -> plantpb_pb2.HarvestReply:
        req = plantpb_pb2.HarvestRequest(
            land_ids=[_to_int(v, 0) for v in land_ids if _to_int(v, 0) > 0],
            host_gid=_to_int(friend_gid, 0),
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
        self.update_operation_limits(reply.operation_limits)
        return reply

    async def put_insects(self, friend_gid: int, land_ids: list[int]) -> int:
        ok = 0
        for land_id in land_ids:
            req = plantpb_pb2.PutInsectsRequest(
                host_gid=_to_int(friend_gid, 0),
                land_ids=[_to_int(land_id, 0)],
            )
            try:
                body = await self.session.call(
                    "gamepb.plantpb.PlantService",
                    "PutInsects",
                    req.SerializeToString(),
                    timeout_sec=self.rpc_timeout_sec,
                )
                reply = plantpb_pb2.PutInsectsReply()
                reply.ParseFromString(body)
                self.update_operation_limits(reply.operation_limits)
                ok += 1
            except Exception:
                continue
            await asyncio.sleep(0.1)
        return ok

    async def put_weeds(self, friend_gid: int, land_ids: list[int]) -> int:
        ok = 0
        for land_id in land_ids:
            req = plantpb_pb2.PutWeedsRequest(
                host_gid=_to_int(friend_gid, 0),
                land_ids=[_to_int(land_id, 0)],
            )
            try:
                body = await self.session.call(
                    "gamepb.plantpb.PlantService",
                    "PutWeeds",
                    req.SerializeToString(),
                    timeout_sec=self.rpc_timeout_sec,
                )
                reply = plantpb_pb2.PutWeedsReply()
                reply.ParseFromString(body)
                self.update_operation_limits(reply.operation_limits)
                ok += 1
            except Exception:
                continue
            await asyncio.sleep(0.1)
        return ok

    async def check_can_operate_remote(self, friend_gid: int, operation_id: int) -> tuple[bool, int]:
        req = plantpb_pb2.CheckCanOperateRequest(
            host_gid=_to_int(friend_gid, 0),
            operation_id=_to_int(operation_id, 0),
        )
        try:
            body = await self.session.call(
                "gamepb.plantpb.PlantService",
                "CheckCanOperate",
                req.SerializeToString(),
                timeout_sec=self.rpc_timeout_sec,
            )
            reply = plantpb_pb2.CheckCanOperateReply()
            reply.ParseFromString(body)
            return bool(reply.can_operate), _to_int(reply.can_steal_num, 0)
        except Exception:
            return True, 0

    def update_operation_limits(self, limits: list[plantpb_pb2.OperationLimit]) -> None:
        if not limits:
            return
        self._check_daily_reset()
        for limit in limits:
            op_id = _to_int(limit.id, 0)
            if op_id <= 0:
                continue
            self._operation_limits[op_id] = {
                "dayTimes": _to_int(limit.day_times, 0),
                "dayTimesLimit": _to_int(limit.day_times_lt, 0),
                "dayExpTimes": _to_int(limit.day_exp_times, 0),
                "dayExpTimesLimit": _to_int(limit.day_ex_times_lt, 0),
            }

    def get_operation_limits(self) -> dict[int, dict[str, int | str]]:
        self._check_daily_reset()
        result: dict[int, dict[str, int | str]] = {}
        for op_id, row in self._operation_limits.items():
            result[op_id] = {
                "name": self.OP_NAMES.get(op_id, f"#{op_id}"),
                "dayTimes": row["dayTimes"],
                "dayTimesLimit": row["dayTimesLimit"],
                "dayExpTimes": row["dayExpTimes"],
                "dayExpTimesLimit": row["dayExpTimesLimit"],
                "remaining": self.get_remaining_times(op_id),
            }
        return result

    def can_get_exp(self, operation_id: int) -> bool:
        self._check_daily_reset()
        row = self._operation_limits.get(_to_int(operation_id, 0))
        if not row:
            return False
        if row["dayExpTimesLimit"] <= 0:
            return True
        return row["dayExpTimes"] < row["dayExpTimesLimit"]

    def can_operate(self, operation_id: int) -> bool:
        self._check_daily_reset()
        row = self._operation_limits.get(_to_int(operation_id, 0))
        if not row:
            return True
        if row["dayTimesLimit"] <= 0:
            return True
        return row["dayTimes"] < row["dayTimesLimit"]

    def get_remaining_times(self, operation_id: int) -> int:
        self._check_daily_reset()
        row = self._operation_limits.get(_to_int(operation_id, 0))
        if not row or row["dayTimesLimit"] <= 0:
            return 999
        return max(0, row["dayTimesLimit"] - row["dayTimes"])

    def analyze_friend_lands(self, lands: list[plantpb_pb2.LandInfo], my_gid: int) -> dict[str, list[Any]]:
        result: dict[str, list[Any]] = {
            "stealable": [],
            "stealableInfo": [],
            "needWater": [],
            "needWeed": [],
            "needBug": [],
            "canPutWeed": [],
            "canPutBug": [],
        }

        for land in lands:
            land_id = _to_int(land.id, 0)
            if not land.HasField("plant") or not land.plant.phases:
                continue
            plant = land.plant
            phase = self._current_phase(plant)
            phase_val = _to_int(phase.phase, 0) if phase else 0

            if phase_val == plantpb_pb2.MATURE:
                if bool(plant.stealable):
                    result["stealable"].append(land_id)
                    plant_id = _to_int(plant.id, 0)
                    result["stealableInfo"].append(
                        {
                            "landId": land_id,
                            "plantId": plant_id,
                            "name": self.config_data.get_plant_name(plant_id),
                        }
                    )
                continue

            if phase_val == plantpb_pb2.DEAD:
                continue

            if _to_int(plant.dry_num, 0) > 0:
                result["needWater"].append(land_id)

            weed_owners = list(plant.weed_owners or [])
            bug_owners = list(plant.insect_owners or [])
            if weed_owners:
                result["needWeed"].append(land_id)
            if bug_owners:
                result["needBug"].append(land_id)

            i_put_weed = any(_to_int(v, 0) == _to_int(my_gid, 0) for v in weed_owners)
            i_put_bug = any(_to_int(v, 0) == _to_int(my_gid, 0) for v in bug_owners)
            if len(weed_owners) < 2 and not i_put_weed:
                result["canPutWeed"].append(land_id)
            if len(bug_owners) < 2 and not i_put_bug:
                result["canPutBug"].append(land_id)

        return result

    async def get_friends_list(self, my_gid: int) -> list[dict[str, Any]]:
        try:
            reply = await self.get_all_friends()
        except Exception:
            return []
        rows: list[dict[str, Any]] = []
        for friend in reply.game_friends:
            gid = _to_int(friend.gid, 0)
            if gid <= 0 or gid == _to_int(my_gid, 0):
                continue
            name = str(friend.remark or friend.name or f"GID:{gid}")
            if name == "小小农夫":
                continue
            rows.append(
                {
                    "gid": gid,
                    "name": name,
                    "plant": {
                        "stealNum": _to_int(friend.plant.steal_plant_num, 0) if friend.HasField("plant") else 0,
                        "dryNum": _to_int(friend.plant.dry_num, 0) if friend.HasField("plant") else 0,
                        "weedNum": _to_int(friend.plant.weed_num, 0) if friend.HasField("plant") else 0,
                        "insectNum": _to_int(friend.plant.insect_num, 0) if friend.HasField("plant") else 0,
                    },
                }
            )
        rows.sort(key=lambda x: (str(x.get("name") or ""), _to_int(x.get("gid"), 0)))
        return rows

    async def get_friend_lands_detail(self, friend_gid: int, my_gid: int) -> dict[str, Any]:
        enter = await self.enter_friend_farm(friend_gid)
        lands = list(enter.lands or [])
        analyzed = self.analyze_friend_lands(lands, my_gid=my_gid)
        now_sec = int(time.time())
        rows: list[dict[str, Any]] = []
        try:
            for land in lands:
                land_id = _to_int(land.id, 0)
                level = _to_int(land.level, 0)
                if not bool(land.unlocked):
                    rows.append(
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
                    continue
                if not land.HasField("plant") or not land.plant.phases:
                    rows.append(
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
                    continue
                plant = land.plant
                phase = self._current_phase(plant)
                phase_val = _to_int(phase.phase, 0) if phase else 0
                phase_name = self._phase_name(phase_val)
                plant_id = _to_int(plant.id, 0)
                seed_id = _to_int((self.config_data.get_plant_by_id(plant_id) or {}).get("seed_id"), 0)
                mature_at = 0
                for item in plant.phases:
                    if _to_int(item.phase, 0) == plantpb_pb2.MATURE:
                        begin = _to_time_sec(item.begin_time)
                        if begin > 0 and (mature_at == 0 or begin < mature_at):
                            mature_at = begin
                mature_in_sec = max(0, mature_at - now_sec) if mature_at > 0 else 0
                if phase_val == plantpb_pb2.MATURE:
                    status = "stealable" if bool(plant.stealable) else "harvested"
                elif phase_val == plantpb_pb2.DEAD:
                    status = "dead"
                else:
                    status = "growing"
                rows.append(
                    {
                        "id": land_id,
                        "unlocked": True,
                        "status": status,
                        "plantName": self.config_data.get_plant_name(plant_id),
                        "seedId": seed_id,
                        "seedImage": self.config_data.get_seed_image(seed_id),
                        "phaseName": phase_name,
                        "level": level,
                        "matureInSec": mature_in_sec,
                        "needWater": _to_int(plant.dry_num, 0) > 0,
                        "needWeed": len(list(plant.weed_owners or [])) > 0,
                        "needBug": len(list(plant.insect_owners or [])) > 0,
                    }
                )
            return {"lands": rows, "summary": analyzed}
        finally:
            await self.leave_friend_farm(friend_gid)

    async def do_friend_operation(
        self,
        friend_gid: int,
        op_type: str,
        *,
        my_gid: int,
        on_after_steal: Callable[[], Awaitable[None] | None] | None = None,
    ) -> dict[str, Any]:
        gid = _to_int(friend_gid, 0)
        if gid <= 0:
            return {"ok": False, "opType": op_type, "count": 0, "message": "无效好友ID"}
        op = str(op_type or "").strip().lower()
        try:
            enter = await self.enter_friend_farm(gid)
        except Exception as e:
            return {"ok": False, "opType": op, "count": 0, "message": f"进入好友农场失败: {e}"}
        try:
            analyzed = self.analyze_friend_lands(list(enter.lands or []), my_gid=my_gid)
            if op == "steal":
                targets = list(analyzed["stealable"])
                if not targets:
                    return {"ok": True, "opType": op, "count": 0, "message": "没有可偷取土地"}
                can_operate, can_num = await self.check_can_operate_remote(gid, 10008)
                if not can_operate:
                    return {"ok": True, "opType": op, "count": 0, "message": "今日偷菜次数已用完"}
                if can_num > 0:
                    targets = targets[:can_num]
                count = await self._run_batch_with_fallback(
                    targets,
                    batch_fn=lambda ids: self.steal_harvest(gid, ids),
                    single_fn=lambda ids: self.steal_harvest(gid, ids),
                )
                if count > 0 and on_after_steal:
                    ret = on_after_steal()
                    if asyncio.iscoroutine(ret):
                        await ret
                return {"ok": True, "opType": op, "count": count, "message": f"偷取完成 {count} 块"}

            if op == "water":
                targets = list(analyzed["needWater"])
                if not targets:
                    return {"ok": True, "opType": op, "count": 0, "message": "没有可浇水土地"}
                can_operate, _ = await self.check_can_operate_remote(gid, 10007)
                if not can_operate:
                    return {"ok": True, "opType": op, "count": 0, "message": "今日浇水次数已用完"}
                count = await self._run_batch_with_fallback(
                    targets,
                    batch_fn=lambda ids: self.help_water(gid, ids),
                    single_fn=lambda ids: self.help_water(gid, ids),
                )
                return {"ok": True, "opType": op, "count": count, "message": f"浇水完成 {count} 块"}

            if op == "weed":
                targets = list(analyzed["needWeed"])
                if not targets:
                    return {"ok": True, "opType": op, "count": 0, "message": "没有可除草土地"}
                can_operate, _ = await self.check_can_operate_remote(gid, 10005)
                if not can_operate:
                    return {"ok": True, "opType": op, "count": 0, "message": "今日除草次数已用完"}
                count = await self._run_batch_with_fallback(
                    targets,
                    batch_fn=lambda ids: self.help_weed(gid, ids),
                    single_fn=lambda ids: self.help_weed(gid, ids),
                )
                return {"ok": True, "opType": op, "count": count, "message": f"除草完成 {count} 块"}

            if op == "bug":
                targets = list(analyzed["needBug"])
                if not targets:
                    return {"ok": True, "opType": op, "count": 0, "message": "没有可除虫土地"}
                can_operate, _ = await self.check_can_operate_remote(gid, 10006)
                if not can_operate:
                    return {"ok": True, "opType": op, "count": 0, "message": "今日除虫次数已用完"}
                count = await self._run_batch_with_fallback(
                    targets,
                    batch_fn=lambda ids: self.help_bug(gid, ids),
                    single_fn=lambda ids: self.help_bug(gid, ids),
                )
                return {"ok": True, "opType": op, "count": count, "message": f"除虫完成 {count} 块"}

            if op == "bad":
                bug_count = 0
                weed_count = 0
                if analyzed["canPutBug"]:
                    bug_count = await self.put_insects(gid, list(analyzed["canPutBug"]))
                if analyzed["canPutWeed"]:
                    weed_count = await self.put_weeds(gid, list(analyzed["canPutWeed"]))
                total = bug_count + weed_count
                if total <= 0:
                    return {
                        "ok": True,
                        "opType": op,
                        "count": 0,
                        "bugCount": 0,
                        "weedCount": 0,
                        "message": "没有可捣乱土地或次数已用完",
                    }
                return {
                    "ok": True,
                    "opType": op,
                    "count": total,
                    "bugCount": bug_count,
                    "weedCount": weed_count,
                    "message": f"捣乱完成 虫{bug_count}/草{weed_count}",
                }

            return {"ok": False, "opType": op, "count": 0, "message": "未知操作类型"}
        except Exception as e:
            return {"ok": False, "opType": op, "count": 0, "message": str(e)}
        finally:
            await self.leave_friend_farm(gid)

    async def _run_batch_with_fallback(
        self,
        land_ids: list[int],
        *,
        batch_fn: Callable[[list[int]], Awaitable[Any]],
        single_fn: Callable[[list[int]], Awaitable[Any]],
    ) -> int:
        targets = [v for v in land_ids if _to_int(v, 0) > 0]
        if not targets:
            return 0
        try:
            await batch_fn(targets)
            return len(targets)
        except Exception:
            ok = 0
            for land_id in targets:
                try:
                    await single_fn([land_id])
                    ok += 1
                except Exception:
                    continue
                await asyncio.sleep(0.1)
            return ok

    def _check_daily_reset(self) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime())
        if day != self._last_reset_day:
            self._operation_limits.clear()
            self._last_reset_day = day

    @staticmethod
    def _phase_name(phase_val: int) -> str:
        names = {
            0: "未知",
            1: "种子",
            2: "发芽",
            3: "小叶",
            4: "大叶",
            5: "开花",
            6: "成熟",
            7: "枯萎",
        }
        return names.get(_to_int(phase_val, 0), "未知")

    @staticmethod
    def _current_phase(plant: plantpb_pb2.PlantInfo) -> plantpb_pb2.PlantPhaseInfo | None:
        now_sec = int(time.time())
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
