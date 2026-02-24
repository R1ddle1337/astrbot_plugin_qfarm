from __future__ import annotations

import json
from pathlib import Path

from astrbot_plugin_qfarm.services.state_store import QFarmStateStore


def test_single_account_binding_policy(tmp_path: Path):
    store = QFarmStateStore(tmp_path)
    assert (tmp_path / "bindings_v2.json").exists()
    store.bind_account("u1001", "acc-1", "A")
    assert store.get_bound_account("u1001") == "acc-1"

    # 同一用户再次绑定应直接覆盖，不产生第二个绑定
    store.bind_account("u1001", "acc-2", "B")
    info = store.get_bound_account_info("u1001")
    assert info is not None
    assert info["account_id"] == "acc-2"
    assert info["account_name"] == "B"

    removed = store.unbind_account("u1001")
    assert removed == "acc-2"
    assert store.get_bound_account("u1001") is None


def test_whitelist_merge_static_and_local(tmp_path: Path):
    store = QFarmStateStore(
        tmp_path,
        static_allowed_users=["100", "200"],
        static_allowed_groups=["3000"],
    )
    assert store.is_user_allowed("100")
    assert not store.is_user_allowed("999")

    store.add_whitelist_user("999")
    store.add_whitelist_group("4000")

    assert store.is_user_allowed("999")
    assert store.is_group_allowed("3000")
    assert store.is_group_allowed("4000")


def test_render_theme_persist(tmp_path: Path):
    store = QFarmStateStore(tmp_path)
    assert store.get_render_theme() == "light"
    store.set_render_theme("dark")
    assert store.get_render_theme() == "dark"

    reloaded = QFarmStateStore(tmp_path)
    assert reloaded.get_render_theme() == "dark"


def test_state_store_save_is_atomic_without_tmp_residue(tmp_path: Path):
    store = QFarmStateStore(tmp_path)
    store.bind_account("u1", "acc-1", "A")
    store.add_whitelist_user("u2")

    assert not list(tmp_path.rglob("*.tmp"))

    bindings = json.loads((tmp_path / "bindings_v2.json").read_text(encoding="utf-8"))
    assert bindings["owners"]["u1"]["account_id"] == "acc-1"
