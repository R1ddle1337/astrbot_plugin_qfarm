from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from ..domain.analytics_service import AnalyticsService
from ..domain.config_data import GameConfigData
from ..domain.farm_service import FarmService
from ..domain.friend_service import FriendService
from ..domain.invite_service import InviteService
from ..domain.task_service import TaskService
from ..domain.user_service import UserService
from ..domain.warehouse_service import WarehouseService
from ..protocol import GatewaySession, GatewaySessionConfig
from ..protocol.proto import friendpb_pb2, game_pb2, notifypb_pb2, plantpb_pb2, taskpb_pb2, userpb_pb2


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


DEFAULT_AUTOMATION = {
    "farm": True,
    "farm_push": True,
    "land_upgrade": True,
    "friend": True,
    "friend_steal": True,
    "friend_help": True,
    "friend_bad": False,
    "task": True,
    "sell": True,
    "fertilizer": "both",
}


class AccountRuntime:
    def __init__(
        self,
        *,
        account: dict[str, Any],
        settings: dict[str, Any],
        session_config: GatewaySessionConfig,
        config_data: GameConfigData,
        heartbeat_interval_sec: int = 25,
        rpc_timeout_sec: int = 10,
        share_file_path: Any | None = None,
        logger: Any | None = None,
        log_callback: Any | None = None,
        kicked_callback: Any | None = None,
    ) -> None:
        self.account = dict(account)
        self.settings = dict(settings)
        self.session_config = session_config
        self.config_data = config_data
        self.heartbeat_interval_sec = max(10, int(heartbeat_interval_sec))
        self.logger = logger
        self.log_callback = log_callback
        self.kicked_callback = kicked_callback

        self.session = GatewaySession(session_config, logger=logger)
        self.analytics = AnalyticsService(config_data)
        self.farm = FarmService(self.session, config_data, self.analytics, rpc_timeout_sec=rpc_timeout_sec, logger=logger)
        self.friend = FriendService(self.session, config_data, rpc_timeout_sec=rpc_timeout_sec)
        self.task = TaskService(self.session, rpc_timeout_sec=rpc_timeout_sec)
        self.user = UserService(self.session, rpc_timeout_sec=rpc_timeout_sec)
        self.warehouse = WarehouseService(self.session, config_data, rpc_timeout_sec=rpc_timeout_sec)
        self.invite = InviteService(
            self.user,
            platform=str(self.session_config.platform or self.account.get("platform") or "qq"),
            share_file_path=share_file_path,
            logger=logger,
            log_callback=self._on_invite_log,
        )

        self.running = False
        self.connected = False
        self.login_ready = False
        self.started_at = 0.0
        self.settings_revision = _to_int(self.settings.get("__revision"), 0)
        self.user_state = {"gid": 0, "name": "", "level": 0, "gold": 0, "exp": 0, "coupon": 0, "platform": str(self.account.get("platform") or "qq")}
        self.initial_state = {"gold": 0, "exp": 0, "coupon": 0, "ready": False}
        self.last_gain = {"gold": 0, "exp": 0}
        self.operations = {
            "harvest": 0,
            "water": 0,
            "weed": 0,
            "bug": 0,
            "fertilize": 0,
            "plant": 0,
            "steal": 0,
            "helpWater": 0,
            "helpWeed": 0,
            "helpBug": 0,
            "taskClaim": 0,
            "sell": 0,
            "upgrade": 0,
        }

        self._tasks: list[asyncio.Task] = []
        self._farm_lock = asyncio.Lock()
        self._friend_lock = asyncio.Lock()
        self._task_lock = asyncio.Lock()
        self._next_farm_at = 0.0
        self._next_friend_at = 0.0
        self._last_push_ts = 0.0
        self._invite_processed = False
        self._invite_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.started_at = time.time()
        await self._connect_and_login()
        self._tasks = [
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._scheduler_loop()),
        ]

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self.login_ready = False
        self.connected = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for task in self._tasks:
            try:
                await task
            except Exception:
                pass
        self._tasks.clear()
        if self._invite_task and not self._invite_task.done():
            self._invite_task.cancel()
            try:
                await self._invite_task
            except Exception:
                pass
        self._invite_task = None
        await self.session.stop()

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    def apply_settings(self, settings: dict[str, Any], revision: int) -> None:
        self.settings = dict(settings)
        self.settings_revision = max(self.settings_revision, _to_int(revision, 0))
        self._reset_schedule()

    def update_account(self, account: dict[str, Any]) -> None:
        self.account = dict(account)

    async def get_status(self) -> dict[str, Any]:
        exp_progress = self.config_data.get_level_exp_progress(_to_int(self.user_state["level"]), _to_int(self.user_state["exp"]))
        return {
            "connection": {"connected": bool(self.connected and self.login_ready and self.session.connected)},
            "status": {
                "name": self.user_state["name"],
                "level": _to_int(self.user_state["level"]),
                "gold": _to_int(self.user_state["gold"]),
                "coupon": _to_int(self.user_state["coupon"]),
                "exp": _to_int(self.user_state["exp"]),
                "platform": self.user_state["platform"],
            },
            "uptime": max(0.0, time.time() - self.started_at),
            "operations": dict(self.operations),
            "sessionExpGained": _to_int(self.user_state["exp"]) - _to_int(self.initial_state["exp"]),
            "sessionGoldGained": _to_int(self.user_state["gold"]) - _to_int(self.initial_state["gold"]),
            "sessionCouponGained": _to_int(self.user_state["coupon"]) - _to_int(self.initial_state["coupon"]),
            "lastExpGain": _to_int(self.last_gain["exp"]),
            "lastGoldGain": _to_int(self.last_gain["gold"]),
            "limits": self.friend.get_operation_limits(),
            "automation": self._automation(),
            "preferredSeed": _to_int(self.settings.get("preferredSeedId"), 0),
            "expProgress": exp_progress,
            "configRevision": self.settings_revision,
            "nextChecks": {
                "farmRemainSec": max(0, int(self._next_farm_at - time.time())),
                "friendRemainSec": max(0, int(self._next_friend_at - time.time())),
            },
        }

    async def get_lands(self) -> dict[str, Any]:
        reply = await self.farm.get_all_lands(host_gid=0)
        self.friend.update_operation_limits(list(reply.operation_limits or []))
        return self.farm.build_lands_view(list(reply.lands or []))

    async def do_farm_operation(self, op_type: str) -> dict[str, Any]:
        async with self._farm_lock:
            return await self._do_farm_operation(op_type)

    async def get_friends(self) -> list[dict[str, Any]]:
        return await self.friend.get_friends_list(_to_int(self.user_state["gid"]))

    async def get_friend_lands(self, friend_gid: int) -> dict[str, Any]:
        return await self.friend.get_friend_lands_detail(friend_gid, _to_int(self.user_state["gid"]))

    async def do_friend_op(self, friend_gid: int, op_type: str) -> dict[str, Any]:
        result = await self.friend.do_friend_operation(friend_gid, op_type, my_gid=_to_int(self.user_state["gid"]), on_after_steal=self._auto_sell)
        count = _to_int(result.get("count"), 0)
        op = str(op_type or "").strip().lower()
        if count > 0:
            if op == "steal":
                self._record("steal", count)
            elif op == "water":
                self._record("helpWater", count)
            elif op == "weed":
                self._record("helpWeed", count)
            elif op == "bug":
                self._record("helpBug", count)
            elif op == "bad":
                self._record("bug", _to_int(result.get("bugCount"), 0))
                self._record("weed", _to_int(result.get("weedCount"), 0))
        return result

    async def get_seeds(self) -> list[dict[str, Any]]:
        return await self.farm.get_available_seeds(_to_int(self.user_state["level"]))

    async def get_bag(self) -> dict[str, Any]:
        return await self.warehouse.get_bag_detail()

    async def get_analytics(self, sort_by: str) -> list[dict[str, Any]]:
        return self.analytics.get_plant_rankings(sort_by)

    async def debug_sell(self) -> dict[str, Any]:
        return await self.warehouse.debug_sell_fruits()

    async def check_and_claim_tasks(self) -> dict[str, Any]:
        async with self._task_lock:
            result = await self.task.check_and_claim_tasks()
            claimed = _to_int(result.get("taskClaimed"), 0)
            if claimed > 0:
                self._record("taskClaim", claimed)
            return result

    async def _connect_and_login(self) -> None:
        code = str(self.account.get("code") or "").strip()
        if not code:
            raise RuntimeError("账号 code 为空")
        await self.session.start(code=code)
        await self.session.notify_dispatcher.on("*", self._on_notify)
        reply = await self.user.login(self.session_config.client_version)
        if not reply.HasField("basic"):
            raise RuntimeError("登录缺少 basic 字段")
        basic = reply.basic
        self.user_state["gid"] = _to_int(basic.gid)
        self.user_state["name"] = str(basic.name or "")
        self.user_state["level"] = _to_int(basic.level)
        self.user_state["gold"] = _to_int(basic.gold)
        self.user_state["exp"] = _to_int(basic.exp)
        self.connected = True
        self.login_ready = True
        try:
            bag = await self.warehouse.get_bag()
            for item in self.warehouse.get_bag_items(bag):
                if _to_int(item.id) == 1002:
                    self.user_state["coupon"] = _to_int(item.count)
                    break
        except Exception:
            pass
        self.initial_state = {
            "gold": _to_int(self.user_state["gold"]),
            "exp": _to_int(self.user_state["exp"]),
            "coupon": _to_int(self.user_state["coupon"]),
            "ready": True,
        }
        if not self._invite_processed:
            self._invite_processed = True
            self._invite_task = asyncio.create_task(self._process_invite_codes_once())
        self._reset_schedule()

    async def _heartbeat_loop(self) -> None:
        while self.running:
            try:
                await asyncio.sleep(self.heartbeat_interval_sec)
                if not self.login_ready:
                    continue
                await self.user.heartbeat(_to_int(self.user_state["gid"]), self.session_config.client_version)
            except asyncio.CancelledError:
                return
            except Exception:
                self.connected = False
                self.login_ready = False

    async def _scheduler_loop(self) -> None:
        backoff = 1
        while self.running:
            try:
                if not self.login_ready or not self.connected:
                    await asyncio.sleep(backoff)
                    backoff = min(30, backoff * 2)
                    await self._connect_and_login()
                    backoff = 1
                    continue
                now = time.time()
                auto = self._automation()
                if now >= self._next_farm_at:
                    if auto.get("farm", True):
                        await self.do_farm_operation("all")
                    if auto.get("task", True):
                        await self.check_and_claim_tasks()
                    self._next_farm_at = now + self._rand_interval("farm")
                if now >= self._next_friend_at:
                    if auto.get("friend", True) and not self._in_friend_quiet_hours():
                        await self._auto_friend_cycle()
                    self._next_friend_at = now + self._rand_interval("friend")
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(1.0)

    async def _auto_friend_cycle(self) -> None:
        async with self._friend_lock:
            auto = self._automation()
            for row in await self.get_friends():
                gid = _to_int(row.get("gid"), 0)
                if gid <= 0:
                    continue
                plant = row.get("plant", {}) if isinstance(row, dict) else {}
                if auto.get("friend_steal", True) and _to_int(plant.get("stealNum"), 0) > 0:
                    await self.do_friend_op(gid, "steal")
                if auto.get("friend_help", True):
                    if _to_int(plant.get("dryNum"), 0) > 0:
                        await self.do_friend_op(gid, "water")
                    if _to_int(plant.get("weedNum"), 0) > 0:
                        await self.do_friend_op(gid, "weed")
                    if _to_int(plant.get("insectNum"), 0) > 0:
                        await self.do_friend_op(gid, "bug")
                if auto.get("friend_bad", False):
                    await self.do_friend_op(gid, "bad")

    async def _do_farm_operation(self, op_type: str) -> dict[str, Any]:
        mode = str(op_type or "all").strip().lower()
        if mode not in {"all", "harvest", "clear", "plant", "upgrade"}:
            raise RuntimeError(f"不支持的农田操作: {mode}")
        reply = await self.farm.get_all_lands(host_gid=0)
        lands = list(reply.lands or [])
        self.friend.update_operation_limits(list(reply.operation_limits or []))
        analyzed = self.farm.analyze_lands(lands)
        actions: list[str] = []
        gid = _to_int(self.user_state["gid"])
        self._debug_log(
            "农场",
            (
                f"农场识别结果 mode={mode}: "
                f"harvestable={len(analyzed.harvestable)} "
                f"dead={len(analyzed.dead)} empty={len(analyzed.empty)}"
            ),
            module="farm",
            event="analyze",
            mode=mode,
            harvestable=len(analyzed.harvestable),
            dead=len(analyzed.dead),
            empty=len(analyzed.empty),
        )

        if mode in {"all", "clear"}:
            if analyzed.need_weed:
                try:
                    await self.farm.weed(analyzed.need_weed, gid)
                    self._record("weed", len(analyzed.need_weed))
                    actions.append(f"除草{len(analyzed.need_weed)}")
                except Exception as e:
                    self._debug_log(
                        "farm",
                        f"weed failed: {e}",
                        module="farm",
                        event="weed_failed",
                        count=len(analyzed.need_weed),
                    )
            if analyzed.need_bug:
                try:
                    await self.farm.bug(analyzed.need_bug, gid)
                    self._record("bug", len(analyzed.need_bug))
                    actions.append(f"除虫{len(analyzed.need_bug)}")
                except Exception as e:
                    self._debug_log(
                        "farm",
                        f"bug failed: {e}",
                        module="farm",
                        event="bug_failed",
                        count=len(analyzed.need_bug),
                    )
            if analyzed.need_water:
                try:
                    await self.farm.water(analyzed.need_water, gid)
                    self._record("water", len(analyzed.need_water))
                    actions.append(f"浇水{len(analyzed.need_water)}")
                except Exception as e:
                    self._debug_log(
                        "farm",
                        f"water failed: {e}",
                        module="farm",
                        event="water_failed",
                        count=len(analyzed.need_water),
                    )

        harvest_ids = list(analyzed.harvestable if mode in {"all", "harvest"} else [])
        if harvest_ids:
            try:
                await self.farm.harvest(harvest_ids, gid)
                self._record("harvest", len(harvest_ids))
                actions.append(f"收获{len(harvest_ids)}")
                self._debug_log(
                    "农场",
                    f"收获执行完成: count={len(harvest_ids)}",
                    module="farm",
                    event="harvest",
                    count=len(harvest_ids),
                    landIds=list(harvest_ids),
                )
            except Exception as e:
                self._debug_log(
                    "farm",
                    f"harvest failed: {e}",
                    module="farm",
                    event="harvest_failed",
                    count=len(harvest_ids),
                )
                harvest_ids = []

        if mode in {"all", "plant"}:
            # 与 Node 原逻辑保持一致：收获后的地块也走 remove->plant 流程
            # 避免部分服务端状态下收获后仍需铲除才能种植的问题。
            dead_ids = list(analyzed.dead) + list(harvest_ids)
            empty_ids = list(analyzed.empty)
            planted = await self._auto_plant(dead_ids, empty_ids)
            if planted > 0:
                actions.append(f"种植{planted}")

        if mode == "upgrade" or (mode == "all" and self._automation().get("land_upgrade", True)):
            unlocked = 0
            for land_id in analyzed.unlockable:
                try:
                    await self.farm.unlock_land(land_id, False)
                    unlocked += 1
                except Exception as e:
                    self._debug_log(
                        "farm",
                        f"unlock failed: {e}",
                        module="farm",
                        event="unlock_failed",
                        landId=land_id,
                    )
                await asyncio.sleep(0.2)
            if unlocked > 0:
                actions.append(f"解锁{unlocked}")

            upgraded = 0
            for land_id in analyzed.upgradable:
                try:
                    await self.farm.upgrade_land(land_id)
                    upgraded += 1
                except Exception as e:
                    self._debug_log(
                        "farm",
                        f"upgrade failed: {e}",
                        module="farm",
                        event="upgrade_failed",
                        landId=land_id,
                    )
                await asyncio.sleep(0.2)
            if upgraded > 0:
                self._record("upgrade", upgraded)
                actions.append(f"升级{upgraded}")

        if harvest_ids and self._automation().get("sell", True):
            await self._auto_sell()
        return {"hadWork": bool(actions), "actions": actions}

    async def _auto_plant(self, dead_ids: list[int], empty_ids: list[int]) -> int:
        lands_to_plant = list(empty_ids)
        if dead_ids:
            try:
                await self.farm.remove_plant(dead_ids)
            except Exception as e:
                self._debug_log(
                    "farm",
                    f"remove_plant failed but continue planting: {e}",
                    module="farm",
                    event="remove_plant_failed",
                    deadCount=len(dead_ids),
                )
            lands_to_plant.extend(dead_ids)
        if not lands_to_plant:
            return 0
        unique_lands: list[int] = []
        seen: set[int] = set()
        for land_id in lands_to_plant:
            lid = _to_int(land_id, 0)
            if lid <= 0 or lid in seen:
                continue
            seen.add(lid)
            unique_lands.append(lid)
        lands_to_plant = unique_lands
        if not lands_to_plant:
            return 0
        seed = await self.farm.choose_seed(
            current_level=_to_int(self.user_state["level"]),
            strategy=str(self.settings.get("strategy") or "preferred"),
            preferred_seed_id=_to_int(self.settings.get("preferredSeedId"), 0),
        )
        if not seed:
            self._debug_log(
                "farm",
                "skip auto plant: no available seed",
                module="farm",
                event="seed_unavailable",
                targetCount=len(lands_to_plant),
            )
            return 0
        seed_id = _to_int(seed.get("seedId"), 0)
        goods_id = _to_int(seed.get("goodsId"), 0)
        price = _to_int(seed.get("price"), 0)
        target_count = len(lands_to_plant)
        seed_stock = await self._get_seed_stock(seed_id)
        buy_count = target_count
        if seed_stock is not None:
            buy_count = 0
            if seed_stock < target_count:
                missing = target_count - seed_stock
                if goods_id > 0 and price > 0:
                    affordable = max(0, _to_int(self.user_state.get("gold"), 0) // max(1, price))
                    buy_count = min(missing, affordable)
                    can_plant = seed_stock + buy_count
                else:
                    can_plant = seed_stock
                if can_plant <= 0:
                    self._debug_log(
                        "farm",
                        "skip auto plant: no seed stock and cannot buy",
                        module="farm",
                        event="seed_unavailable_runtime",
                        seedId=seed_id,
                        targetCount=target_count,
                        stock=seed_stock,
                        goodsId=goods_id,
                        price=price,
                    )
                    return 0
                if can_plant < target_count:
                    lands_to_plant = lands_to_plant[:can_plant]
                    target_count = len(lands_to_plant)
            self._debug_log(
                "farm",
                f"seed plan resolved: stock={seed_stock}, buy={buy_count}, target={target_count}",
                module="farm",
                event="seed_plan",
                seedId=seed_id,
                stock=seed_stock,
                buyCount=buy_count,
                targetCount=target_count,
                goodsId=goods_id,
                price=price,
            )

        if goods_id > 0 and price > 0 and buy_count > 0:
            try:
                buy_reply = await self.farm.buy_goods(goods_id, buy_count, price)
                if buy_count > 0:
                    self.user_state["gold"] = max(0, _to_int(self.user_state.get("gold"), 0) - (price * buy_count))
                if hasattr(buy_reply, "get_items"):
                    for item in list(getattr(buy_reply, "get_items", []) or []):
                        got_id = _to_int(getattr(item, "id", 0), 0)
                        if got_id > 0:
                            seed_id = got_id
                            break
            except Exception as e:
                # 购买失败时仍尝试播种（可能背包已有种子），避免整轮自动化中断。
                self._debug_log(
                    "farm",
                    f"buy seed failed but continue planting: {e}",
                    module="farm",
                    event="seed_buy_failed",
                    seedId=seed_id,
                    goodsId=goods_id,
                    targetCount=len(lands_to_plant),
                )
                if seed_stock is not None:
                    fallback_count = min(seed_stock, len(lands_to_plant))
                    if fallback_count <= 0:
                        return 0
                    lands_to_plant = lands_to_plant[:fallback_count]
        planted = await self.farm.plant(seed_id, lands_to_plant)
        if planted > 0:
            self._record("plant", planted)
            mode = str(self._automation().get("fertilizer") or "both")
            planted_ids = lands_to_plant[:planted]
            if mode in {"normal", "both"}:
                self._record("fertilize", await self.farm.fertilize(planted_ids, 1011))
            if mode in {"organic", "both"}:
                self._record("fertilize", await self.farm.fertilize(planted_ids, 1012))
        return planted

    async def _get_seed_stock(self, seed_id: int) -> int | None:
        if seed_id <= 0:
            return 0
        warehouse = getattr(self, "warehouse", None)
        if not warehouse or not hasattr(warehouse, "get_bag") or not hasattr(warehouse, "get_bag_items"):
            return None
        try:
            bag = await warehouse.get_bag()
            items = warehouse.get_bag_items(bag)
            total = 0
            for item in list(items or []):
                if _to_int(getattr(item, "id", 0), 0) != seed_id:
                    continue
                total += max(0, _to_int(getattr(item, "count", 0), 0))
            return total
        except Exception as e:
            self._debug_log(
                "farm",
                f"seed stock check failed: {e}",
                module="farm",
                event="seed_stock_check_failed",
                seedId=seed_id,
            )
            return None

    async def _auto_sell(self) -> None:
        result = await self.warehouse.sell_all_fruits()
        if _to_int(result.get("soldKinds"), 0) > 0:
            self._record("sell", 1)

    async def _on_notify(self, message_type: str, payload: bytes) -> None:
        if "Kickout" in message_type:
            notify = game_pb2.KickoutNotify()
            notify.ParseFromString(payload)
            self.connected = False
            self.login_ready = False
            if self.kicked_callback:
                ret = self.kicked_callback(str(self.account.get("id") or ""), str(notify.reason_message or "未知"))
                if asyncio.iscoroutine(ret):
                    await ret
            return
        if "LandsNotify" in message_type and self._automation().get("farm_push", True):
            now = time.time()
            if now - self._last_push_ts > 0.5 and not self._farm_lock.locked():
                self._last_push_ts = now
                asyncio.create_task(self.do_farm_operation("all"))
            return
        if "ItemNotify" in message_type:
            notify = notifypb_pb2.ItemNotify()
            notify.ParseFromString(payload)
            for row in notify.items:
                if not row.HasField("item"):
                    continue
                item_id = _to_int(row.item.id, 0)
                count = _to_int(row.item.count, 0)
                delta = _to_int(row.delta, 0)
                if item_id == 1101:
                    old = _to_int(self.user_state["exp"])
                    self.user_state["exp"] = count if count > 0 else max(0, old + delta)
                    self.last_gain["exp"] = max(0, _to_int(self.user_state["exp"]) - old)
                elif item_id in {1, 1001}:
                    old = _to_int(self.user_state["gold"])
                    self.user_state["gold"] = count if count > 0 else max(0, old + delta)
                    self.last_gain["gold"] = max(0, _to_int(self.user_state["gold"]) - old)
                elif item_id == 1002:
                    old = _to_int(self.user_state["coupon"])
                    self.user_state["coupon"] = count if count > 0 else max(0, old + delta)
            return
        if "BasicNotify" in message_type:
            notify = userpb_pb2.BasicNotify()
            notify.ParseFromString(payload)
            if notify.HasField("basic"):
                basic = notify.basic
                if _to_int(basic.level, -1) >= 0:
                    self.user_state["level"] = _to_int(basic.level)
                if _to_int(basic.gold, -1) >= 0:
                    self.user_state["gold"] = _to_int(basic.gold)
                if _to_int(basic.exp, -1) >= 0:
                    self.user_state["exp"] = _to_int(basic.exp)
            return
        if "TaskInfoNotify" in message_type and self._automation().get("task", True):
            notify = taskpb_pb2.TaskInfoNotify()
            notify.ParseFromString(payload)
            if notify.HasField("task_info"):
                asyncio.create_task(self.check_and_claim_tasks())
            return
        if "FriendApplicationReceivedNotify" in message_type:
            notify = friendpb_pb2.FriendApplicationReceivedNotify()
            notify.ParseFromString(payload)
            gids = [_to_int(v.gid) for v in notify.applications if _to_int(v.gid) > 0]
            if gids:
                await self.friend.accept_friends(gids)

    def _automation(self) -> dict[str, Any]:
        data = self.settings.get("automation", {}) if isinstance(self.settings, dict) else {}
        result = dict(DEFAULT_AUTOMATION)
        if isinstance(data, dict):
            result.update(data)
        return result

    def _reset_schedule(self) -> None:
        now = time.time()
        self._next_farm_at = now + self._rand_interval("farm")
        self._next_friend_at = now + self._rand_interval("friend")

    def _rand_interval(self, key: str) -> int:
        intervals = self.settings.get("intervals", {}) if isinstance(self.settings, dict) else {}
        if key == "farm":
            min_sec = max(1, _to_int(intervals.get("farmMin"), _to_int(intervals.get("farm"), 2)))
            max_sec = max(min_sec, _to_int(intervals.get("farmMax"), min_sec))
        else:
            min_sec = max(1, _to_int(intervals.get("friendMin"), _to_int(intervals.get("friend"), 10)))
            max_sec = max(min_sec, _to_int(intervals.get("friendMax"), min_sec))
        return random.randint(min_sec, max_sec)

    def _in_friend_quiet_hours(self) -> bool:
        cfg = self.settings.get("friendQuietHours", {}) if isinstance(self.settings, dict) else {}
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled")):
            return False
        start = self._parse_hhmm(str(cfg.get("start") or "23:00"))
        end = self._parse_hhmm(str(cfg.get("end") or "07:00"))
        if start is None or end is None:
            return False
        now = time.localtime()
        current = now.tm_hour * 60 + now.tm_min
        if start == end:
            return True
        if start < end:
            return start <= current < end
        return current >= start or current < end

    @staticmethod
    def _parse_hhmm(value: str) -> int | None:
        parts = value.strip().split(":")
        if len(parts) != 2:
            return None
        try:
            hh = int(parts[0])
            mm = int(parts[1])
        except Exception:
            return None
        if hh < 0 or hh > 23 or mm < 0 or mm > 59:
            return None
        return hh * 60 + mm

    def _record(self, key: str, value: int) -> None:
        self.operations[key] = _to_int(self.operations.get(key), 0) + max(0, _to_int(value))

    def _debug_log(self, tag: str, message: str, **meta: Any) -> None:
        if self.logger and hasattr(self.logger, "debug"):
            try:
                self.logger.debug(f"[qfarm-runtime] [{tag}] {message}")
            except Exception:
                pass
        if self.log_callback:
            try:
                self.log_callback(
                    str((self.account or {}).get("id") or ""),
                    str(tag or ""),
                    str(message or ""),
                    False,
                    meta,
                )
            except Exception:
                pass

    async def _process_invite_codes_once(self) -> None:
        try:
            await self.invite.process_invites()
        except asyncio.CancelledError:
            return
        except Exception as e:
            self._debug_log(
                "invite",
                f"invite process failed: {e}",
                module="invite",
                event="invite_process_failed",
            )

    def _on_invite_log(self, tag: str, message: str, is_warn: bool, meta: dict[str, Any]) -> None:
        if self.log_callback:
            try:
                self.log_callback(
                    str((self.account or {}).get("id") or ""),
                    str(tag or ""),
                    str(message or ""),
                    bool(is_warn),
                    dict(meta or {}),
                )
            except Exception:
                pass
