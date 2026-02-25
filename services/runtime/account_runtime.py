from __future__ import annotations

import asyncio
import math
import random
import time
from typing import Any, Awaitable, Callable

from ..domain.analytics_service import AnalyticsService
from ..domain.config_data import GameConfigData
from ..domain.email_service import EmailService
from ..domain.farm_service import FarmService
from ..domain.friend_service import FriendService
from ..domain.invite_service import InviteService
from ..domain.mall_service import MallService
from ..domain.monthcard_service import MonthCardService
from ..domain.share_service import ShareService
from ..domain.task_service import TaskService
from ..domain.user_service import UserService
from ..domain.vip_service import VipService
from ..domain.warehouse_service import WarehouseService
from ..protocol import GatewaySession, GatewaySessionConfig
from ..protocol.proto import friendpb_pb2, game_pb2, notifypb_pb2, plantpb_pb2, taskpb_pb2, userpb_pb2


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


DEFAULT_AUTOMATION = {
    "farm": True,
    "farm_push": True,
    "land_upgrade": True,
    "friend": True,
    "friend_steal": True,
    "friend_help": True,
    "friend_bad": False,
    "task": True,
    "email": True,
    "mall": True,
    "monthcard": True,
    "vip": True,
    "share": True,
    "sell": True,
    "fertilizer": "both",
}

DAILY_ROUTINE_KEY_EMAIL = "email_rewards"
DAILY_ROUTINE_KEY_MALL_FREE = "mall_free_gifts"
DAILY_ROUTINE_KEY_MALL_ORGANIC = "mall_organic_fertilizer"
DAILY_ROUTINE_KEY_FERTILIZER_GIFT = "fertilizer_gift_use"
DAILY_ROUTINE_KEY_SHARE = "daily_share"
DAILY_ROUTINE_KEY_VIP = "vip_daily_gift"
DAILY_ROUTINE_KEY_MONTHCARD = "month_card_gift"
DAILY_ROUTINE_ERROR_BACKOFF_SEC = 30

ORGANIC_FERTILIZER_GOODS_ID = 1002


class AccountRuntime:
    def __init__(
        self,
        *,
        account: dict[str, Any],
        settings: dict[str, Any],
        session_config: GatewaySessionConfig,
        config_data: GameConfigData,
        heartbeat_interval_sec: int = 25,
        heartbeat_fail_limit: int | None = None,
        friend_error_backoff_sec: float | None = None,
        rpc_timeout_sec: int = 10,
        share_file_path: Any | None = None,
        logger: Any | None = None,
        log_callback: Any | None = None,
        kicked_callback: Any | None = None,
        runtime_state_persist: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.account = dict(account)
        self.settings = dict(settings)
        self.session_config = session_config
        self.config_data = config_data
        self.heartbeat_interval_sec = max(10, int(heartbeat_interval_sec))
        self.logger = logger
        self.log_callback = log_callback
        self.kicked_callback = kicked_callback
        self.runtime_state_persist = runtime_state_persist

        self.session = GatewaySession(session_config, logger=logger)
        self.analytics = AnalyticsService(config_data)
        self.farm = FarmService(self.session, config_data, self.analytics, rpc_timeout_sec=rpc_timeout_sec, logger=logger)
        self.friend = FriendService(self.session, config_data, rpc_timeout_sec=rpc_timeout_sec)
        self.task = TaskService(self.session, rpc_timeout_sec=rpc_timeout_sec)
        self.user = UserService(self.session, rpc_timeout_sec=rpc_timeout_sec)
        self.warehouse = WarehouseService(self.session, config_data, rpc_timeout_sec=rpc_timeout_sec)
        self.email = EmailService(self.session, rpc_timeout_sec=rpc_timeout_sec)
        self.mall = MallService(self.session, rpc_timeout_sec=rpc_timeout_sec)
        self.monthcard = MonthCardService(self.session, rpc_timeout_sec=rpc_timeout_sec)
        self.vip = VipService(self.session, rpc_timeout_sec=rpc_timeout_sec)
        self.share = ShareService(self.session, rpc_timeout_sec=rpc_timeout_sec)
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
        self._last_plant_skip_reason = ""
        self._last_farm_result = {
            "mode": "",
            "plantTargetCount": 0,
            "plantedCount": 0,
            "noActionReason": "",
            "plantSkipReason": "",
            "seedDecision": "",
            "seedDecisionReason": "",
            "preferredSeedId": 0,
            "selectedSeedId": 0,
            "selectedSeedName": "",
        }
        self._last_seed_decision = ""
        self._last_seed_decision_reason = ""
        self._last_selected_seed_id = 0
        self._last_selected_seed_name = ""
        self._daily_routines = self._normalize_daily_routines(self.settings.get("dailyRoutines"))
        self.heartbeat_fail_limit = max(
            1,
            _to_int(
                heartbeat_fail_limit if heartbeat_fail_limit is not None else self.settings.get("heartbeatFailLimit"),
                2,
            ),
        )
        self.friend_error_backoff_sec = max(
            1.0,
            _to_float(
                friend_error_backoff_sec
                if friend_error_backoff_sec is not None
                else self.settings.get(
                    "friendErrorBackoffSec",
                    (self.settings.get("automation") or {}).get("friend_error_backoff_sec", 5.0),
                ),
                5.0,
            ),
        )
        self._session_disconnect_bound = False

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
        self._daily_routines = self._normalize_daily_routines(self.settings.get("dailyRoutines"))
        self.heartbeat_fail_limit = max(1, _to_int(self.settings.get("heartbeatFailLimit"), self.heartbeat_fail_limit))
        self.friend_error_backoff_sec = max(
            1.0,
            _to_float(
                self.settings.get(
                    "friendErrorBackoffSec",
                    (self.settings.get("automation") or {}).get(
                        "friend_error_backoff_sec",
                        self.friend_error_backoff_sec,
                    ),
                ),
                self.friend_error_backoff_sec,
            ),
        )
        self._reset_schedule()

    def update_account(self, account: dict[str, Any]) -> None:
        self.account = dict(account)

    async def get_status(self) -> dict[str, Any]:
        exp_progress = self.config_data.get_level_exp_progress(_to_int(self.user_state["level"]), _to_int(self.user_state["exp"]))
        last_farm = self._last_farm_result if isinstance(getattr(self, "_last_farm_result", None), dict) else {}
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
                "farmRemainSec": max(0, math.ceil(self._next_farm_at - time.time())),
                "friendRemainSec": max(0, math.ceil(self._next_friend_at - time.time())),
            },
            "lastFarm": {
                "mode": str(last_farm.get("mode") or ""),
                "plantTargetCount": max(0, _to_int(last_farm.get("plantTargetCount"), 0)),
                "plantedCount": max(0, _to_int(last_farm.get("plantedCount"), 0)),
                "noActionReason": str(last_farm.get("noActionReason") or ""),
                "plantSkipReason": str(last_farm.get("plantSkipReason") or ""),
                "seedDecision": str(last_farm.get("seedDecision") or ""),
                "seedDecisionReason": str(last_farm.get("seedDecisionReason") or ""),
                "preferredSeedId": max(0, _to_int(last_farm.get("preferredSeedId"), 0)),
                "selectedSeedId": max(0, _to_int(last_farm.get("selectedSeedId"), 0)),
                "selectedSeedName": str(last_farm.get("selectedSeedName") or ""),
            },
            "dailyRoutines": self._daily_routines_snapshot(),
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

    async def get_email_list(self, box_type: int = 1) -> dict[str, Any]:
        reply = await self.email.get_email_list(box_type)
        rows: list[dict[str, Any]] = []
        for item in list(reply.emails or []):
            rows.append(
                {
                    "id": str(getattr(item, "id", "") or ""),
                    "mailType": _to_int(getattr(item, "mail_type", 0), 0),
                    "title": str(getattr(item, "title", "") or ""),
                    "claimed": bool(getattr(item, "claimed", False)),
                    "hasReward": bool(getattr(item, "has_reward", False)),
                    "subtitle": str(getattr(item, "subtitle", "") or ""),
                }
            )
        return {"boxType": _to_int(box_type, 1), "emails": rows}

    async def claim_email(self, box_type: int = 1, email_id: str = "", *, batch: bool = False) -> dict[str, Any]:
        if batch:
            reply = await self.email.batch_claim_email(box_type, email_id)
        else:
            reply = await self.email.claim_email(box_type, email_id)
        return {
            "boxType": _to_int(box_type, 1),
            "emailId": str(email_id or ""),
            "batch": bool(batch),
            "items": self._format_core_items(list(getattr(reply, "items", []) or [])),
        }

    async def get_mall_goods(self, slot_type: int = 1) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for goods in await self.mall.get_mall_goods_list(slot_type):
            rows.append(
                {
                    "goodsId": _to_int(getattr(goods, "goods_id", 0), 0),
                    "name": str(getattr(goods, "name", "") or ""),
                    "type": _to_int(getattr(goods, "type", 0), 0),
                    "isFree": bool(getattr(goods, "is_free", False)),
                    "isLimited": bool(getattr(goods, "is_limited", False)),
                    "discount": str(getattr(goods, "discount", "") or ""),
                }
            )
        return {"slotType": _to_int(slot_type, 1), "goods": rows}

    async def purchase_mall_goods(self, goods_id: int, count: int = 1) -> dict[str, Any]:
        reply = await self.mall.purchase(goods_id, count)
        return {
            "goodsId": _to_int(getattr(reply, "goods_id", goods_id), 0),
            "count": _to_int(getattr(reply, "count", count), 0),
            "rewardInfoSize": len(bytes(getattr(reply, "reward_info", b"") or b"")),
            "resultSize": len(bytes(getattr(reply, "result", b"") or b"")),
        }

    async def get_monthcard_infos(self) -> dict[str, Any]:
        reply = await self.monthcard.get_month_card_infos()
        infos: list[dict[str, Any]] = []
        for row in list(reply.infos or []):
            reward = getattr(row, "reward", None)
            reward_payload: dict[str, int] | None = None
            if reward is not None:
                reward_payload = {
                    "id": _to_int(getattr(reward, "id", 0), 0),
                    "count": _to_int(getattr(reward, "count", 0), 0),
                }
            infos.append(
                {
                    "goodsId": _to_int(getattr(row, "goods_id", 0), 0),
                    "canClaim": bool(getattr(row, "can_claim", False)),
                    "reward": reward_payload,
                }
            )
        return {"infos": infos}

    async def claim_monthcard_reward(self, goods_id: int) -> dict[str, Any]:
        reply = await self.monthcard.claim_month_card_reward(goods_id)
        return {"goodsId": _to_int(goods_id, 0), "items": self._format_core_items(list(reply.items or []))}

    async def get_vip_daily_status(self) -> dict[str, Any]:
        reply = await self.vip.get_daily_gift_status()
        return {
            "canClaim": bool(getattr(reply, "can_claim", False)),
            "hasGift": bool(getattr(reply, "has_gift", False)),
        }

    async def claim_vip_daily_gift(self) -> dict[str, Any]:
        reply = await self.vip.claim_daily_gift()
        return {"items": self._format_core_items(list(reply.items or []))}

    async def check_can_share(self) -> dict[str, Any]:
        reply = await self.share.check_can_share()
        return {"canShare": bool(getattr(reply, "can_share", False))}

    async def report_share(self, shared: bool = True) -> dict[str, Any]:
        reply = await self.share.report_share(shared)
        return {"shared": bool(shared), "success": bool(getattr(reply, "success", False))}

    async def claim_share_reward(self, claimed: bool = True) -> dict[str, Any]:
        reply = await self.share.claim_share_reward(claimed)
        return {
            "claimed": bool(claimed),
            "success": bool(getattr(reply, "success", False)),
            "hasReward": bool(getattr(reply, "has_reward", False)),
            "items": self._format_core_items(list(reply.items or [])),
        }

    async def run_daily_routine(self, routine: str, force: bool = False) -> dict[str, Any]:
        token = str(routine or "").strip().lower()
        if token in {"email", "mail"}:
            return self._with_status_code(await self._run_email_routine(force=bool(force)))
        if token in {"mall", "shop"}:
            free = self._with_status_code(await self._run_mall_free_gifts_routine(force=bool(force)))
            organic = self._with_status_code(await self._run_mall_organic_routine(force=bool(force)))
            fertilizer_gift = self._with_status_code(await self._run_fertilizer_gift_routine(force=bool(force)))
            return {
                "routine": "mall",
                "statusCode": self._merge_status_codes([free.get("statusCode"), organic.get("statusCode"), fertilizer_gift.get("statusCode")]),
                "freeGifts": free,
                "organicFertilizer": organic,
                "fertilizerGift": fertilizer_gift,
            }
        if token in {"fertilizer", "fertilizer_gift"}:
            return self._with_status_code(await self._run_fertilizer_gift_routine(force=bool(force)))
        if token in {"monthcard", "month_card"}:
            return self._with_status_code(await self._run_monthcard_routine(force=bool(force)))
        if token in {"vip", "qqvip"}:
            return self._with_status_code(await self._run_vip_routine(force=bool(force)))
        if token in {"share"}:
            return self._with_status_code(await self._run_share_routine(force=bool(force)))
        if token in {"all", "*"}:
            return await self.run_daily_routines(force=bool(force))
        raise RuntimeError(f"unsupported daily routine: {routine}")

    async def run_daily_routines(self, force: bool = False) -> dict[str, Any]:
        auto = self._automation()
        result: dict[str, Any] = {"force": bool(force)}
        if auto.get("email", True):
            result["email"] = self._with_status_code(await self._run_email_routine(force=bool(force)))
        if auto.get("mall", True):
            result["mallFreeGifts"] = self._with_status_code(await self._run_mall_free_gifts_routine(force=bool(force)))
            result["mallOrganicFertilizer"] = self._with_status_code(await self._run_mall_organic_routine(force=bool(force)))
            result["fertilizerGift"] = self._with_status_code(await self._run_fertilizer_gift_routine(force=bool(force)))
        if auto.get("share", True):
            result["share"] = self._with_status_code(await self._run_share_routine(force=bool(force)))
        if auto.get("monthcard", True):
            result["monthcard"] = self._with_status_code(await self._run_monthcard_routine(force=bool(force)))
        if auto.get("vip", True):
            result["vip"] = self._with_status_code(await self._run_vip_routine(force=bool(force)))
        summary: dict[str, str] = {}
        for key, value in result.items():
            if key == "force" or not isinstance(value, dict):
                continue
            summary[key] = str(value.get("statusCode") or "none")
        overall = self._merge_status_codes(summary.values())
        result["statusCode"] = overall
        self._debug_log(
            "daily",
            f"daily summary: {summary}",
            module="task",
            event="daily_summary",
            result=overall,
            summary=summary,
            force=bool(force),
        )
        return result

    def _with_status_code(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = dict(payload or {})
        status_code = str(row.get("statusCode") or "").strip().lower()
        if status_code not in {"ok", "none", "error", "already_claimed", "no_coupon", "skipped"}:
            status_code = self._infer_status_code(row)
        row["statusCode"] = status_code
        return row

    @staticmethod
    def _infer_status_code(payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return "error"
        if bool(payload.get("skipped")):
            return "skipped"
        if str(payload.get("error") or "").strip():
            return "error"
        if bool(payload.get("alreadyClaimed")):
            return "already_claimed"
        if bool(payload.get("pausedNoCoupon")):
            return "no_coupon"
        claimed = payload.get("claimed")
        if isinstance(claimed, bool) and claimed:
            return "ok"
        if not isinstance(claimed, bool) and _to_int(claimed, 0) > 0:
            return "ok"
        if _to_int(payload.get("bought"), 0) > 0 or _to_int(payload.get("rewardItems"), 0) > 0:
            return "ok"
        return "none"

    @staticmethod
    def _merge_status_codes(codes: Any) -> str:
        values = [str(item or "").strip().lower() for item in list(codes or []) if str(item or "").strip()]
        if not values:
            return "none"
        if "error" in values:
            return "error"
        if "ok" in values:
            return "ok"
        if "no_coupon" in values:
            return "no_coupon"
        if "already_claimed" in values:
            return "already_claimed"
        if all(item == "skipped" for item in values):
            return "skipped"
        return "none"

    async def get_daily_routines_state(self) -> dict[str, Any]:
        return self._daily_routines_snapshot()

    async def _run_email_routine(self, *, force: bool = False) -> dict[str, Any]:
        routine_key = DAILY_ROUTINE_KEY_EMAIL
        if not await self._routine_can_run(routine_key, cooldown_sec=300, force=force):
            return {"routine": routine_key, "skipped": True, "state": self._routine_state(routine_key)}
        try:
            box1, box2 = await asyncio.gather(
                self.get_email_list(1),
                self.get_email_list(2),
            )
            merged: dict[str, dict[str, Any]] = {}
            for box_type, rows in ((1, box1.get("emails", [])), (2, box2.get("emails", []))):
                for row in list(rows or []):
                    email_id = str((row or {}).get("id") or "").strip()
                    if not email_id:
                        continue
                    current = dict(row or {})
                    current["_boxType"] = box_type
                    old = merged.get(email_id)
                    if old is None:
                        merged[email_id] = current
                        continue
                    old_claimable = bool(old.get("hasReward")) and not bool(old.get("claimed"))
                    now_claimable = bool(current.get("hasReward")) and not bool(current.get("claimed"))
                    if now_claimable and not old_claimable:
                        merged[email_id] = current

            claimable = [
                row for row in merged.values() if bool(row.get("hasReward")) and not bool(row.get("claimed"))
            ]
            if not claimable:
                await self._mark_routine_done(routine_key, result="none", error="")
                self._debug_log("daily", "email routine: no claimable mails", module="task", event=routine_key, result="none")
                return {"routine": routine_key, "claimed": 0, "rewardItems": 0, "state": self._routine_state(routine_key)}

            claimed = 0
            reward_items = 0
            grouped: dict[int, list[dict[str, Any]]] = {}
            for row in claimable:
                box_type = _to_int(row.get("_boxType"), 1)
                grouped.setdefault(box_type if box_type in {1, 2} else 1, []).append(row)
            for box_type, rows in grouped.items():
                first_id = str((rows[0] or {}).get("id") or "").strip()
                if not first_id:
                    continue
                try:
                    batch_reply = await self.claim_email(box_type, first_id, batch=True)
                    claimed += 1
                    reward_items += len(batch_reply.get("items", []))
                except Exception:
                    pass
            for row in claimable:
                box_type = _to_int(row.get("_boxType"), 1)
                email_id = str(row.get("id") or "").strip()
                if not email_id:
                    continue
                try:
                    claim_reply = await self.claim_email(box_type, email_id, batch=False)
                    claimed += 1
                    reward_items += len(claim_reply.get("items", []))
                except Exception:
                    continue
            if claimed > 0:
                await self._mark_routine_done(routine_key, result="ok", claimed=True, error="")
                self._debug_log(
                    "daily",
                    f"email routine claimed: {claimed}",
                    module="task",
                    event=routine_key,
                    result="ok",
                    count=claimed,
                )
            else:
                await self._mark_routine_done(routine_key, result="none", error="")
            return {"routine": routine_key, "claimed": claimed, "rewardItems": reward_items, "state": self._routine_state(routine_key)}
        except Exception as e:
            await self._mark_routine_error(routine_key, str(e))
            self._debug_log("daily", f"email routine failed: {e}", module="task", event=routine_key, result="error")
            return {"routine": routine_key, "claimed": 0, "rewardItems": 0, "error": str(e), "state": self._routine_state(routine_key)}

    async def _run_mall_free_gifts_routine(self, *, force: bool = False) -> dict[str, Any]:
        routine_key = DAILY_ROUTINE_KEY_MALL_FREE
        if not await self._routine_can_run(routine_key, cooldown_sec=600, force=force):
            return {"routine": routine_key, "skipped": True, "state": self._routine_state(routine_key)}
        try:
            payload = await self.get_mall_goods(1)
            goods = list(payload.get("goods", []))
            freebies = [row for row in goods if bool((row or {}).get("isFree")) and _to_int((row or {}).get("goodsId"), 0) > 0]
            if not freebies:
                await self._mark_routine_done(routine_key, result="none", error="")
                self._debug_log("daily", "mall free gifts: none", module="task", event=routine_key, result="none")
                return {"routine": routine_key, "claimed": 0, "state": self._routine_state(routine_key)}
            claimed = 0
            for row in freebies:
                goods_id = _to_int(row.get("goodsId"), 0)
                if goods_id <= 0:
                    continue
                try:
                    await self.purchase_mall_goods(goods_id, 1)
                    claimed += 1
                except Exception:
                    continue
            await self._mark_routine_done(routine_key, result="ok" if claimed > 0 else "none", claimed=claimed > 0, error="")
            self._debug_log(
                "daily",
                f"mall free gifts claimed: {claimed}",
                module="task",
                event=routine_key,
                result="ok" if claimed > 0 else "none",
                count=claimed,
            )
            return {"routine": routine_key, "claimed": claimed, "state": self._routine_state(routine_key)}
        except Exception as e:
            await self._mark_routine_error(routine_key, str(e))
            self._debug_log("daily", f"mall free gifts failed: {e}", module="task", event=routine_key, result="error")
            return {"routine": routine_key, "claimed": 0, "error": str(e), "state": self._routine_state(routine_key)}

    async def _run_mall_organic_routine(self, *, force: bool = False) -> dict[str, Any]:
        routine_key = DAILY_ROUTINE_KEY_MALL_ORGANIC
        if not await self._routine_can_run(routine_key, cooldown_sec=600, force=force):
            return {"routine": routine_key, "skipped": True, "state": self._routine_state(routine_key)}
        try:
            goods_list = await self.mall.get_mall_goods_list(1)
            target = None
            for row in goods_list:
                if _to_int(getattr(row, "goods_id", 0), 0) == ORGANIC_FERTILIZER_GOODS_ID:
                    target = row
                    break
            if target is None:
                await self._mark_routine_done(routine_key, result="none", error="")
                return {"routine": routine_key, "bought": 0, "state": self._routine_state(routine_key)}

            price = self._parse_mall_price_value(getattr(target, "price", b""))
            coupon = max(0, _to_int(self.user_state.get("coupon"), 0))
            if price > 0 and coupon < price:
                await self._mark_routine_done(routine_key, result="no_coupon", error="")
                return {"routine": routine_key, "bought": 0, "pausedNoCoupon": True, "state": self._routine_state(routine_key)}

            bought = 0
            per_round = 10
            for _ in range(30):
                if price > 0 and coupon < price:
                    break
                count = per_round
                if price > 0:
                    affordable = max(0, coupon // price)
                    if affordable <= 0:
                        break
                    count = max(1, min(per_round, affordable))
                try:
                    await self.purchase_mall_goods(ORGANIC_FERTILIZER_GOODS_ID, count)
                    bought += count
                    if price > 0:
                        coupon = max(0, coupon - (price * count))
                    await asyncio.sleep(0.12)
                except Exception as e:
                    message = str(e or "")
                    if ("code=1000019" in message or "余额不足" in message or "点券不足" in message) and count > 1:
                        per_round = 1
                        continue
                    if "code=1000019" in message or "余额不足" in message or "点券不足" in message:
                        await self._mark_routine_done(routine_key, result="no_coupon", error="")
                    break

            if bought > 0:
                self.user_state["coupon"] = coupon
                await self._mark_routine_done(routine_key, result="ok", claimed=True, error="")
            elif self._routine_state(routine_key).get("lastResult") == "no_coupon":
                pass
            else:
                await self._mark_routine_done(routine_key, result="none", error="")
            return {"routine": routine_key, "bought": bought, "state": self._routine_state(routine_key)}
        except Exception as e:
            await self._mark_routine_error(routine_key, str(e))
            self._debug_log("daily", f"mall organic routine failed: {e}", module="task", event=routine_key, result="error")
            return {"routine": routine_key, "bought": 0, "error": str(e), "state": self._routine_state(routine_key)}

    async def _run_fertilizer_gift_routine(self, *, force: bool = False) -> dict[str, Any]:
        routine_key = DAILY_ROUTINE_KEY_FERTILIZER_GIFT
        if not await self._routine_can_run(routine_key, cooldown_sec=600, force=force):
            return {"routine": routine_key, "skipped": True, "state": self._routine_state(routine_key)}
        try:
            result = await self.warehouse.use_fertilizer_gifts()
            used_count = max(0, _to_int(result.get("usedCount"), 0))
            used_kinds = max(0, _to_int(result.get("usedKinds"), 0))
            failed_kinds = max(0, _to_int(result.get("failedKinds"), 0))
            mode = str(result.get("mode") or "")
            error = str(result.get("error") or "")
            if used_count > 0:
                await self._mark_routine_done(routine_key, result="ok", claimed=True, error="")
            elif failed_kinds > 0 and error:
                await self._mark_routine_error(routine_key, error)
            else:
                await self._mark_routine_done(routine_key, result="none", error="")
            self._debug_log(
                "daily",
                f"fertilizer gifts used: kinds={used_kinds}, count={used_count}, failed={failed_kinds}, mode={mode}",
                module="task",
                event=routine_key,
                result="ok" if used_count > 0 else ("error" if failed_kinds > 0 and error else "none"),
                usedKinds=used_kinds,
                usedCount=used_count,
                failedKinds=failed_kinds,
                mode=mode,
            )
            return {
                "routine": routine_key,
                "usedKinds": used_kinds,
                "usedCount": used_count,
                "failedKinds": failed_kinds,
                "mode": mode,
                "error": error if failed_kinds > 0 and used_count <= 0 else "",
                "state": self._routine_state(routine_key),
            }
        except Exception as e:
            await self._mark_routine_error(routine_key, str(e))
            self._debug_log("daily", f"fertilizer gift routine failed: {e}", module="task", event=routine_key, result="error")
            return {"routine": routine_key, "usedKinds": 0, "usedCount": 0, "error": str(e), "state": self._routine_state(routine_key)}

    async def _run_monthcard_routine(self, *, force: bool = False) -> dict[str, Any]:
        routine_key = DAILY_ROUTINE_KEY_MONTHCARD
        if not await self._routine_can_run(routine_key, cooldown_sec=600, force=force):
            return {"routine": routine_key, "skipped": True, "state": self._routine_state(routine_key)}
        try:
            infos_payload = await self.get_monthcard_infos()
            infos = list(infos_payload.get("infos", []))
            claimable = [row for row in infos if bool((row or {}).get("canClaim")) and _to_int((row or {}).get("goodsId"), 0) > 0]
            if not claimable:
                await self._mark_routine_done(routine_key, result="none", error="")
                return {"routine": routine_key, "claimed": 0, "state": self._routine_state(routine_key)}
            claimed = 0
            reward_items = 0
            for row in claimable:
                goods_id = _to_int(row.get("goodsId"), 0)
                if goods_id <= 0:
                    continue
                try:
                    rep = await self.claim_monthcard_reward(goods_id)
                    claimed += 1
                    reward_items += len(rep.get("items", []))
                except Exception:
                    continue
            if claimed > 0:
                await self._mark_routine_done(routine_key, result="ok", claimed=True, error="")
            else:
                await self._mark_routine_done(routine_key, result="none", error="")
            return {"routine": routine_key, "claimed": claimed, "rewardItems": reward_items, "state": self._routine_state(routine_key)}
        except Exception as e:
            await self._mark_routine_error(routine_key, str(e))
            self._debug_log("daily", f"monthcard routine failed: {e}", module="task", event=routine_key, result="error")
            return {"routine": routine_key, "claimed": 0, "error": str(e), "state": self._routine_state(routine_key)}

    async def _run_vip_routine(self, *, force: bool = False) -> dict[str, Any]:
        routine_key = DAILY_ROUTINE_KEY_VIP
        if not await self._routine_can_run(routine_key, cooldown_sec=600, force=force):
            return {"routine": routine_key, "skipped": True, "state": self._routine_state(routine_key)}
        try:
            status = await self.get_vip_daily_status()
            if not bool(status.get("canClaim")):
                await self._mark_routine_done(routine_key, result="none", error="")
                return {"routine": routine_key, "claimed": False, "state": self._routine_state(routine_key)}
            claim = await self.claim_vip_daily_gift()
            await self._mark_routine_done(routine_key, result="ok", claimed=True, error="")
            return {
                "routine": routine_key,
                "claimed": True,
                "rewardItems": len(claim.get("items", [])),
                "state": self._routine_state(routine_key),
            }
        except Exception as e:
            message = str(e or "")
            if "code=1021002" in message or "已领取" in message or "今日已领" in message:
                await self._mark_routine_done(routine_key, result="none", claimed=True, error="")
                return {"routine": routine_key, "claimed": False, "alreadyClaimed": True, "state": self._routine_state(routine_key)}
            await self._mark_routine_error(routine_key, message)
            self._debug_log("daily", f"vip routine failed: {e}", module="task", event=routine_key, result="error")
            return {"routine": routine_key, "claimed": False, "error": message, "state": self._routine_state(routine_key)}

    async def _run_share_routine(self, *, force: bool = False) -> dict[str, Any]:
        routine_key = DAILY_ROUTINE_KEY_SHARE
        if not await self._routine_can_run(routine_key, cooldown_sec=600, force=force):
            return {"routine": routine_key, "skipped": True, "state": self._routine_state(routine_key)}
        try:
            can = await self.check_can_share()
            if not bool(can.get("canShare")):
                await self._mark_routine_done(routine_key, result="none", error="")
                return {"routine": routine_key, "claimed": False, "state": self._routine_state(routine_key)}
            report = await self.report_share(True)
            if not bool(report.get("success")):
                await self._mark_routine_error(routine_key, "report_share_failed")
                return {"routine": routine_key, "claimed": False, "error": "report_share_failed", "state": self._routine_state(routine_key)}
            claim = await self.claim_share_reward(True)
            if bool(claim.get("success")):
                await self._mark_routine_done(routine_key, result="ok", claimed=True, error="")
                return {
                    "routine": routine_key,
                    "claimed": True,
                    "rewardItems": len(claim.get("items", [])),
                    "state": self._routine_state(routine_key),
                }
            await self._mark_routine_done(routine_key, result="none", error="")
            return {"routine": routine_key, "claimed": False, "state": self._routine_state(routine_key)}
        except Exception as e:
            message = str(e or "")
            if "code=1009001" in message or "已领取" in message:
                await self._mark_routine_done(routine_key, result="none", claimed=True, error="")
                return {"routine": routine_key, "claimed": False, "alreadyClaimed": True, "state": self._routine_state(routine_key)}
            await self._mark_routine_error(routine_key, message)
            self._debug_log("daily", f"share routine failed: {e}", module="task", event=routine_key, result="error")
            return {"routine": routine_key, "claimed": False, "error": message, "state": self._routine_state(routine_key)}

    async def _connect_and_login(self) -> None:
        code = str(self.account.get("code") or "").strip()
        if not code:
            raise RuntimeError("账号缺少绑定 code，code 可能失效，请重新扫码绑定")
        if not self._session_disconnect_bound:
            await self.session.on_disconnect(self._on_session_disconnect)
            self._session_disconnect_bound = True
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
        asyncio.create_task(self.run_daily_routines(force=True))

    async def _on_session_disconnect(self, reason: str) -> None:
        self.connected = False
        self.login_ready = False
        self._debug_log(
            "session",
            f"session disconnected: {reason}",
            module="system",
            event="session_disconnected",
            result="error",
            reason=str(reason or ""),
        )

    async def _heartbeat_loop(self) -> None:
        fail_streak = 0
        while self.running:
            try:
                await asyncio.sleep(self.heartbeat_interval_sec)
                if not self.login_ready:
                    fail_streak = 0
                    continue
                fail_limit = max(1, _to_int(getattr(self, "heartbeat_fail_limit", 3), 3))
                if not bool(getattr(self.session, "connected", False)):
                    fail_streak += 1
                    if fail_streak >= fail_limit:
                        self.connected = False
                        self.login_ready = False
                        self._debug_log(
                            "heartbeat",
                            f"heartbeat fail limit reached ({fail_streak}/{fail_limit}), mark offline: session disconnected",
                            module="system",
                            event="heartbeat_fail_limit",
                            result="error",
                            streak=fail_streak,
                            limit=fail_limit,
                        )
                        try:
                            await self.session.stop()
                        except Exception:
                            pass
                        fail_streak = 0
                    continue
                await self.user.heartbeat(_to_int(self.user_state["gid"]), self.session_config.client_version)
                fail_streak = 0
            except asyncio.CancelledError:
                return
            except Exception as e:
                fail_limit = max(1, _to_int(getattr(self, "heartbeat_fail_limit", 3), 3))
                fail_streak += 1
                self._debug_log(
                    "heartbeat",
                    f"heartbeat failed ({fail_streak}/{fail_limit}): {e}",
                    module="system",
                    event="heartbeat_error",
                    result="error",
                    streak=fail_streak,
                    limit=fail_limit,
                )
                if fail_streak < fail_limit:
                    continue
                self.connected = False
                self.login_ready = False
                self._debug_log(
                    "heartbeat",
                    f"heartbeat fail limit reached ({fail_streak}/{fail_limit}), mark offline and reconnect",
                    module="system",
                    event="heartbeat_fail_limit",
                    result="error",
                    streak=fail_streak,
                    limit=fail_limit,
                )
                try:
                    await self.session.stop()
                except Exception:
                    pass
                fail_streak = 0

    async def _scheduler_loop(self) -> None:
        backoff = 1.0
        farm_error_backoff = 5.0
        friend_error_backoff_base = max(1.0, _to_float(getattr(self, "friend_error_backoff_sec", 10.0), 10.0))
        friend_error_backoff = friend_error_backoff_base
        while self.running:
            try:
                session_connected = bool(getattr(self.session, "connected", False))
                if not self.login_ready or not self.connected or not session_connected:
                    self.connected = False
                    self.login_ready = False
                    try:
                        await self._connect_and_login()
                        backoff = 1.0
                    except Exception as e:
                        self._debug_log(
                            "scheduler",
                            f"reconnect failed, backoff={backoff:.1f}s: {e}",
                            module="system",
                            event="reconnect_error",
                            result="error",
                            backoffSec=backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(30.0, backoff * 2)
                    continue
                now = time.time()
                auto = self._automation()
                if now >= self._next_farm_at:
                    try:
                        if auto.get("farm", True):
                            await self.do_farm_operation("all")
                        if auto.get("task", True):
                            await self.check_and_claim_tasks()
                        await self.run_daily_routines(force=False)
                        self._next_farm_at = time.time() + self._rand_interval("farm")
                        farm_error_backoff = 5.0
                    except Exception as e:
                        self._next_farm_at = time.time() + farm_error_backoff
                        self._debug_log(
                            "scheduler",
                            f"farm cycle failed, backoff={farm_error_backoff:.1f}s: {e}",
                            module="system",
                            event="farm_cycle_error",
                            result="error",
                            backoffSec=farm_error_backoff,
                        )
                        farm_error_backoff = min(300.0, farm_error_backoff * 2)
                if now >= self._next_friend_at:
                    try:
                        if auto.get("friend", True) and not self._in_friend_quiet_hours():
                            await self._auto_friend_cycle()
                        self._next_friend_at = time.time() + self._rand_interval("friend")
                        friend_error_backoff = friend_error_backoff_base
                    except Exception as e:
                        self._next_friend_at = time.time() + friend_error_backoff
                        self._debug_log(
                            "scheduler",
                            f"friend cycle failed, backoff={friend_error_backoff:.1f}s: {e}",
                            module="system",
                            event="friend_cycle_error",
                            result="error",
                            backoffSec=friend_error_backoff,
                        )
                        friend_error_backoff = min(300.0, friend_error_backoff * 2)
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
        plant_target_count = 0
        planted_count = 0
        plant_skip_reason = ""
        harvest_skip_reason = ""
        no_action_reason = ""
        seed_decision = ""
        seed_decision_reason = ""
        selected_seed_id = 0
        selected_seed_name = ""
        preferred_seed_id = max(0, _to_int(self.settings.get("preferredSeedId"), 0))
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
        elif mode in {"all", "harvest"}:
            harvest_skip_reason = "本轮没有成熟地块可收获"

        if mode in {"all", "plant"}:
            # 与 Node 原逻辑保持一致：收获后的地块也走 remove->plant 流程
            # 避免部分服务端状态下收获后仍需铲除才能种植的问题。
            dead_ids = list(analyzed.dead) + list(harvest_ids)
            empty_ids = list(analyzed.empty)
            plant_target_count = len({_to_int(v, 0) for v in dead_ids + empty_ids if _to_int(v, 0) > 0})
            planted = await self._auto_plant(dead_ids, empty_ids)
            planted_count = max(0, _to_int(planted, 0))
            seed_decision = str(getattr(self, "_last_seed_decision", "") or "")
            seed_decision_reason = str(getattr(self, "_last_seed_decision_reason", "") or "")
            selected_seed_id = max(0, _to_int(getattr(self, "_last_selected_seed_id", 0), 0))
            selected_seed_name = str(getattr(self, "_last_selected_seed_name", "") or "")
            if planted > 0:
                actions.append(f"种植{planted}")
            elif plant_target_count > 0:
                plant_skip_reason = str(getattr(self, "_last_plant_skip_reason", "") or "存在可种植地块，但本次未完成种植")

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
        if not actions:
            if mode == "harvest" and harvest_skip_reason:
                no_action_reason = harvest_skip_reason
            elif mode in {"all", "plant"} and plant_skip_reason:
                no_action_reason = plant_skip_reason
            else:
                no_action_reason = "当前地块状态无需执行本轮操作"
            self._debug_log(
                "农场",
                f"本轮无动作: mode={mode}, reason={no_action_reason}",
                module="farm",
                event="noop",
                mode=mode,
                reason=no_action_reason,
            )
        result = {
            "hadWork": bool(actions),
            "actions": actions,
            "mode": mode,
            "summary": {
                "harvestable": len(analyzed.harvestable),
                "dead": len(analyzed.dead),
                "empty": len(analyzed.empty),
                "needWater": len(analyzed.need_water),
                "needWeed": len(analyzed.need_weed),
                "needBug": len(analyzed.need_bug),
                "unlockable": len(analyzed.unlockable),
                "upgradable": len(analyzed.upgradable),
            },
            "plantTargetCount": plant_target_count,
            "plantedCount": planted_count,
            "plantSkipReason": plant_skip_reason,
            "seedDecision": seed_decision,
            "seedDecisionReason": seed_decision_reason,
            "preferredSeedId": preferred_seed_id,
            "selectedSeedId": selected_seed_id,
            "selectedSeedName": selected_seed_name,
            "plantFailures": list(getattr(self.farm, "last_plant_failures", []) or [])[:10],
            "explain": {
                "harvestSkipReason": harvest_skip_reason,
                "plantSkipReason": plant_skip_reason,
                "noActionReason": no_action_reason,
            },
        }
        self._last_farm_result = {
            "mode": mode,
            "plantTargetCount": plant_target_count,
            "plantedCount": planted_count,
            "noActionReason": no_action_reason,
            "plantSkipReason": plant_skip_reason,
            "seedDecision": seed_decision,
            "seedDecisionReason": seed_decision_reason,
            "preferredSeedId": preferred_seed_id,
            "selectedSeedId": selected_seed_id,
            "selectedSeedName": selected_seed_name,
        }
        return result

    async def _auto_plant(self, dead_ids: list[int], empty_ids: list[int]) -> int:
        self._last_plant_skip_reason = ""
        self._last_seed_decision = ""
        self._last_seed_decision_reason = ""
        self._last_selected_seed_id = 0
        self._last_selected_seed_name = ""
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
            self._last_plant_skip_reason = "没有可种植的空地或枯萎地块"
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
            self._last_plant_skip_reason = "没有可种植的有效地块"
            return 0

        current_level = max(1, _to_int(self.user_state["level"]))
        preferred_seed_id = max(0, _to_int(self.settings.get("preferredSeedId"), 0))
        strategy = str(self.settings.get("strategy") or "preferred").strip().lower()
        seed: dict[str, Any] | None = None

        if preferred_seed_id > 0:
            if strategy != "preferred":
                self._debug_log(
                    "farm",
                    f"force preferred seed selection this round: preferred={preferred_seed_id}, strategy={strategy}",
                    module="farm",
                    event="seed_preferred_forced",
                    preferredSeedId=preferred_seed_id,
                    strategy=strategy,
                )
            seed = await self._pick_seed_from_bag(
                current_level=current_level,
                preferred_seed_id=preferred_seed_id,
                preferred_only=True,
            )
            if seed:
                self._last_seed_decision = "preferred_bag"
                self._last_seed_decision_reason = f"偏好种子 {preferred_seed_id} 已从背包命中。"
            else:
                seed = await self._pick_preferred_seed_from_shop(
                    current_level=current_level,
                    preferred_seed_id=preferred_seed_id,
                )
                if seed:
                    self._last_seed_decision = "preferred_shop"
                    self._last_seed_decision_reason = f"偏好种子 {preferred_seed_id} 已从商店可购买列表命中。"

        if not seed:
            seed = await self.farm.choose_seed(
                current_level=current_level,
                strategy=strategy or "preferred",
                preferred_seed_id=preferred_seed_id,
            )
            if seed:
                self._last_seed_decision = "strategy"
                self._last_seed_decision_reason = f"按策略 {strategy or 'preferred'} 选择种子。"

        if not seed:
            self._debug_log(
                "farm",
                "seed pick failed from shop candidates, try bag fallback",
                module="farm",
                event="seed_pick_failed",
                targetCount=len(lands_to_plant),
            )
            seed = await self._pick_seed_from_bag(
                current_level=current_level,
                preferred_seed_id=preferred_seed_id,
                preferred_only=False,
            )
            if seed:
                self._last_seed_decision = "strategy_fallback_bag"
                self._last_seed_decision_reason = "商店候选不可用，已回退背包库存种子。"

        if not seed:
            self._last_plant_skip_reason = "没有可用种子（请先执行 qfarm 种子 列表 / qfarm 设置 种子 <seedId>）"
            self._debug_log(
                "farm",
                "skip auto plant: no available seed",
                module="farm",
                event="seed_unavailable",
                targetCount=len(lands_to_plant),
            )
            return 0

        seed_id = _to_int(seed.get("seedId"), 0)
        config_data = getattr(self, "config_data", None)
        fallback_seed_name = ""
        if config_data is not None and hasattr(config_data, "get_plant_name_by_seed"):
            try:
                fallback_seed_name = str(config_data.get_plant_name_by_seed(seed_id) or "")
            except Exception:
                fallback_seed_name = ""
        seed_name = str(seed.get("name") or fallback_seed_name or f"seed-{seed_id}")
        self._last_selected_seed_id = seed_id
        self._last_selected_seed_name = seed_name
        if preferred_seed_id > 0 and seed_id > 0 and seed_id != preferred_seed_id:
            self._last_seed_decision_reason = (
                f"偏好种子 {preferred_seed_id} 当前不可用，已回退为 {seed_name}({seed_id})。"
            )
            self._debug_log(
                "farm",
                "preferred seed unavailable, fallback to another seed",
                module="farm",
                event="seed_preferred_fallback",
                preferredSeedId=preferred_seed_id,
                selectedSeedId=seed_id,
            )

        goods_id = _to_int(seed.get("goodsId"), 0)
        price = _to_int(seed.get("price"), 0)
        target_count = len(lands_to_plant)
        seed_stock_override = _to_int(seed.get("_bagStock"), -1)
        seed_stock = seed_stock_override if seed_stock_override >= 0 else await self._get_seed_stock(seed_id)
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
                    self._last_plant_skip_reason = "种子库存不足且金币不足，无法购买种子"
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

        bought_seed_count = 0
        optimistic_stock_after_buy: int | None = None
        if goods_id > 0 and price > 0 and buy_count > 0:
            try:
                buy_reply = await self.farm.buy_goods(goods_id, buy_count, price)
                if buy_count > 0:
                    self.user_state["gold"] = max(0, _to_int(self.user_state.get("gold"), 0) - (price * buy_count))
                parsed_buy_count = 0
                if hasattr(buy_reply, "get_items"):
                    for item in list(getattr(buy_reply, "get_items", []) or []):
                        got_id = _to_int(getattr(item, "id", 0), 0)
                        got_count = max(0, _to_int(getattr(item, "count", 0), 0))
                        if got_id > 0:
                            seed_id = got_id
                        if got_id <= 0 or got_count <= 0:
                            continue
                        if got_id == seed_id:
                            parsed_buy_count += got_count
                # 兼容服务端仅返回成功状态、不返回 get_items 明细的情况，避免因库存回写延迟导致少种。
                bought_seed_count = parsed_buy_count if parsed_buy_count > 0 else buy_count
                if seed_stock is not None:
                    optimistic_stock_after_buy = max(0, seed_stock) + max(0, bought_seed_count)
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
                        self._last_plant_skip_reason = "购买种子失败且背包无可用种子"
                        return 0
                    lands_to_plant = lands_to_plant[:fallback_count]
        post_stock = await self._get_seed_stock(seed_id)
        if post_stock is not None:
            effective_stock = max(0, post_stock)
            if optimistic_stock_after_buy is not None and effective_stock < optimistic_stock_after_buy:
                effective_stock = optimistic_stock_after_buy
            if effective_stock <= 0:
                self._last_plant_skip_reason = (
                    f"背包种子库存为0(seedId={seed_id})，请先检查 `qfarm 背包 查看` 或更换 `qfarm 设置 种子 <seedId>`"
                )
                return 0
            if effective_stock < len(lands_to_plant):
                lands_to_plant = lands_to_plant[:effective_stock]
        planted = await self.farm.plant(seed_id, lands_to_plant)
        if planted <= 0 and lands_to_plant:
            last_error = str(getattr(self.farm, "last_plant_error", "") or "").strip()
            failures = getattr(self.farm, "last_plant_failures", [])
            if isinstance(failures, list) and failures:
                first = failures[0] if isinstance(failures[0], dict) else {}
                land_id = _to_int(first.get("landId"), 0)
                err = str(first.get("error") or "").strip()
                if land_id > 0 and err:
                    self._last_plant_skip_reason = f"种植失败: 地块#{land_id} {err}"
                elif err:
                    self._last_plant_skip_reason = f"种植失败: {err}"
                elif last_error:
                    self._last_plant_skip_reason = f"种植失败: {last_error}"
                else:
                    self._last_plant_skip_reason = "种植失败: 服务端返回空错误，请重试并查看 /qfarm 日志 50 module=farm"
            elif last_error:
                self._last_plant_skip_reason = f"种植失败: {last_error}"
            else:
                self._last_plant_skip_reason = "种植请求已发送，但未成功种植任何地块"
        if planted > 0:
            self._record("plant", planted)
            mode = str(self._automation().get("fertilizer") or "both")
            planted_ids = lands_to_plant[:planted]
            if mode in {"normal", "both"}:
                self._record("fertilize", await self.farm.fertilize(planted_ids, 1011))
            if mode in {"organic", "both"}:
                self._record("fertilize", await self.farm.fertilize(planted_ids, 1012))
        return planted

    async def _pick_preferred_seed_from_shop(self, *, current_level: int, preferred_seed_id: int) -> dict[str, Any] | None:
        target_seed_id = max(0, _to_int(preferred_seed_id, 0))
        if target_seed_id <= 0:
            return None
        try:
            seeds = await self.farm.get_available_seeds(current_level=max(1, _to_int(current_level, 1)))
        except Exception as e:
            self._debug_log(
                "farm",
                f"preferred seed lookup failed: {e}",
                module="farm",
                event="seed_preferred_lookup_failed",
                preferredSeedId=target_seed_id,
            )
            return None
        for row in list(seeds or []):
            if not isinstance(row, dict):
                continue
            if _to_int(row.get("seedId"), 0) != target_seed_id:
                continue
            if bool(row.get("locked")) or bool(row.get("soldOut")):
                return None
            return dict(row)
        return None

    async def _pick_seed_from_bag(
        self,
        *,
        current_level: int,
        preferred_seed_id: int = 0,
        preferred_only: bool = False,
    ) -> dict[str, Any] | None:
        try:
            seeds = await self.farm.get_available_seeds(current_level=max(1, _to_int(current_level, 1)))
        except Exception as e:
            self._debug_log(
                "farm",
                f"seed pick fallback failed: {e}",
                module="farm",
                event="seed_pick_failed",
                reason="get_available_seeds_failed",
            )
            return None
        warehouse = getattr(self, "warehouse", None)
        if not warehouse or not hasattr(warehouse, "get_bag") or not hasattr(warehouse, "get_bag_items"):
            return None
        try:
            bag = await warehouse.get_bag()
            items = warehouse.get_bag_items(bag)
        except Exception as e:
            self._debug_log(
                "farm",
                f"seed pick fallback failed: {e}",
                module="farm",
                event="seed_pick_failed",
                reason="get_bag_failed",
            )
            return None
        stock_by_seed: dict[int, int] = {}
        for item in list(items or []):
            seed_id = _to_int(getattr(item, "id", 0), 0)
            if seed_id <= 0:
                continue
            stock_by_seed[seed_id] = _to_int(stock_by_seed.get(seed_id), 0) + max(0, _to_int(getattr(item, "count", 0), 0))
        preferred_seed_id = max(0, _to_int(preferred_seed_id, 0))
        candidates: list[tuple[int, int, int, dict[str, Any]]] = []
        for row in list(seeds or []):
            if not isinstance(row, dict):
                continue
            seed_id = _to_int(row.get("seedId"), 0)
            if seed_id <= 0:
                continue
            if bool(row.get("locked")):
                continue
            if preferred_only and seed_id != preferred_seed_id:
                continue
            stock = max(0, _to_int(stock_by_seed.get(seed_id), 0))
            if stock <= 0:
                continue
            required_level = max(0, _to_int(row.get("requiredLevel"), 0))
            candidates.append((0 if seed_id == preferred_seed_id else 1, -required_level, -seed_id, dict(row)))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        selected = dict(candidates[0][3])
        selected["_bagStock"] = max(0, _to_int(stock_by_seed.get(_to_int(selected.get("seedId"), 0)), 0))
        event = "seed_pick_from_bag_preferred" if preferred_only else "seed_pick_from_bag"
        self._debug_log(
            "farm",
            "seed picked from bag fallback",
            module="farm",
            event=event,
            seedId=_to_int(selected.get("seedId"), 0),
            stock=_to_int(selected.get("_bagStock"), 0),
            preferredOnly=bool(preferred_only),
        )
        return selected

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

    @staticmethod
    def _parse_mall_price_value(raw: Any) -> int:
        if raw is None:
            return 0
        if isinstance(raw, int):
            return max(0, int(raw))
        if isinstance(raw, (bytes, bytearray)):
            data = bytes(raw)
        else:
            try:
                data = bytes(raw or b"")
            except Exception:
                return 0
        if not data:
            return 0
        idx = 0
        parsed = 0
        length = len(data)
        while idx < length:
            key = data[idx]
            idx += 1
            field = key >> 3
            wire = key & 0x07
            if wire != 0:
                break
            value = 0
            shift = 0
            while idx < length:
                b = data[idx]
                idx += 1
                value |= (b & 0x7F) << shift
                if (b & 0x80) == 0:
                    break
                shift += 7
            if field == 2:
                parsed = value
        return max(0, int(parsed))

    @staticmethod
    def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
        value = 0
        shift = 0
        idx = max(0, _to_int(offset, 0))
        length = len(data)
        while idx < length:
            byte = data[idx]
            idx += 1
            value |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                return value, idx
            shift += 7
            if shift > 63:
                raise ValueError("varint is too long")
        raise ValueError("unexpected EOF while reading varint")

    @classmethod
    def _skip_wire_value(cls, data: bytes, offset: int, wire_type: int) -> int:
        idx = max(0, _to_int(offset, 0))
        length = len(data)
        if wire_type == 0:  # varint
            _, idx = cls._read_varint(data, idx)
            return idx
        if wire_type == 1:  # 64-bit
            idx += 8
            if idx > length:
                raise ValueError("unexpected EOF while skipping fixed64")
            return idx
        if wire_type == 2:  # length-delimited
            value_len, idx = cls._read_varint(data, idx)
            idx += value_len
            if idx > length:
                raise ValueError("unexpected EOF while skipping bytes")
            return idx
        if wire_type == 5:  # 32-bit
            idx += 4
            if idx > length:
                raise ValueError("unexpected EOF while skipping fixed32")
            return idx
        raise ValueError(f"unsupported wire type: {wire_type}")

    @classmethod
    def _extract_basic_notify_present_fields(cls, payload: bytes) -> set[int]:
        if not isinstance(payload, (bytes, bytearray)):
            return set()
        data = bytes(payload)
        idx = 0
        basic_payload = b""
        while idx < len(data):
            key, idx = cls._read_varint(data, idx)
            field_no = key >> 3
            wire_type = key & 0x07
            if field_no == 1 and wire_type == 2:
                value_len, idx = cls._read_varint(data, idx)
                end = idx + value_len
                if end > len(data):
                    raise ValueError("truncated basic payload")
                basic_payload = data[idx:end]
                idx = end
                continue
            idx = cls._skip_wire_value(data, idx, wire_type)
        if not basic_payload:
            return set()

        present_fields: set[int] = set()
        inner_idx = 0
        while inner_idx < len(basic_payload):
            key, inner_idx = cls._read_varint(basic_payload, inner_idx)
            field_no = key >> 3
            wire_type = key & 0x07
            if field_no > 0:
                present_fields.add(field_no)
            inner_idx = cls._skip_wire_value(basic_payload, inner_idx, wire_type)
        return present_fields

    def _today_key(self) -> str:
        now = time.localtime()
        return f"{now.tm_year:04d}-{now.tm_mon:02d}-{now.tm_mday:02d}"

    def _normalize_daily_routines(self, raw: Any) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        if not isinstance(raw, dict):
            return result
        for key, value in raw.items():
            routine_key = str(key or "").strip()
            if not routine_key or not isinstance(value, dict):
                continue
            result[routine_key] = {
                "doneDateKey": str(value.get("doneDateKey") or ""),
                "lastCheckAt": max(0, _to_int(value.get("lastCheckAt"), 0)),
                "lastClaimAt": max(0, _to_int(value.get("lastClaimAt"), 0)),
                "lastResult": str(value.get("lastResult") or ""),
                "lastError": str(value.get("lastError") or ""),
            }
        return result

    def _daily_routines_snapshot(self) -> dict[str, dict[str, Any]]:
        data: dict[str, dict[str, Any]] = {}
        for key, value in self._daily_routines.items():
            if not isinstance(value, dict):
                continue
            data[str(key)] = {
                "doneDateKey": str(value.get("doneDateKey") or ""),
                "lastCheckAt": max(0, _to_int(value.get("lastCheckAt"), 0)),
                "lastClaimAt": max(0, _to_int(value.get("lastClaimAt"), 0)),
                "lastResult": str(value.get("lastResult") or ""),
                "lastError": str(value.get("lastError") or ""),
            }
        return data

    def _routine_state(self, key: str) -> dict[str, Any]:
        routine_key = str(key or "").strip()
        if not routine_key:
            return {"doneDateKey": "", "lastCheckAt": 0, "lastClaimAt": 0, "lastResult": "", "lastError": ""}
        current = self._daily_routines.get(routine_key)
        if not isinstance(current, dict):
            current = {"doneDateKey": "", "lastCheckAt": 0, "lastClaimAt": 0, "lastResult": "", "lastError": ""}
            self._daily_routines[routine_key] = current
        return {
            "doneDateKey": str(current.get("doneDateKey") or ""),
            "lastCheckAt": max(0, _to_int(current.get("lastCheckAt"), 0)),
            "lastClaimAt": max(0, _to_int(current.get("lastClaimAt"), 0)),
            "lastResult": str(current.get("lastResult") or ""),
            "lastError": str(current.get("lastError") or ""),
        }

    async def _persist_daily_routines(self) -> None:
        if not self.runtime_state_persist:
            return
        try:
            await self.runtime_state_persist({"dailyRoutines": self._daily_routines_snapshot()})
        except Exception as e:
            self._debug_log("daily", f"persist daily routines failed: {e}", module="task", event="daily_state_persist", result="error")

    async def _routine_can_run(self, key: str, cooldown_sec: int, force: bool) -> bool:
        routine_key = str(key or "").strip()
        if not routine_key:
            return False
        state = self._routine_state(routine_key)
        now_ms = int(time.time() * 1000)
        if not force and state.get("doneDateKey") == self._today_key():
            return False
        last_check_at = max(0, _to_int(state.get("lastCheckAt"), 0))
        if not force and str(state.get("lastResult") or "") == "error":
            if now_ms - last_check_at < DAILY_ROUTINE_ERROR_BACKOFF_SEC * 1000:
                return False
            return True
        if not force and now_ms - last_check_at < max(1, int(cooldown_sec)) * 1000:
            return False
        return True

    async def _mark_routine_done(self, key: str, *, result: str, claimed: bool = False, error: str = "") -> None:
        routine_key = str(key or "").strip()
        if not routine_key:
            return
        now_ms = int(time.time() * 1000)
        state = self._routine_state(routine_key)
        self._daily_routines[routine_key] = {
            **state,
            "doneDateKey": self._today_key(),
            "lastCheckAt": now_ms,
            "lastResult": str(result or ""),
            "lastError": str(error or ""),
            "lastClaimAt": now_ms if claimed else max(0, _to_int(state.get("lastClaimAt"), 0)),
        }
        await self._persist_daily_routines()

    async def _mark_routine_error(self, key: str, error: str) -> None:
        routine_key = str(key or "").strip()
        if not routine_key:
            return
        now_ms = int(time.time() * 1000)
        state = self._routine_state(routine_key)
        self._daily_routines[routine_key] = {
            **state,
            "lastCheckAt": now_ms,
            "lastResult": "error",
            "lastError": str(error or ""),
        }
        await self._persist_daily_routines()

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
                present_fields: set[int] | None = None
                try:
                    present_fields = self._extract_basic_notify_present_fields(payload)
                except Exception as e:
                    self._debug_log(
                        "farm",
                        f"basic notify presence parse failed: {e}",
                        module="farm",
                        event="basic_presence_parse_failed",
                    )
                next_level = _to_int(basic.level, -1)
                if next_level > 0:
                    self.user_state["level"] = next_level
                elif next_level <= 0:
                    current_level = _to_int(self.user_state.get("level"), 0)
                    if current_level > 0:
                        self._debug_log(
                            "farm",
                            f"ignore invalid basic level update: recv={next_level}, keep={current_level}",
                            module="farm",
                            event="basic_level_ignored",
                            recvLevel=next_level,
                            keepLevel=current_level,
                        )
                if present_fields is not None and 5 in present_fields and _to_int(basic.gold, -1) >= 0:
                    self.user_state["gold"] = _to_int(basic.gold)
                if present_fields is not None and 4 in present_fields and _to_int(basic.exp, -1) >= 0:
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

    @staticmethod
    def _format_core_items(items: list[Any]) -> list[dict[str, int]]:
        rows: list[dict[str, int]] = []
        for item in list(items or []):
            rows.append(
                {
                    "id": _to_int(getattr(item, "id", 0), 0),
                    "count": _to_int(getattr(item, "count", 0), 0),
                }
            )
        return rows

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
