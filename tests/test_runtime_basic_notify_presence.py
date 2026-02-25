from __future__ import annotations

import pytest

from astrbot_plugin_qfarm.services.protocol.proto import userpb_pb2
from astrbot_plugin_qfarm.services.runtime.account_runtime import AccountRuntime


@pytest.mark.asyncio
async def test_basic_notify_level_only_does_not_overwrite_gold_exp():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.account = {"id": "acc-1"}
    runtime.logger = None
    runtime.log_callback = None
    runtime.user_state = {"level": 3, "gold": 12345, "exp": 67890}

    notify = userpb_pb2.BasicNotify()
    notify.basic.level = 8

    await runtime._on_notify("BasicNotify", notify.SerializeToString())

    assert runtime.user_state["level"] == 8
    assert runtime.user_state["gold"] == 12345
    assert runtime.user_state["exp"] == 67890


@pytest.mark.asyncio
async def test_basic_notify_explicit_zero_fields_are_applied():
    runtime = AccountRuntime.__new__(AccountRuntime)
    runtime.account = {"id": "acc-1"}
    runtime.logger = None
    runtime.log_callback = None
    runtime.user_state = {"level": 1, "gold": 999, "exp": 555}

    # BasicNotify {
    #   basic {
    #     level = 9
    #     exp = 0
    #     gold = 0
    #   }
    # }
    payload = b"\x0a\x06\x18\x09\x20\x00\x28\x00"
    await runtime._on_notify("gamepb.userpb.BasicNotify", payload)

    assert runtime.user_state["level"] == 9
    assert runtime.user_state["gold"] == 0
    assert runtime.user_state["exp"] == 0
