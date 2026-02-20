from __future__ import annotations

import asyncio
from typing import Any

from ..protocol.session import GatewaySession
from ..protocol.proto import taskpb_pb2


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


class TaskService:
    def __init__(self, session: GatewaySession, *, rpc_timeout_sec: int = 10) -> None:
        self.session = session
        self.rpc_timeout_sec = max(1, int(rpc_timeout_sec))

    async def get_task_info(self) -> taskpb_pb2.TaskInfoReply:
        req = taskpb_pb2.TaskInfoRequest()
        body = await self.session.call(
            "gamepb.taskpb.TaskService",
            "TaskInfo",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = taskpb_pb2.TaskInfoReply()
        reply.ParseFromString(body)
        return reply

    async def claim_task_reward(self, task_id: int, do_shared: bool = False) -> taskpb_pb2.ClaimTaskRewardReply:
        req = taskpb_pb2.ClaimTaskRewardRequest(
            id=_to_int(task_id, 0),
            do_shared=bool(do_shared),
        )
        body = await self.session.call(
            "gamepb.taskpb.TaskService",
            "ClaimTaskReward",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = taskpb_pb2.ClaimTaskRewardReply()
        reply.ParseFromString(body)
        return reply

    async def claim_daily_reward(self, active_type: int, point_ids: list[int]) -> taskpb_pb2.ClaimDailyRewardReply:
        req = taskpb_pb2.ClaimDailyRewardRequest(
            type=_to_int(active_type, 0),
            point_ids=[_to_int(v, 0) for v in point_ids if _to_int(v, 0) > 0],
        )
        body = await self.session.call(
            "gamepb.taskpb.TaskService",
            "ClaimDailyReward",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = taskpb_pb2.ClaimDailyRewardReply()
        reply.ParseFromString(body)
        return reply

    async def get_all_tasks(self) -> dict[str, list[dict[str, Any]]]:
        try:
            reply = await self.get_task_info()
        except Exception:
            return {"daily": [], "growth": [], "main": []}
        if not reply.HasField("task_info"):
            return {"daily": [], "growth": [], "main": []}
        task_info = reply.task_info
        return {
            "daily": [self.format_task(task) for task in task_info.daily_tasks],
            "growth": [self.format_task(task) for task in task_info.growth_tasks],
            "main": [self.format_task(task) for task in task_info.tasks],
        }

    def format_task(self, task: taskpb_pb2.Task) -> dict[str, Any]:
        progress = _to_int(task.progress, 0)
        total = _to_int(task.total_progress, 0)
        can_claim = bool(task.is_unlocked and not task.is_claimed and total > 0 and progress >= total)
        return {
            "id": _to_int(task.id, 0),
            "desc": str(task.desc or f"任务#{_to_int(task.id, 0)}"),
            "progress": progress,
            "totalProgress": total,
            "isClaimed": bool(task.is_claimed),
            "isUnlocked": bool(task.is_unlocked),
            "shareMultiple": _to_int(task.share_multiple, 0),
            "rewards": [{"id": _to_int(item.id, 0), "count": _to_int(item.count, 0)} for item in task.rewards],
            "canClaim": can_claim,
        }

    async def check_and_claim_tasks(self) -> dict[str, Any]:
        result = {
            "taskClaimed": 0,
            "activeClaimed": 0,
            "taskItems": [],
            "activeItems": [],
        }
        reply = await self.get_task_info()
        if not reply.HasField("task_info"):
            return result
        info = reply.task_info
        claimable = self._collect_claimable_tasks(info)
        for task in claimable:
            shared = _to_int(task.share_multiple, 0) > 1
            try:
                claimed = await self.claim_task_reward(_to_int(task.id, 0), shared)
                result["taskClaimed"] += 1
                result["taskItems"].extend(self._format_items(claimed.items))
            except Exception:
                continue
            await asyncio.sleep(0.2)
        active_done, active_items = await self._claim_actives(info.actives)
        result["activeClaimed"] = active_done
        result["activeItems"] = active_items
        return result

    def _collect_claimable_tasks(self, info: taskpb_pb2.TaskInfo) -> list[taskpb_pb2.Task]:
        rows: list[taskpb_pb2.Task] = []
        all_rows = list(info.growth_tasks) + list(info.daily_tasks) + list(info.tasks)
        for task in all_rows:
            progress = _to_int(task.progress, 0)
            total = _to_int(task.total_progress, 0)
            if bool(task.is_unlocked) and (not bool(task.is_claimed)) and total > 0 and progress >= total:
                rows.append(task)
        return rows

    async def _claim_actives(self, actives: list[taskpb_pb2.Active]) -> tuple[int, list[dict[str, int]]]:
        claimed = 0
        item_rows: list[dict[str, int]] = []
        for active in actives:
            point_ids = [
                _to_int(reward.point_id, 0)
                for reward in active.rewards
                if _to_int(reward.status, 0) == taskpb_pb2.DONE and _to_int(reward.point_id, 0) > 0
            ]
            if not point_ids:
                continue
            try:
                reply = await self.claim_daily_reward(_to_int(active.type, 0), point_ids)
                claimed += len(point_ids)
                item_rows.extend(self._format_items(reply.items))
            except Exception:
                continue
            await asyncio.sleep(0.2)
        return claimed, item_rows

    @staticmethod
    def _format_items(items: list[Any]) -> list[dict[str, int]]:
        rows: list[dict[str, int]] = []
        for item in items:
            rows.append({"id": _to_int(getattr(item, "id", 0), 0), "count": _to_int(getattr(item, "count", 0), 0)})
        return rows
