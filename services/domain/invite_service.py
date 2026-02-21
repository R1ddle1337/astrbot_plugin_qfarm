from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from .user_service import UserService


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


class InviteService:
    REQUEST_DELAY_SEC = 2.0

    def __init__(
        self,
        user_service: UserService,
        *,
        platform: str = "qq",
        share_file_path: Path | None = None,
        logger: Any | None = None,
        log_callback: Any | None = None,
    ) -> None:
        self.user_service = user_service
        self.platform = str(platform or "qq").strip().lower()
        self.share_file_path = Path(share_file_path) if share_file_path else None
        self.logger = logger
        self.log_callback = log_callback

    @staticmethod
    def parse_share_link(link: str) -> dict[str, str]:
        text = str(link or "").strip()
        if not text:
            return {"uid": "", "openid": "", "share_source": "", "doc_id": ""}
        if text.startswith("?"):
            text = text[1:]
        query = parse_qs(text, keep_blank_values=True)
        return {
            "uid": str((query.get("uid") or [""])[0] or ""),
            "openid": str((query.get("openid") or [""])[0] or ""),
            "share_source": str((query.get("share_source") or [""])[0] or ""),
            "doc_id": str((query.get("doc_id") or [""])[0] or ""),
        }

    def read_share_file(self) -> list[dict[str, str]]:
        path = self.share_file_path
        if not path or not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            self._log("invite", f"read share file failed: {e}", is_warn=True, event="share_read_failed")
            return []

        rows: list[dict[str, str]] = []
        seen_uid: set[str] = set()
        for line in lines:
            raw = str(line or "").strip()
            if not raw or "openid=" not in raw:
                continue
            parsed = self.parse_share_link(raw)
            uid = parsed.get("uid", "").strip()
            openid = parsed.get("openid", "").strip()
            if not uid or not openid or uid in seen_uid:
                continue
            seen_uid.add(uid)
            rows.append(parsed)
        return rows

    def clear_share_file(self) -> None:
        path = self.share_file_path
        if not path:
            return
        try:
            path.write_text("", encoding="utf-8")
        except Exception:
            return

    async def process_invites(self) -> dict[str, Any]:
        if self.platform != "wx":
            self._log("invite", "skip invite process for non-wx platform", event="invite_skip_platform", platform=self.platform)
            return {"ok": True, "skipped": True, "reason": "platform_not_wx", "total": 0, "success": 0, "failed": 0}

        rows = self.read_share_file()
        if not rows:
            return {"ok": True, "skipped": True, "reason": "empty", "total": 0, "success": 0, "failed": 0}

        success = 0
        failed = 0
        for idx, row in enumerate(rows):
            uid = _to_int(row.get("uid"), 0)
            openid = str(row.get("openid") or "")
            share_source = _to_int(row.get("share_source"), 0)
            try:
                await self.user_service.report_ark_click(
                    sharer_id=uid,
                    sharer_open_id=openid,
                    share_cfg_id=share_source,
                    scene_id="1256",
                )
                success += 1
                self._log("invite", f"invite report ok uid={uid}", event="invite_report_ok", index=idx + 1, total=len(rows), uid=uid)
            except Exception as e:
                failed += 1
                self._log(
                    "invite",
                    f"invite report failed uid={uid}: {e}",
                    is_warn=True,
                    event="invite_report_failed",
                    index=idx + 1,
                    total=len(rows),
                    uid=uid,
                )
            if idx < len(rows) - 1:
                await asyncio.sleep(self.REQUEST_DELAY_SEC)

        self.clear_share_file()
        self._log("invite", f"invite process done success={success} failed={failed}", event="invite_done", success=success, failed=failed)
        return {"ok": True, "skipped": False, "total": len(rows), "success": success, "failed": failed}

    def _log(self, tag: str, message: str, *, is_warn: bool = False, **meta: Any) -> None:
        if self.logger:
            try:
                text = f"[qfarm-runtime] [{tag}] {message}"
                if is_warn and hasattr(self.logger, "warning"):
                    self.logger.warning(text)
                elif hasattr(self.logger, "info"):
                    self.logger.info(text)
            except Exception:
                pass
        if self.log_callback:
            try:
                self.log_callback(str(tag or ""), str(message or ""), bool(is_warn), dict(meta or {}))
            except Exception:
                pass
