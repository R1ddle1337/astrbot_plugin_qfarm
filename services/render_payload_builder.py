from __future__ import annotations

import datetime as dt
import re
from typing import Any

ALLOWED_THEMES = {"dark", "light"}

TEXT_ONLY_PREFIXES = (
    "用法:",
    "权限不足",
    "操作失败:",
    "命令执行异常:",
    "未知命令:",
    "请求过于频繁",
)
TEXT_ONLY_KEYWORDS = (
    "建议:",
    "最近启动错误",
    "排查",
    "未绑定账号",
    "账号未运行",
    "白名单",
)


def build_qfarm_payload_pages(
    text: str,
    *,
    theme: str = "light",
    icon: str = "🌾",
    footer: str = "astrbot_plugin_qfarm",
) -> list[dict[str, Any]]:
    lines = [_clip_line(_normalize_line(line)) for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        lines = ["暂无可展示内容。"]

    title, body_lines = _extract_title(lines)
    summary = ""
    if body_lines and not _looks_like_list_item(body_lines[0]):
        summary = body_lines[0]
        body_lines = body_lines[1:]

    stats: list[dict[str, str]] = []
    rows: list[dict[str, str]] = []
    for raw in body_lines:
        key, value = _split_key_value(raw)
        if key and value and len(key) <= 8 and len(value) <= 24 and len(stats) < 10:
            stats.append({"label": _clip_line(key, 16), "value": _clip_line(value, 48)})
            continue
        if key and value:
            rows.append({"label": _clip_line(key, 20), "value": _clip_line(value, 120)})
            continue
        rows.append({"value": _clip_line(raw, 120)})

    if not rows and summary:
        rows = [{"value": summary}]

    page_size = 16 if _is_log_message(title, lines) else 22
    row_chunks = _chunk_rows(rows, page_size) if rows else [[]]
    total_pages = len(row_chunks)
    now_text = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    normalized_theme = str(theme or "light").strip().lower()
    if normalized_theme not in ALLOWED_THEMES:
        normalized_theme = "light"

    payloads: list[dict[str, Any]] = []
    for index, chunk in enumerate(row_chunks, start=1):
        payloads.append(
            {
                "title": title,
                "subtitle": now_text,
                "icon": icon,
                "theme": normalized_theme,
                "summary": summary if index == 1 else "",
                "stats": stats if index == 1 else stats[:4],
                "sections": [{"title": "详情", "rows": chunk}] if chunk else [],
                "page": {"index": index, "total": total_pages},
                "footer": footer,
            }
        )
    return payloads


def should_render_qfarm_image(text: str) -> bool:
    content = str(text or "").strip()
    if not content:
        return False
    if any(content.startswith(prefix) for prefix in TEXT_ONLY_PREFIXES):
        return False
    if any(keyword in content for keyword in TEXT_ONLY_KEYWORDS):
        return False
    return True


def _extract_title(lines: list[str]) -> tuple[str, list[str]]:
    first = lines[0]
    m = re.match(r"^【(.+?)】$", first)
    if m:
        return _clip_line(m.group(1), 40), lines[1:]
    return "QFarm 结果", lines


def _is_log_message(title: str, lines: list[str]) -> bool:
    if "日志" in title:
        return True
    joined = "\n".join(lines[:6])
    return "日志" in joined or "log" in joined.lower()


def _chunk_rows(rows: list[dict[str, str]], size: int) -> list[list[dict[str, str]]]:
    if size <= 0:
        size = 1
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def _split_key_value(line: str) -> tuple[str, str]:
    clean = line.lstrip("- ").strip()
    if not clean:
        return "", ""
    for sep in ("：", ":"):
        if sep in clean:
            key, value = clean.split(sep, 1)
            return key.strip(), value.strip()
    return "", ""


def _looks_like_list_item(line: str) -> bool:
    text = line.strip()
    if text.startswith("- "):
        return True
    return bool(re.match(r"^\d+\.\s*", text))


def _normalize_line(value: str) -> str:
    return str(value or "").replace("\u0000", "").strip()


def _clip_line(value: str, limit: int = 120) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)] + "..."
