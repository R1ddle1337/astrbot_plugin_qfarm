from __future__ import annotations

from pathlib import Path


def test_main_py_is_utf8_without_bom_and_alias_intact():
    root = Path(__file__).resolve().parents[1]
    target = root / "main.py"
    raw = target.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")

    text = raw.decode("utf-8")
    assert '@filter.command("qfarm"' in text
    assert "农场" in text
    assert "qfram" in text
