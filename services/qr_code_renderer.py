from __future__ import annotations

import io
import time
import uuid
from pathlib import Path

import segno


class QRCodeRenderError(RuntimeError):
    """Raised when local QR code render/save fails."""


def render_png_bytes(content: str) -> bytes:
    text = str(content or "").strip()
    if not text:
        raise QRCodeRenderError("二维码内容为空")

    try:
        qr = segno.make(text, error="m")
        buffer = io.BytesIO()
        qr.save(buffer, kind="png", scale=8, border=2, dark="#000000", light="#ffffff")
        payload = buffer.getvalue()
    except Exception as e:  # pragma: no cover - depends on segno internals
        raise QRCodeRenderError(f"二维码生成失败: {e}") from e

    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise QRCodeRenderError("二维码生成失败: 非 PNG 数据")
    return payload


def save_qr_png(content: str, cache_dir: Path) -> str:
    target_dir = Path(cache_dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise QRCodeRenderError(f"二维码目录创建失败: {e}") from e

    payload = render_png_bytes(content)
    ts = int(time.time() * 1000)
    token = uuid.uuid4().hex[:10]
    path = target_dir / f"qfarm_qr_{ts}_{token}.png"
    try:
        path.write_bytes(payload)
    except Exception as e:
        raise QRCodeRenderError(f"二维码写入失败: {e}") from e
    return str(path)


def cleanup_qr_cache(cache_dir: Path, ttl_sec: int) -> int:
    target_dir = Path(cache_dir)
    if not target_dir.exists():
        return 0

    now = time.time()
    safe_ttl = max(60, int(ttl_sec))
    removed = 0
    for path in target_dir.glob("*.png"):
        try:
            if now - path.stat().st_mtime >= safe_ttl:
                path.unlink(missing_ok=True)
                removed += 1
        except Exception:
            continue
    return removed
