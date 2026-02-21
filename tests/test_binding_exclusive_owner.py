from __future__ import annotations

import json
from pathlib import Path

import pytest

from astrbot_plugin_qfarm.services.state_store import QFarmStateStore


def test_account_binding_is_exclusive(tmp_path: Path):
    store = QFarmStateStore(tmp_path)
    store.bind_account("u1", "acc-1", "A")
    with pytest.raises(ValueError):
        store.bind_account("u2", "acc-1", "B")


def test_legacy_bindings_are_normalized_with_account_owner_index(tmp_path: Path):
    legacy = {
        "owners": {
            "u1": {"account_id": "acc-1", "account_name": "A", "updated_at": 1},
            "u2": {"account_id": "acc-1", "account_name": "B", "updated_at": 2},
        }
    }
    (tmp_path / "bindings_v2.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    store = QFarmStateStore(tmp_path)
    info = json.loads((tmp_path / "bindings_v2.json").read_text(encoding="utf-8"))
    assert isinstance(info.get("accountOwners"), dict)
    assert info["accountOwners"].get("acc-1") in {"u1", "u2"}
    owner = info["accountOwners"]["acc-1"]
    assert store.get_bound_account(owner) == "acc-1"
