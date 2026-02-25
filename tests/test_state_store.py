from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
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


def test_load_corrupt_json_creates_backup_then_resets_default(tmp_path: Path, monkeypatch):
    broken = '{"owners": '
    bindings_path = tmp_path / "bindings_v2.json"
    bindings_path.write_text(broken, encoding="utf-8")

    monkeypatch.setattr(
        "astrbot_plugin_qfarm.services.state_store.time.time",
        lambda: 1700000000,
    )

    QFarmStateStore(tmp_path)

    backup = tmp_path / "bindings_v2.corrupt-1700000000.json"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == broken

    repaired = json.loads(bindings_path.read_text(encoding="utf-8"))
    assert repaired == {"owners": {}, "accountOwners": {}}


def test_concurrent_binding_writes_remain_parseable_and_not_lost(tmp_path: Path):
    store = QFarmStateStore(tmp_path)
    total = 24

    def worker(index: int) -> None:
        store.bind_account(f"user-{index}", f"acc-{index}", f"name-{index}")

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(worker, i) for i in range(total)]
        for future in futures:
            future.result(timeout=10)

    content = (tmp_path / "bindings_v2.json").read_text(encoding="utf-8")
    payload = json.loads(content)

    assert isinstance(payload, dict)
    assert len(payload["owners"]) == total
    assert len(payload["accountOwners"]) == total
    for i in range(total):
        uid = f"user-{i}"
        aid = f"acc-{i}"
        assert payload["owners"][uid]["account_id"] == aid
        assert payload["accountOwners"][aid] == uid
