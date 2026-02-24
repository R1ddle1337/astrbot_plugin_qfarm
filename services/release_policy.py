from __future__ import annotations

import re
from pathlib import Path


REQUIRED_README_FIELDS = (
    "Reason:",
    "Change:",
    "Impact:",
    "Risk:",
    "Verification:",
)

_METADATA_VERSION_RE = re.compile(r"^version:\s*v(\d+\.\d+\.\d+)\s*$", re.MULTILINE)
_MAIN_REGISTER_RE = re.compile(
    r'@register\(\s*"(?:[^"\\]|\\.)*"\s*,\s*"(?:[^"\\]|\\.)*"\s*,\s*"(?:[^"\\]|\\.)*"\s*,\s*"(\d+\.\d+\.\d+)"\s*,',
    re.DOTALL,
)
_README_CURRENT_RE = re.compile(r"^- Current release:\s*v(\d+\.\d+\.\d+)\s*$", re.MULTILINE)
_README_RELEASE_LINE_RE = re.compile(r"^- \d{4}-\d{2}-\d{2} v(\d+\.\d+\.\d+)\s*$")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_metadata_version(text: str) -> str | None:
    match = _METADATA_VERSION_RE.search(text)
    return match.group(1) if match else None


def _extract_main_version(text: str) -> str | None:
    match = _MAIN_REGISTER_RE.search(text)
    return match.group(1) if match else None


def _extract_readme_current_version(text: str) -> str | None:
    match = _README_CURRENT_RE.search(text)
    return match.group(1) if match else None


def _extract_release_block_lines(readme_text: str, version: str) -> list[str]:
    lines = readme_text.splitlines()
    start = -1
    for idx, raw in enumerate(lines):
        m = _README_RELEASE_LINE_RE.match(raw.strip())
        if m and m.group(1) == version:
            start = idx
            break
    if start < 0:
        return []

    block: list[str] = []
    for idx in range(start, len(lines)):
        text = lines[idx].strip()
        if idx > start and _README_RELEASE_LINE_RE.match(text):
            break
        if not text:
            break
        block.append(text)
    return block


def validate_release_policy(project_root: Path, *, require_api_field: bool = False) -> list[str]:
    root = Path(project_root)
    metadata_path = root / "metadata.yaml"
    main_path = root / "main.py"
    readme_path = root / "README.md"

    errors: list[str] = []

    metadata_version = _extract_metadata_version(_read_text(metadata_path))
    main_version = _extract_main_version(_read_text(main_path))
    readme_current = _extract_readme_current_version(_read_text(readme_path))

    if not metadata_version:
        errors.append("metadata.yaml 缺少 `version: vX.Y.Z`。")
    if not main_version:
        errors.append("main.py 缺少 `@register(..., \"X.Y.Z\", ...)` 版本声明。")
    if not readme_current:
        errors.append("README.md 缺少 `Current release: vX.Y.Z`。")

    versions = [v for v in (metadata_version, main_version, readme_current) if v]
    if versions and len(set(versions)) != 1:
        errors.append(
            "版本号不一致: "
            f"metadata={metadata_version or '-'} "
            f"main={main_version or '-'} "
            f"readme={readme_current or '-'}"
        )

    if not readme_current:
        return errors

    readme_text = _read_text(readme_path)
    block = _extract_release_block_lines(readme_text, readme_current)
    if not block:
        errors.append(f"README.md 缺少 `{readme_current}` 的发布条目（`- YYYY-MM-DD v{readme_current}`）。")
        return errors

    for field in REQUIRED_README_FIELDS:
        has_field = any(line.startswith(f"- {field}") for line in block)
        if not has_field:
            errors.append(f"README.md `{readme_current}` 发布条目缺少 `- {field}`。")

    if require_api_field and not any(line.startswith("- API:") for line in block):
        errors.append(f"README.md `{readme_current}` 发布条目缺少 `- API:`。")

    return errors
