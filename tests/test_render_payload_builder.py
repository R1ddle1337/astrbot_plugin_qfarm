from __future__ import annotations

from astrbot_plugin_qfarm.services.render_payload_builder import (
    build_qfarm_payload_pages,
    should_render_qfarm_image,
)


def test_build_payload_extracts_title_and_stats():
    text = "\n".join(
        [
            "【农场状态】",
            "连接在线，账号运行中",
            "金币: 1000",
            "经验: 200",
            "- 田地A 已成熟",
            "- 田地B 需浇水",
        ]
    )
    pages = build_qfarm_payload_pages(text, theme="dark")
    assert len(pages) == 1
    page = pages[0]
    assert page["title"] == "农场状态"
    assert page["theme"] == "dark"
    assert page["summary"] == "连接在线，账号运行中"
    assert {"label": "金币", "value": "1000"} in page["stats"]
    assert {"label": "经验", "value": "200"} in page["stats"]
    assert page["sections"][0]["rows"][0]["value"].startswith("- 田地A")


def test_build_payload_logs_use_small_page_size():
    lines = [f"- log line {i}" for i in range(35)]
    text = "【日志】\n" + "\n".join(lines)
    pages = build_qfarm_payload_pages(text, theme="light")
    assert len(pages) == 3
    assert pages[0]["page"] == {"index": 1, "total": 3}
    assert pages[1]["page"] == {"index": 2, "total": 3}
    assert pages[2]["page"] == {"index": 3, "total": 3}


def test_should_render_qfarm_image_text_only_cases():
    assert should_render_qfarm_image("用法: qfarm 状态") is False
    assert should_render_qfarm_image("操作失败: 账号未运行\n建议: qfarm 账号 启动") is False
    assert should_render_qfarm_image("【农场状态】\n金币: 1000") is True
