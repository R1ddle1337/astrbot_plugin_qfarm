from __future__ import annotations

import logging

import pytest

from astrbot_plugin_qfarm.services.domain import friend_service as friend_service_module
from astrbot_plugin_qfarm.services.domain.friend_service import FriendService
from astrbot_plugin_qfarm.services.domain.task_service import TaskService
from astrbot_plugin_qfarm.services.protocol.proto import taskpb_pb2


class _FakeSession:
    def __init__(
        self,
        *,
        responses: dict[tuple[str, str], bytes] | None = None,
        errors: dict[tuple[str, str], Exception] | None = None,
    ) -> None:
        self.responses = dict(responses or {})
        self.errors = dict(errors or {})

    async def call(self, service_name: str, method_name: str, body: bytes, timeout_sec: int = 10) -> bytes:
        key = (service_name, method_name)
        if key in self.errors:
            raise self.errors[key]
        if key not in self.responses:
            raise AssertionError(f"unexpected call: {key}")
        return self.responses[key]


@pytest.mark.asyncio
async def test_check_can_operate_remote_safe_fail_and_logs(caplog: pytest.LogCaptureFixture):
    fake = _FakeSession(
        errors={
            ("gamepb.plantpb.PlantService", "CheckCanOperate"): RuntimeError("rpc unavailable"),
        }
    )
    service = FriendService(fake, config_data=object(), rpc_timeout_sec=8)

    caplog.set_level(logging.DEBUG, logger=friend_service_module.__name__)
    can_operate, can_steal_num = await service.check_can_operate_remote(12345, 10008)

    assert can_operate is False
    assert can_steal_num == 0
    assert any(
        record.levelno == logging.WARNING and "check_can_operate_remote failed, deny operation" in record.getMessage()
        for record in caplog.records
    )
    assert any(
        record.levelno == logging.DEBUG and "check_can_operate_remote traceback" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_get_friends_list_raises_upstream_error():
    fake = _FakeSession(
        errors={
            ("gamepb.friendpb.FriendService", "GetAll"): RuntimeError("friend list rpc timeout"),
        }
    )
    service = FriendService(fake, config_data=object(), rpc_timeout_sec=6)

    with pytest.raises(RuntimeError, match="friend list rpc timeout"):
        await service.get_friends_list(my_gid=1)


@pytest.mark.asyncio
async def test_get_all_tasks_raises_diagnostic_error():
    fake = _FakeSession(
        errors={
            ("gamepb.taskpb.TaskService", "TaskInfo"): RuntimeError("task info rpc timeout"),
        }
    )
    service = TaskService(fake, rpc_timeout_sec=6)

    with pytest.raises(RuntimeError, match="get_all_tasks failed: task info rpc timeout") as exc:
        await service.get_all_tasks()
    assert isinstance(exc.value.__cause__, RuntimeError)
    assert str(exc.value.__cause__) == "task info rpc timeout"


@pytest.mark.asyncio
async def test_get_all_tasks_keeps_empty_when_task_info_missing():
    reply = taskpb_pb2.TaskInfoReply()
    fake = _FakeSession(
        responses={
            ("gamepb.taskpb.TaskService", "TaskInfo"): reply.SerializeToString(),
        }
    )
    service = TaskService(fake, rpc_timeout_sec=6)

    assert await service.get_all_tasks() == {"daily": [], "growth": [], "main": []}
