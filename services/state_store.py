from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

ALLOWED_RENDER_THEMES = {"dark", "light"}


def _normalize_id(value: Any) -> str:
    return str(value or "").strip()


def _normalize_id_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        item = _normalize_id(value)
        if item and item not in result:
            result.append(item)
    return result


class QFarmStateStore:
    """插件本地状态持久化：绑定关系、白名单、渲染主题。"""

    def __init__(
        self,
        data_dir: Path,
        static_allowed_users: list[str] | None = None,
        static_allowed_groups: list[str] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.owner_bindings_path = self.data_dir / "bindings_v2.json"
        self.whitelist_path = self.data_dir / "whitelist.json"
        self.runtime_secret_path = self.data_dir / "state_v2.json"

        self._static_allowed_users = _normalize_id_list(static_allowed_users or [])
        self._static_allowed_groups = _normalize_id_list(static_allowed_groups or [])

        self._owner_bindings = self._load_json(
            self.owner_bindings_path,
            {"owners": {}},
        )
        if "owners" not in self._owner_bindings or not isinstance(self._owner_bindings["owners"], dict):
            self._owner_bindings = {"owners": {}}
            self._save_json(self.owner_bindings_path, self._owner_bindings)

        self._whitelist = self._load_json(
            self.whitelist_path,
            {"users": [], "groups": []},
        )
        self._whitelist["users"] = _normalize_id_list(self._whitelist.get("users", []))
        self._whitelist["groups"] = _normalize_id_list(self._whitelist.get("groups", []))
        self._save_json(self.whitelist_path, self._whitelist)

        self._runtime_secret = self._load_json(self.runtime_secret_path, {"render_theme": "light"})

    def refresh_static_whitelist(self, users: list[str] | None, groups: list[str] | None) -> None:
        self._static_allowed_users = _normalize_id_list(users or [])
        self._static_allowed_groups = _normalize_id_list(groups or [])

    def get_render_theme(self, default: str = "light") -> str:
        fallback = str(default or "light").strip().lower()
        if fallback not in ALLOWED_RENDER_THEMES:
            fallback = "light"
        current = str(self._runtime_secret.get("render_theme") or "").strip().lower()
        if current in ALLOWED_RENDER_THEMES:
            return current
        return fallback

    def set_render_theme(self, theme: str) -> str:
        normalized = str(theme or "").strip().lower()
        if normalized not in ALLOWED_RENDER_THEMES:
            raise ValueError("theme 仅支持 dark|light")
        self._runtime_secret["render_theme"] = normalized
        self._save_json(self.runtime_secret_path, self._runtime_secret)
        return normalized

    def get_bound_account(self, user_id: str | int) -> str | None:
        uid = _normalize_id(user_id)
        if not uid:
            return None
        info = self._owner_bindings["owners"].get(uid)
        if not isinstance(info, dict):
            return None
        account_id = _normalize_id(info.get("account_id"))
        return account_id or None

    def get_bound_account_info(self, user_id: str | int) -> dict[str, Any] | None:
        uid = _normalize_id(user_id)
        if not uid:
            return None
        info = self._owner_bindings["owners"].get(uid)
        if not isinstance(info, dict):
            return None
        account_id = _normalize_id(info.get("account_id"))
        if not account_id:
            return None
        return {
            "user_id": uid,
            "account_id": account_id,
            "account_name": str(info.get("account_name") or ""),
            "updated_at": int(info.get("updated_at") or 0),
        }

    def bind_account(self, user_id: str | int, account_id: str | int, account_name: str = "") -> None:
        uid = _normalize_id(user_id)
        aid = _normalize_id(account_id)
        if not uid or not aid:
            raise ValueError("user_id 和 account_id 不能为空")
        self._owner_bindings["owners"][uid] = {
            "account_id": aid,
            "account_name": str(account_name or ""),
            "updated_at": int(time.time()),
        }
        self._save_json(self.owner_bindings_path, self._owner_bindings)

    def unbind_account(self, user_id: str | int) -> str | None:
        uid = _normalize_id(user_id)
        if not uid:
            return None
        info = self._owner_bindings["owners"].pop(uid, None)
        self._save_json(self.owner_bindings_path, self._owner_bindings)
        if isinstance(info, dict):
            account_id = _normalize_id(info.get("account_id"))
            return account_id or None
        return None

    def set_whitelist(self, users: list[str], groups: list[str]) -> None:
        self._whitelist = {
            "users": _normalize_id_list(users),
            "groups": _normalize_id_list(groups),
        }
        self._save_json(self.whitelist_path, self._whitelist)

    def list_whitelist_users(self) -> list[str]:
        merged = []
        for value in self._static_allowed_users + self._whitelist.get("users", []):
            if value and value not in merged:
                merged.append(value)
        return merged

    def list_whitelist_groups(self) -> list[str]:
        merged = []
        for value in self._static_allowed_groups + self._whitelist.get("groups", []):
            if value and value not in merged:
                merged.append(value)
        return merged

    def list_local_whitelist_users(self) -> list[str]:
        return list(self._whitelist.get("users", []))

    def list_local_whitelist_groups(self) -> list[str]:
        return list(self._whitelist.get("groups", []))

    def add_whitelist_user(self, user_id: str | int) -> bool:
        uid = _normalize_id(user_id)
        if not uid:
            return False
        users = self._whitelist.get("users", [])
        if uid in users:
            return False
        users.append(uid)
        self._whitelist["users"] = _normalize_id_list(users)
        self._save_json(self.whitelist_path, self._whitelist)
        return True

    def remove_whitelist_user(self, user_id: str | int) -> bool:
        uid = _normalize_id(user_id)
        users = self._whitelist.get("users", [])
        if uid not in users:
            return False
        users = [value for value in users if value != uid]
        self._whitelist["users"] = users
        self._save_json(self.whitelist_path, self._whitelist)
        return True

    def add_whitelist_group(self, group_id: str | int) -> bool:
        gid = _normalize_id(group_id)
        if not gid:
            return False
        groups = self._whitelist.get("groups", [])
        if gid in groups:
            return False
        groups.append(gid)
        self._whitelist["groups"] = _normalize_id_list(groups)
        self._save_json(self.whitelist_path, self._whitelist)
        return True

    def remove_whitelist_group(self, group_id: str | int) -> bool:
        gid = _normalize_id(group_id)
        groups = self._whitelist.get("groups", [])
        if gid not in groups:
            return False
        groups = [value for value in groups if value != gid]
        self._whitelist["groups"] = groups
        self._save_json(self.whitelist_path, self._whitelist)
        return True

    def is_user_allowed(self, user_id: str | int) -> bool:
        uid = _normalize_id(user_id)
        if not uid:
            return False
        return uid in self.list_whitelist_users()

    def is_group_allowed(self, group_id: str | int) -> bool:
        gid = _normalize_id(group_id)
        if not gid:
            return False
        return gid in self.list_whitelist_groups()

    def _load_json(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            self._save_json(path, default)
            return json.loads(json.dumps(default))
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        self._save_json(path, default)
        return json.loads(json.dumps(default))

    def _save_json(self, path: Path, data: dict[str, Any]) -> None:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
