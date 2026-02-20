from __future__ import annotations

from astrbot_plugin_qfarm.services.command_router import parse_key_value_args, tokenize_command


def test_tokenize_command():
    assert tokenize_command("  qfarm   状态  ") == ["qfarm", "状态"]
    assert tokenize_command("") == []


def test_parse_key_value_args():
    limit, options = parse_key_value_args(
        ["100", "module=farm", "event=farm_cycle", "keyword=收获", "isWarn=1"]
    )
    assert limit == 100
    assert options["module"] == "farm"
    assert options["event"] == "farm_cycle"
    assert options["keyword"] == "收获"
    assert options["isWarn"] == "1"

