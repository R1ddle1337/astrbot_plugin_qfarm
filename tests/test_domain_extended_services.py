from __future__ import annotations

import pytest

from astrbot_plugin_qfarm.services.domain.email_service import EmailService
from astrbot_plugin_qfarm.services.domain.mall_service import MallService
from astrbot_plugin_qfarm.services.domain.monthcard_service import MonthCardService
from astrbot_plugin_qfarm.services.domain.share_service import ShareService
from astrbot_plugin_qfarm.services.domain.vip_service import VipService
from astrbot_plugin_qfarm.services.protocol.proto import (
    corepb_pb2,
    emailpb_pb2,
    mallpb_pb2,
    qqvippb_pb2,
    sharepb_pb2,
)


class _FakeSession:
    def __init__(self, responses: dict[tuple[str, str], bytes]) -> None:
        self.responses = dict(responses)
        self.calls: list[tuple[str, str, bytes, int]] = []

    async def call(self, service_name: str, method_name: str, body: bytes, timeout_sec: int = 10) -> bytes:
        self.calls.append((service_name, method_name, body, int(timeout_sec)))
        key = (service_name, method_name)
        if key not in self.responses:
            raise AssertionError(f"unexpected call: {key}")
        return self.responses[key]


@pytest.mark.asyncio
async def test_email_service_get_and_claim():
    list_reply = emailpb_pb2.GetEmailListReply(
        emails=[
            emailpb_pb2.EmailItem(
                id="mail-1",
                mail_type=1,
                title="daily",
                claimed=False,
                has_reward=True,
                subtitle="sub",
            )
        ]
    )
    claim_reply = emailpb_pb2.ClaimEmailReply(items=[corepb_pb2.Item(id=1001, count=8)])
    fake = _FakeSession(
        {
            ("gamepb.emailpb.EmailService", "GetEmailList"): list_reply.SerializeToString(),
            ("gamepb.emailpb.EmailService", "ClaimEmail"): claim_reply.SerializeToString(),
        }
    )
    service = EmailService(fake, rpc_timeout_sec=7)

    got = await service.get_email_list(2)
    req = emailpb_pb2.GetEmailListRequest()
    req.ParseFromString(fake.calls[0][2])
    assert req.box_type == 2
    assert got.emails[0].id == "mail-1"

    claimed = await service.claim_email(1, "mail-1")
    req2 = emailpb_pb2.ClaimEmailRequest()
    req2.ParseFromString(fake.calls[1][2])
    assert req2.box_type == 1
    assert req2.email_id == "mail-1"
    assert claimed.items[0].id == 1001


@pytest.mark.asyncio
async def test_mall_service_goods_decode_and_purchase():
    goods = mallpb_pb2.MallGoods(goods_id=1002, name="organic", type=1, is_free=False, is_limited=True)
    list_reply = mallpb_pb2.GetMallListBySlotTypeResponse(goods_list=[goods.SerializeToString()])
    purchase_reply = mallpb_pb2.PurchaseResponse(goods_id=1002, count=3)
    fake = _FakeSession(
        {
            ("gamepb.mallpb.MallService", "GetMallListBySlotType"): list_reply.SerializeToString(),
            ("gamepb.mallpb.MallService", "Purchase"): purchase_reply.SerializeToString(),
        }
    )
    service = MallService(fake, rpc_timeout_sec=9)

    decoded = await service.get_mall_goods_list(1)
    req = mallpb_pb2.GetMallListBySlotTypeRequest()
    req.ParseFromString(fake.calls[0][2])
    assert req.slot_type == 1
    assert len(decoded) == 1
    assert decoded[0].goods_id == 1002

    result = await service.purchase(1002, 3)
    req2 = mallpb_pb2.PurchaseRequest()
    req2.ParseFromString(fake.calls[1][2])
    assert req2.goods_id == 1002
    assert req2.count == 3
    assert result.count == 3


@pytest.mark.asyncio
async def test_monthcard_service_calls_mall_methods():
    infos_reply = mallpb_pb2.GetMonthCardInfosReply(
        infos=[mallpb_pb2.MonthCardInfo(goods_id=11, can_claim=True)]
    )
    claim_reply = mallpb_pb2.ClaimMonthCardRewardReply(items=[corepb_pb2.Item(id=2, count=5)])
    fake = _FakeSession(
        {
            ("gamepb.mallpb.MallService", "GetMonthCardInfos"): infos_reply.SerializeToString(),
            ("gamepb.mallpb.MallService", "ClaimMonthCardReward"): claim_reply.SerializeToString(),
        }
    )
    service = MonthCardService(fake)

    infos = await service.get_month_card_infos()
    claim = await service.claim_month_card_reward(11)
    req2 = mallpb_pb2.ClaimMonthCardRewardRequest()
    req2.ParseFromString(fake.calls[1][2])
    assert infos.infos[0].goods_id == 11
    assert req2.goods_id == 11
    assert claim.items[0].count == 5


@pytest.mark.asyncio
async def test_vip_service_status_and_claim():
    status_reply = qqvippb_pb2.GetDailyGiftStatusReply(can_claim=True, has_gift=True)
    claim_reply = qqvippb_pb2.ClaimDailyGiftReply(items=[corepb_pb2.Item(id=1002, count=1)])
    fake = _FakeSession(
        {
            ("gamepb.qqvippb.QQVipService", "GetDailyGiftStatus"): status_reply.SerializeToString(),
            ("gamepb.qqvippb.QQVipService", "ClaimDailyGift"): claim_reply.SerializeToString(),
        }
    )
    service = VipService(fake)

    status = await service.get_daily_gift_status()
    claim = await service.claim_daily_gift()
    assert status.can_claim is True
    assert status.has_gift is True
    assert claim.items[0].id == 1002


@pytest.mark.asyncio
async def test_share_service_check_report_claim():
    can_reply = sharepb_pb2.CheckCanShareReply(can_share=True)
    report_reply = sharepb_pb2.ReportShareReply(success=True)
    claim_reply = sharepb_pb2.ClaimShareRewardReply(
        success=True,
        has_reward=True,
        items=[corepb_pb2.Item(id=1, count=88)],
    )
    fake = _FakeSession(
        {
            ("gamepb.sharepb.ShareService", "CheckCanShare"): can_reply.SerializeToString(),
            ("gamepb.sharepb.ShareService", "ReportShare"): report_reply.SerializeToString(),
            ("gamepb.sharepb.ShareService", "ClaimShareReward"): claim_reply.SerializeToString(),
        }
    )
    service = ShareService(fake)

    can = await service.check_can_share()
    report = await service.report_share(True)
    claim = await service.claim_share_reward(True)
    assert can.can_share is True
    assert report.success is True
    assert claim.has_reward is True
    assert claim.items[0].count == 88
