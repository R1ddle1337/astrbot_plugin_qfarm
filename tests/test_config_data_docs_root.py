from __future__ import annotations

import json
from pathlib import Path

from astrbot_plugin_qfarm.services.domain.config_data import GameConfigData


def _prepare_game_config(root: Path) -> None:
    game_cfg = root / "gameConfig"
    game_cfg.mkdir(parents=True, exist_ok=True)
    (game_cfg / "RoleLevel.json").write_text(json.dumps([]), encoding="utf-8")
    (game_cfg / "Plant.json").write_text(json.dumps([]), encoding="utf-8")
    (game_cfg / "ItemInfo.json").write_text(json.dumps([]), encoding="utf-8")


def test_config_data_prefers_qqfarm_docs_dir(tmp_path: Path):
    docs = tmp_path / "qqfarm文档"
    _prepare_game_config(docs)

    cfg = GameConfigData(tmp_path)

    assert cfg.docs_root == docs
    assert cfg.config_dir == docs / "gameConfig"


def test_config_data_fallbacks_to_any_qqfarm_like_dir_with_game_config(tmp_path: Path):
    weird = tmp_path / "qqfarm_docs_backup"
    _prepare_game_config(weird)

    cfg = GameConfigData(tmp_path)

    assert cfg.docs_root == weird
    assert cfg.config_dir == weird / "gameConfig"
