from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.domain.invite_service import InviteService


class _FakeUserService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, int, str]] = []

    async def report_ark_click(
        self,
        sharer_id: int,
        sharer_open_id: str = "",
        share_cfg_id: int = 0,
        scene_id: str = "",
    ):
        self.calls.append((int(sharer_id), str(sharer_open_id), int(share_cfg_id), str(scene_id)))
        return {}


def test_parse_share_link():
    row = InviteService.parse_share_link("?uid=1001&openid=abc&share_source=77&doc_id=9")
    assert row["uid"] == "1001"
    assert row["openid"] == "abc"
    assert row["share_source"] == "77"
    assert row["doc_id"] == "9"


def test_read_share_file_dedup_by_uid(tmp_path: Path):
    share = tmp_path / "share.txt"
    share.write_text(
        "\n".join(
            [
                "?uid=1001&openid=aaa&share_source=1",
                "?uid=1001&openid=bbb&share_source=2",
                "?uid=1002&openid=ccc&share_source=3",
            ]
        ),
        encoding="utf-8",
    )
    service = InviteService(_FakeUserService(), platform="wx", share_file_path=share)
    rows = service.read_share_file()
    assert len(rows) == 2
    assert rows[0]["uid"] == "1001"
    assert rows[1]["uid"] == "1002"


@pytest.mark.asyncio
async def test_process_invites_skip_on_non_wx(tmp_path: Path):
    share = tmp_path / "share.txt"
    share.write_text("?uid=1001&openid=aaa&share_source=1", encoding="utf-8")
    fake = _FakeUserService()
    service = InviteService(fake, platform="qq", share_file_path=share)
    result = await service.process_invites()
    assert result["skipped"] is True
    assert result["reason"] == "platform_not_wx"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_process_invites_calls_report_and_clears_file(tmp_path: Path):
    share = tmp_path / "share.txt"
    share.write_text(
        "\n".join(
            [
                "?uid=2001&openid=openid-1&share_source=11",
                "?uid=2002&openid=openid-2&share_source=12",
            ]
        ),
        encoding="utf-8",
    )
    fake = _FakeUserService()
    service = InviteService(fake, platform="wx", share_file_path=share)
    service.REQUEST_DELAY_SEC = 0.0

    result = await service.process_invites()

    assert result["skipped"] is False
    assert result["total"] == 2
    assert result["success"] == 2
    assert fake.calls == [
        (2001, "openid-1", 11, "1256"),
        (2002, "openid-2", 12, "1256"),
    ]
    assert share.read_text(encoding="utf-8") == ""
