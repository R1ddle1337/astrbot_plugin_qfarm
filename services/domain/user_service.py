from __future__ import annotations

from typing import Any

from ..protocol.session import GatewaySession
from ..protocol.proto import userpb_pb2


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


class UserService:
    def __init__(self, session: GatewaySession, *, rpc_timeout_sec: int = 10) -> None:
        self.session = session
        self.rpc_timeout_sec = max(1, int(rpc_timeout_sec))

    async def login(self, client_version: str) -> userpb_pb2.LoginReply:
        req = userpb_pb2.LoginRequest(
            sharer_id=0,
            sharer_open_id="",
            device_info=userpb_pb2.DeviceInfo(
                client_version=str(client_version or "1.6.0.5_20251224"),
                sys_software="iOS 26.2.1",
                network="wifi",
                memory=7672,
                device_id="iPhone X<iPhone18,3>",
            ),
            share_cfg_id=0,
            scene_id="1256",
            report_data=userpb_pb2.ReportData(
                callback="",
                cd_extend_info="",
                click_id="",
                clue_token="",
                minigame_channel="other",
                minigame_platid=2,
                req_id="",
                trackid="",
            ),
        )
        body = await self.session.call(
            "gamepb.userpb.UserService",
            "Login",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = userpb_pb2.LoginReply()
        reply.ParseFromString(body)
        return reply

    async def heartbeat(self, gid: int, client_version: str) -> userpb_pb2.HeartbeatReply:
        req = userpb_pb2.HeartbeatRequest(
            gid=_to_int(gid, 0),
            client_version=str(client_version or "1.6.0.5_20251224"),
        )
        body = await self.session.call(
            "gamepb.userpb.UserService",
            "Heartbeat",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = userpb_pb2.HeartbeatReply()
        reply.ParseFromString(body)
        return reply

    async def report_ark_click(
        self,
        sharer_id: int,
        sharer_open_id: str = "",
        share_cfg_id: int = 0,
        scene_id: str = "",
    ) -> userpb_pb2.ReportArkClickReply:
        req = userpb_pb2.ReportArkClickRequest(
            sharer_id=_to_int(sharer_id, 0),
            sharer_open_id=str(sharer_open_id or ""),
            share_cfg_id=_to_int(share_cfg_id, 0),
            scene_id=str(scene_id or ""),
        )
        body = await self.session.call(
            "gamepb.userpb.UserService",
            "ReportArkClick",
            req.SerializeToString(),
            timeout_sec=self.rpc_timeout_sec,
        )
        reply = userpb_pb2.ReportArkClickReply()
        reply.ParseFromString(body)
        return reply
