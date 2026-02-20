from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from ..domain.analytics_service import AnalyticsService
from ..domain.config_data import GameConfigData
from ..protocol import GatewaySessionConfig
from ..qr_login import QFarmQRLogin
from .account_runtime import AccountRuntime


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


DEFAULT_ACCOUNT_CONFIG = {
    "automation": {
        "farm": True,
        "farm_push": True,
        "land_upgrade": True,
        "friend": True,
        "friend_steal": True,
        "friend_help": True,
        "friend_bad": False,
        "task": True,
        "sell": True,
        "fertilizer": "both",
    },
    "strategy": "preferred",
    "preferredSeedId": 0,
    "intervals": {
        "farm": 2,
        "friend": 10,
        "farmMin": 2,
        "farmMax": 2,
        "friendMin": 10,
        "friendMax": 10,
    },
    "friendQuietHours": {
        "enabled": False,
        "start": "23:00",
        "end": "07:00",
    },
}


class QFarmRuntimeManager:
    def __init__(
        self,
        *,
        plugin_root: Path,
        data_dir: Path,
        gateway_ws_url: str,
        client_version: str,
        platform: str = "qq",
        heartbeat_interval_sec: int = 25,
        rpc_timeout_sec: int = 10,
        logger: Any | None = None,
    ) -> None:
        self.plugin_root = Path(plugin_root)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self.session_config = GatewaySessionConfig(
            gateway_ws_url=str(gateway_ws_url or "wss://gate-obt.nqf.qq.com/prod/ws"),
            client_version=str(client_version or "1.6.0.5_20251224"),
            platform=str(platform or "qq"),
            rpc_timeout_sec=max(1, int(rpc_timeout_sec)),
        )
        self.heartbeat_interval_sec = max(10, int(heartbeat_interval_sec))
        self.rpc_timeout_sec = max(1, int(rpc_timeout_sec))
        self.config_data = GameConfigData(self.plugin_root)
        self.analytics = AnalyticsService(self.config_data)
        self.qr_login = QFarmQRLogin()

        self.accounts_path = self.data_dir / "accounts_v2.json"
        self.settings_path = self.data_dir / "settings_v2.json"
        self.runtime_path = self.data_dir / "runtime_v2.json"
        self.bindings_path = self.data_dir / "bindings_v2.json"

        self._accounts = self._load_json(self.accounts_path, {"accounts": [], "nextId": 1})
        self._settings = self._load_json(
            self.settings_path,
            {"accountConfigs": {}, "defaultAccountConfig": DEFAULT_ACCOUNT_CONFIG, "ui": {"theme": "dark"}, "__revision": int(time.time())},
        )
        self._runtime_data = self._load_json(self.runtime_path, {"status": {}})
        self._load_json(self.bindings_path, {"owners": {}})

        self._service_running = False
        self._runtimes: dict[str, AccountRuntime] = {}
        self._global_logs: list[dict[str, Any]] = []
        self._account_logs: list[dict[str, Any]] = []
        self._state_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._service_running:
            return
        self._service_running = True
        for account in list(self._accounts.get("accounts", [])):
            account_id = str(account.get("id") or "").strip()
            if not account_id:
                continue
            try:
                await self.start_account(account_id)
            except Exception as e:
                self._log("系统", f"账号启动失败 {account_id}: {e}", is_warn=True, module="system", event="start_account")

    async def stop(self) -> None:
        self._service_running = False
        runtime_ids = list(self._runtimes.keys())
        for account_id in runtime_ids:
            try:
                await self.stop_account(account_id)
            except Exception:
                continue

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    def service_status(self) -> dict[str, Any]:
        return {
            "managed_mode": True,
            "running": self._service_running,
            "pid": None,
            "runtimeCount": len(self._runtimes),
            "project_root": str(self.plugin_root),
            "mode": "python",
        }

    async def ping(self) -> bool:
        return True

    async def get_accounts(self) -> dict[str, Any]:
        data = self._normalize_accounts_data(self._accounts)
        for row in data["accounts"]:
            row["running"] = str(row.get("id")) in self._runtimes
        return data

    async def upsert_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._state_lock:
            data = self._normalize_accounts_data(self._accounts)
            account_id = str(payload.get("id") or "").strip()
            if account_id:
                idx = next((i for i, v in enumerate(data["accounts"]) if str(v.get("id")) == account_id), -1)
                if idx < 0:
                    raise RuntimeError(f"账号不存在: {account_id}")
                current = data["accounts"][idx]
                merged = {**current, **(payload or {}), "id": account_id, "updatedAt": int(time.time() * 1000)}
                data["accounts"][idx] = merged
                action = "update"
                target = merged
            else:
                new_id = str(data["nextId"])
                data["nextId"] += 1
                target = {
                    "id": new_id,
                    "name": str(payload.get("name") or f"账号{new_id}"),
                    "platform": str(payload.get("platform") or "qq"),
                    "code": str(payload.get("code") or ""),
                    "uin": str(payload.get("uin") or ""),
                    "qq": str(payload.get("qq") or payload.get("uin") or ""),
                    "avatar": str(payload.get("avatar") or payload.get("avatarUrl") or ""),
                    "createdAt": int(time.time() * 1000),
                    "updatedAt": int(time.time() * 1000),
                }
                data["accounts"].append(target)
                action = "add"
            self._accounts = data
            self._save_json_atomic(self.accounts_path, self._accounts)
            self._add_account_log(action, f"{'更新' if action == 'update' else '添加'}账号: {target.get('name')}", str(target.get("id")), str(target.get("name")))

        if action == "update":
            await self.stop_account(str(target.get("id")))
        await self.start_account(str(target.get("id")))
        return await self.get_accounts()

    async def delete_account(self, account_id: str | int) -> dict[str, Any]:
        account_id_text = str(account_id or "").strip()
        if not account_id_text:
            raise RuntimeError("account_id 不能为空")
        await self.stop_account(account_id_text)
        async with self._state_lock:
            data = self._normalize_accounts_data(self._accounts)
            before = len(data["accounts"])
            target_name = ""
            kept = []
            for row in data["accounts"]:
                if str(row.get("id")) == account_id_text:
                    target_name = str(row.get("name") or "")
                    continue
                kept.append(row)
            data["accounts"] = kept
            if before == len(kept):
                raise RuntimeError(f"账号不存在: {account_id_text}")
            if not kept:
                data["nextId"] = 1
            self._accounts = data
            self._settings.get("accountConfigs", {}).pop(account_id_text, None)
            self._save_json_atomic(self.accounts_path, self._accounts)
            self._save_json_atomic(self.settings_path, self._settings)
            self._add_account_log("delete", f"删除账号: {target_name or account_id_text}", account_id_text, target_name)
        return await self.get_accounts()

    async def start_account(self, account_id: str | int) -> None:
        account_id_text = str(account_id or "").strip()
        if not account_id_text:
            raise RuntimeError("account_id 不能为空")
        if account_id_text in self._runtimes:
            return
        account = self._find_account(account_id_text)
        if not account:
            raise RuntimeError(f"账号不存在: {account_id_text}")
        runtime = AccountRuntime(
            account=account,
            settings=self._get_account_settings(account_id_text),
            session_config=self.session_config,
            config_data=self.config_data,
            heartbeat_interval_sec=self.heartbeat_interval_sec,
            rpc_timeout_sec=self.rpc_timeout_sec,
            logger=self.logger,
            log_callback=self._on_runtime_log,
            kicked_callback=self._on_runtime_kicked,
        )
        self._runtimes[account_id_text] = runtime
        try:
            await runtime.start()
        except Exception:
            self._runtimes.pop(account_id_text, None)
            raise

    async def stop_account(self, account_id: str | int) -> None:
        account_id_text = str(account_id or "").strip()
        runtime = self._runtimes.get(account_id_text)
        if not runtime:
            return
        try:
            await runtime.stop()
        finally:
            self._runtimes.pop(account_id_text, None)

    async def get_status(self, account_id: str | int) -> dict[str, Any]:
        account_id_text = str(account_id or "").strip()
        runtime = self._runtimes.get(account_id_text)
        if runtime:
            return await runtime.get_status()
        account = self._find_account(account_id_text)
        if not account:
            raise RuntimeError("账号不存在")
        return {
            "connection": {"connected": False},
            "status": {"name": "", "level": 0, "gold": 0, "coupon": 0, "exp": 0, "platform": str(account.get("platform") or "qq")},
            "uptime": 0,
            "operations": {},
            "sessionExpGained": 0,
            "sessionGoldGained": 0,
            "sessionCouponGained": 0,
            "lastExpGain": 0,
            "lastGoldGain": 0,
            "limits": {},
            "automation": self._get_account_settings(account_id_text).get("automation", {}),
            "preferredSeed": self._get_account_settings(account_id_text).get("preferredSeedId", 0),
            "expProgress": {"current": 0, "needed": 0, "level": 0},
            "configRevision": _to_int(self._settings.get("__revision"), 0),
            "nextChecks": {"farmRemainSec": 0, "friendRemainSec": 0},
        }

    async def get_lands(self, account_id: str | int) -> dict[str, Any]:
        return await self._require_runtime(account_id).get_lands()

    async def get_friends(self, account_id: str | int) -> list[dict[str, Any]]:
        return await self._require_runtime(account_id).get_friends()

    async def get_friend_lands(self, account_id: str | int, friend_gid: str | int) -> dict[str, Any]:
        return await self._require_runtime(account_id).get_friend_lands(_to_int(friend_gid, 0))

    async def do_friend_op(self, account_id: str | int, friend_gid: str | int, op_type: str) -> dict[str, Any]:
        return await self._require_runtime(account_id).do_friend_op(_to_int(friend_gid, 0), op_type)

    async def get_seeds(self, account_id: str | int) -> list[dict[str, Any]]:
        return await self._require_runtime(account_id).get_seeds()

    async def get_bag(self, account_id: str | int) -> dict[str, Any]:
        return await self._require_runtime(account_id).get_bag()

    async def do_farm_op(self, account_id: str | int, op_type: str) -> dict[str, Any]:
        return await self._require_runtime(account_id).do_farm_operation(op_type)

    async def get_analytics(self, account_id: str | int, sort_by: str) -> list[dict[str, Any]]:
        runtime = self._runtimes.get(str(account_id))
        if runtime:
            return await runtime.get_analytics(sort_by)
        return self.analytics.get_plant_rankings(sort_by)

    async def set_automation(self, account_id: str | int, key: str, value: Any) -> dict[str, Any]:
        payload = {"automation": {str(key): value}}
        return await self.save_settings(account_id, payload)

    async def save_settings(self, account_id: str | int, payload: dict[str, Any]) -> dict[str, Any]:
        account_id_text = str(account_id or "").strip()
        if not account_id_text:
            raise RuntimeError("account_id 不能为空")
        async with self._state_lock:
            current = self._get_account_settings(account_id_text)
            next_cfg = self._merge_settings(current, payload or {})
            cfg_map = self._settings.setdefault("accountConfigs", {})
            cfg_map[account_id_text] = next_cfg
            self._settings["__revision"] = _to_int(self._settings.get("__revision"), int(time.time())) + 1
            revision = _to_int(self._settings.get("__revision"), 0)
            self._save_json_atomic(self.settings_path, self._settings)
        runtime = self._runtimes.get(account_id_text)
        if runtime:
            runtime.apply_settings(self._get_account_settings(account_id_text), revision)
        return await self.get_settings(account_id_text)

    async def get_settings(self, account_id: str | int) -> dict[str, Any]:
        account_id_text = str(account_id or "").strip()
        cfg = self._get_account_settings(account_id_text)
        return {
            "intervals": cfg.get("intervals", {}),
            "strategy": cfg.get("strategy", "preferred"),
            "preferredSeed": cfg.get("preferredSeedId", 0),
            "friendQuietHours": cfg.get("friendQuietHours", {}),
            "automation": cfg.get("automation", {}),
            "ui": self._settings.get("ui", {"theme": "dark"}),
        }

    async def set_theme(self, theme: str) -> dict[str, Any]:
        value = str(theme or "dark").strip().lower()
        if value not in {"dark", "light"}:
            value = "dark"
        async with self._state_lock:
            self._settings.setdefault("ui", {})["theme"] = value
            self._settings["__revision"] = _to_int(self._settings.get("__revision"), int(time.time())) + 1
            self._save_json_atomic(self.settings_path, self._settings)
        return {"ui": {"theme": value}}

    async def get_logs(self, account_id: str | int, **filters: Any) -> list[dict[str, Any]]:
        account_id_text = str(account_id or "").strip()
        limit = max(1, min(300, _to_int(filters.get("limit"), 100)))
        keyword = str(filters.get("keyword") or "").strip().lower()
        module_name = str(filters.get("module") or "").strip()
        event_name = str(filters.get("event") or "").strip()
        is_warn_raw = str(filters.get("isWarn") or "").strip()
        has_warn_filter = is_warn_raw in {"0", "1", "true", "false"}
        warn_expect = is_warn_raw in {"1", "true"}
        rows = list(self._global_logs)
        if account_id_text:
            rows = [row for row in rows if str(row.get("accountId") or "") == account_id_text]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("_searchText") or "").lower()]
        if module_name:
            rows = [row for row in rows if str((row.get("meta") or {}).get("module") or "") == module_name]
        if event_name:
            rows = [row for row in rows if str((row.get("meta") or {}).get("event") or "") == event_name]
        if has_warn_filter:
            rows = [row for row in rows if bool(row.get("isWarn")) is warn_expect]
        return list(reversed(rows[-limit:]))

    async def get_account_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        safe = max(1, min(300, _to_int(limit, 100)))
        return list(reversed(self._account_logs[-safe:]))

    async def debug_sell(self, account_id: str | int) -> dict[str, Any]:
        return await self._require_runtime(account_id).debug_sell()

    async def qr_create(self) -> dict[str, Any]:
        return await self.qr_login.create()

    async def qr_check(self, code: str) -> dict[str, Any]:
        return await self.qr_login.check(str(code or ""))

    def _require_runtime(self, account_id: str | int) -> AccountRuntime:
        account_id_text = str(account_id or "").strip()
        runtime = self._runtimes.get(account_id_text)
        if not runtime:
            raise RuntimeError("账号未运行")
        return runtime

    def _find_account(self, account_id: str) -> dict[str, Any] | None:
        for row in self._accounts.get("accounts", []):
            if str(row.get("id")) == str(account_id):
                return dict(row)
        return None

    def _get_account_settings(self, account_id: str) -> dict[str, Any]:
        base = self._merge_settings(dict(DEFAULT_ACCOUNT_CONFIG), self._settings.get("defaultAccountConfig", {}))
        account_cfg = (self._settings.get("accountConfigs", {}) or {}).get(str(account_id), {})
        return self._merge_settings(base, account_cfg)

    def _merge_settings(self, base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        result = json.loads(json.dumps(base))
        src = patch if isinstance(patch, dict) else {}
        for key in ("strategy",):
            if key in src:
                result[key] = str(src.get(key) or "preferred")
        if "preferredSeedId" in src:
            result["preferredSeedId"] = max(0, _to_int(src.get("preferredSeedId"), 0))
        if "seedId" in src:
            result["preferredSeedId"] = max(0, _to_int(src.get("seedId"), 0))
        if isinstance(src.get("automation"), dict):
            result.setdefault("automation", {}).update(src.get("automation"))
        if isinstance(src.get("intervals"), dict):
            result.setdefault("intervals", {}).update(src.get("intervals"))
        if isinstance(src.get("friendQuietHours"), dict):
            result.setdefault("friendQuietHours", {}).update(src.get("friendQuietHours"))
        return result

    def _normalize_accounts_data(self, raw: dict[str, Any]) -> dict[str, Any]:
        data = dict(raw or {})
        accounts = data.get("accounts", [])
        if not isinstance(accounts, list):
            accounts = []
        normalized = []
        max_id = 0
        for row in accounts:
            if not isinstance(row, dict):
                continue
            account_id = str(row.get("id") or "").strip()
            if not account_id:
                continue
            max_id = max(max_id, _to_int(account_id, 0))
            normalized.append(dict(row))
        next_id = max(_to_int(data.get("nextId"), 1), max_id + 1 if normalized else 1)
        return {"accounts": normalized, "nextId": next_id}

    def _on_runtime_log(self, account_id: str, tag: str, message: str, is_warn: bool, meta: dict[str, Any]) -> None:
        entry = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "tag": tag,
            "msg": message,
            "isWarn": bool(is_warn),
            "accountId": str(account_id or ""),
            "meta": dict(meta or {}),
            "ts": int(time.time() * 1000),
        }
        entry["_searchText"] = f"{entry['msg']} {entry['tag']} {json.dumps(entry['meta'], ensure_ascii=False)}".lower()
        self._global_logs.append(entry)
        if len(self._global_logs) > 1000:
            self._global_logs.pop(0)

    async def _on_runtime_kicked(self, account_id: str, reason: str) -> None:
        self._add_account_log("kickout_delete", f"账号被踢下线，已删除: {reason}", account_id, "", reason=reason)
        try:
            await self.delete_account(account_id)
        except Exception:
            return

    def _add_account_log(self, action: str, msg: str, account_id: str = "", account_name: str = "", **extra: Any) -> None:
        row = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "action": str(action or ""),
            "msg": str(msg or ""),
            "accountId": str(account_id or ""),
            "accountName": str(account_name or ""),
        }
        row.update(extra)
        self._account_logs.append(row)
        if len(self._account_logs) > 300:
            self._account_logs.pop(0)

    def _log(self, tag: str, message: str, *, is_warn: bool = False, **meta: Any) -> None:
        if self.logger:
            try:
                text = f"[qfarm-runtime] [{tag}] {message}"
                if is_warn and hasattr(self.logger, "warning"):
                    self.logger.warning(text)
                elif hasattr(self.logger, "info"):
                    self.logger.info(text)
            except Exception:
                pass
        self._on_runtime_log("", tag, message, is_warn, meta)

    @staticmethod
    def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
            return json.loads(json.dumps(default))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.loads(json.dumps(default))

    @staticmethod
    def _save_json_atomic(path: Path, data: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
