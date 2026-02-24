from __future__ import annotations

from pathlib import Path

from astrbot_plugin_qfarm.services.release_policy import validate_release_policy


def test_release_policy_passes_for_current_repo():
    root = Path(__file__).resolve().parents[1]
    errors = validate_release_policy(root)
    assert errors == []


def test_release_policy_reports_missing_fields(tmp_path: Path):
    (tmp_path / "metadata.yaml").write_text("version: v1.2.3\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        '@register("x", "y", "z", "1.2.3", "https://example.com")\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "\n".join(
            [
                "## Version",
                "- Current release: v1.2.3",
                "- 2026-02-24 v1.2.3",
                "- Reason: why",
                "- Change: what",
                "- Impact: who",
                "- Verification: tests",
            ]
        ),
        encoding="utf-8",
    )

    errors = validate_release_policy(tmp_path)
    assert any("缺少 `- Risk:`" in row for row in errors)


def test_release_policy_reports_version_mismatch(tmp_path: Path):
    (tmp_path / "metadata.yaml").write_text("version: v1.2.4\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        '@register("x", "y", "z", "1.2.3", "https://example.com")\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "\n".join(
            [
                "## Version",
                "- Current release: v1.2.3",
                "- 2026-02-24 v1.2.3",
                "- Reason: why",
                "- Change: what",
                "- Impact: who",
                "- Risk: low",
                "- Verification: tests",
            ]
        ),
        encoding="utf-8",
    )

    errors = validate_release_policy(tmp_path)
    assert any("版本号不一致" in row for row in errors)
