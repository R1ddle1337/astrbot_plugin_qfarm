from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from ..domain.analytics_service import AnalyticsService
from ..domain.config_data import GameConfigData
from ..protocol import GatewaySessionConfig
from ..qr_code_renderer import QRCodeRenderError, cleanup_qr_cache, save_qr_png
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
        "email": True,
        "mall": True,
        "monthcard": True,
        "vip": True,
        "share": True,
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
    "dailyRoutines": {},
    "push": {
        "enabled": False,
        "channel": "webhook",
        "endpoint": "",
        "token": "",
        "autoEvents": "core",
        "retryMax": 2,
        "allowPrivateEndpoint": False,
        "bodyTokenEnabled": False,
        "maxConcurrency": 8,
        "maxPerMinute": 60,
    },
}

CORE_PUSH_TASK_ERROR_EVENTS = {
    "email_rewards",
    "mall_free_gifts",
    "mall_organic_fertilizer",
    "month_card_gift",
    "vip_daily_gift",
    "daily_share",
}
CORE_PUSH_SYSTEM_EVENTS = {"account_start_failed", "start_failed", "kickout_delete"}


class PushDeliverError(RuntimeError):
    def __init__(self, message: str, *, http_status: int = 0, error_code: str = "") -> None:
        super().__init__(message)
        self.http_status = max(0, int(http_status))
        self.error_code = str(error_code or "")


class QFarmRuntimeManager:
    _PUSH_ERROR_SNIPPET_MAX = 256

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
        start_retry_max_attempts: int = 3,
        start_retry_base_delay_sec: float = 1.0,
        start_retry_max_delay_sec: float = 8.0,
        auto_start_concurrency: int = 5,
        persist_runtime_logs: bool = True,
        runtime_log_max_entries: int = 3000,
        runtime_log_flush_interval_sec: float = 2.0,
        runtime_log_flush_batch: int = 80,
        default_automation: dict[str, Any] | None = None,
        default_push: dict[str, Any] | None = None,
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
        self.start_retry_max_attempts = max(1, int(start_retry_max_attempts))
        self.start_retry_base_delay_sec = max(0.1, float(start_retry_base_delay_sec))
        self.start_retry_max_delay_sec = max(
            self.start_retry_base_delay_sec,
            float(start_retry_max_delay_sec),
        )
        self.auto_start_concurrency = max(1, int(auto_start_concurrency))
        self.persist_runtime_logs = bool(persist_runtime_logs)
        self.runtime_log_max_entries = max(1, int(runtime_log_max_entries))
        self.runtime_log_flush_interval_sec = max(0.2, float(runtime_log_flush_interval_sec))
        self.runtime_log_flush_batch = max(1, int(runtime_log_flush_batch))
        self._default_account_config = json.loads(json.dumps(DEFAULT_ACCOUNT_CONFIG))
        incoming_auto = default_automation if isinstance(default_automation, dict) else {}
        base_auto = self._default_account_config.setdefault("automation", {})
        for key, value in incoming_auto.items():
            if key not in base_auto:
                continue
            if key == "fertilizer":
                mode = str(value or "both").strip().lower()
                base_auto[key] = mode if mode in {"both", "normal", "organic", "none"} else "both"
                continue
            base_auto[key] = bool(value)
        incoming_push = default_push if isinstance(default_push, dict) else {}
        base_push = self._default_account_config.setdefault("push", {})
        for key, value in incoming_push.items():
            if key == "enabled":
                base_push["enabled"] = bool(value)
                continue
            if key == "channel":
                channel = str(value or "webhook").strip().lower()
                base_push["channel"] = channel if channel in {"webhook"} else "webhook"
                continue
            if key in {"endpoint", "url"}:
                base_push["endpoint"] = str(value or "").strip()
                continue
            if key == "token":
                base_push["token"] = str(value or "").strip()
                continue
            if key == "autoEvents":
                base_push["autoEvents"] = "core"
                continue
            if key == "retryMax":
                base_push["retryMax"] = max(0, min(5, _to_int(value, 2)))
                continue
            if key in {"allowPrivateEndpoint", "allow_private_endpoint"}:
                base_push["allowPrivateEndpoint"] = bool(value)
                continue
            if key in {"bodyTokenEnabled", "body_token_enabled"}:
                base_push["bodyTokenEnabled"] = bool(value)
                continue
            if key in {"maxConcurrency", "max_concurrency"}:
                base_push["maxConcurrency"] = max(1, min(32, _to_int(value, 8)))
                continue
            if key in {"maxPerMinute", "max_per_minute"}:
                base_push["maxPerMinute"] = max(1, min(600, _to_int(value, 60)))
        self.config_data = GameConfigData(self.plugin_root)
        self.analytics = AnalyticsService(self.config_data)
        self.qr_login = QFarmQRLogin()
        self.qr_cache_dir = self.data_dir / "qr_cache"
        self.qr_cache_dir.mkdir(parents=True, exist_ok=True)
        self.qr_cache_ttl_sec = 3600

        self.accounts_path = self.data_dir / "accounts_v2.json"
        self.settings_path = self.data_dir / "settings_v2.json"
        self.runtime_path = self.data_dir / "runtime_v2.json"
        self.bindings_path = self.data_dir / "bindings_v2.json"
        self.runtime_logs_path = self.data_dir / "runtime_logs_v2.json"

        self._accounts = self._load_json(self.accounts_path, {"accounts": [], "nextId": 1})
        self._settings = self._load_json(
            self.settings_path,
            {"accountConfigs": {}, "defaultAccountConfig": self._default_account_config, "ui": {"theme": "dark"}, "__revision": int(time.time())},
        )
        self._runtime_data = self._load_json(self.runtime_path, {"status": {}})
        if not isinstance(self._runtime_data.get("status"), dict):
            self._runtime_data = {"status": {}}
            self._save_json_atomic(self.runtime_path, self._runtime_data)
        self._load_json(self.bindings_path, {"owners": {}})

        self._service_running = False
        self._runtimes: dict[str, AccountRuntime] = {}
        self._global_logs: list[dict[str, Any]] = []
        self._account_logs: list[dict[str, Any]] = []
        self._runtime_logs_dirty = False
        self._runtime_logs_pending = 0
        self._runtime_logs_last_flush_at = time.monotonic()
        self._load_persisted_runtime_logs()
        self._state_lock = asyncio.Lock()
        self._runtime_status_lock = asyncio.Lock()
        self._start_locks: dict[str, asyncio.Lock] = {}
        self._push_semaphore = asyncio.Semaphore(max(1, _to_int(base_push.get("maxConcurrency"), 8)))
        self._push_rate_lock = asyncio.Lock()
        self._push_rate_windows: dict[str, deque[float]] = {}
        self._auto_push_pending = 0
        self._auto_push_pending_limit = max(20, max(1, _to_int(base_push.get("maxConcurrency"), 8)) * 20)

    async def start(self) -> None:
        if self._service_running:
            return
        self._service_running = True
        semaphore = asyncio.Semaphore(self.auto_start_concurrency)
        tasks: list[asyncio.Task] = []

        async def _auto_start_one(account_id: str) -> None:
            async with semaphore:
                try:
                    await self.start_account(account_id)
                except Exception as e:
                    self._log(
                        "系统",
                        f"账号启动失败 {account_id}: {e}",
                        is_warn=True,
                        module="system",
                        event="start_account",
                        accountId=account_id,
                    )

        for account in list(self._accounts.get("accounts", [])):
            account_id = str(account.get("id") or "").strip()
            if not account_id:
                continue
            tasks.append(asyncio.create_task(_auto_start_one(account_id)))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self) -> None:
        self._service_running = False
        runtime_ids = list(self._runtimes.keys())
        for account_id in runtime_ids:
            try:
                await self.stop_account(account_id)
            except Exception:
                continue
        self._persist_runtime_logs(force=True)

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    def service_status(self) -> dict[str, Any]:
        failed_accounts: list[dict[str, Any]] = []
        retrying_count = 0
        for account_id, row in (self._runtime_data.get("status") or {}).items():
            if not isinstance(row, dict):
                continue
            state = str(row.get("runtimeState") or "stopped")
            if state == "retrying":
                retrying_count += 1
            if state == "failed":
                failed_accounts.append(
                    {
                        "accountId": str(account_id),
                        "error": str(row.get("lastStartError") or ""),
                        "retryCount": _to_int(row.get("startRetryCount"), 0),
                    }
                )
        return {
            "managed_mode": True,
            "running": self._service_running,
            "pid": None,
            "runtimeCount": len(self._runtimes),
            "project_root": str(self.plugin_root),
            "mode": "python",
            "failedCount": len(failed_accounts),
            "failedAccounts": failed_accounts[:20],
            "retryingCount": retrying_count,
        }

    async def ping(self) -> bool:
        return True

    async def get_accounts(self) -> dict[str, Any]:
        data = self._normalize_accounts_data(self._accounts)
        for row in data["accounts"]:
            account_id = str(row.get("id") or "").strip()
            is_running = account_id in self._runtimes
            row["running"] = is_running
            row.update(self._runtime_status_view(account_id, is_running=is_running))
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
            self._add_account_log(
                action,
                f"{'更新' if action == 'update' else '添加'}账号: {target.get('name')}",
                str(target.get("id")),
                str(target.get("name")),
            )

        account_id_text = str(target.get("id") or "")
        if action == "update":
            await self.stop_account(account_id_text)
        start_error = ""
        try:
            await self.start_account(account_id_text)
        except Exception as e:
            start_error = str(e)
            self._log(
                "系统",
                f"账号已保存，但自动启动失败: {start_error}",
                is_warn=True,
                module="system",
                event="account_start_failed",
                result="error",
                accountId=account_id_text,
            )
            self._add_account_log(
                "start_failed",
                f"账号保存成功，但自动启动失败: {start_error}",
                account_id_text,
                str(target.get("name") or ""),
            )
        current_account = await self._get_account_view_by_id(account_id_text)
        return {
            "action": action,
            "account": current_account or dict(target),
            "autoStart": not bool(start_error),
            "startError": start_error,
        }

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
            await self._clear_runtime_status(account_id_text)
            self._save_json_atomic(self.accounts_path, self._accounts)
            self._save_json_atomic(self.settings_path, self._settings)
            self._add_account_log("delete", f"删除账号: {target_name or account_id_text}", account_id_text, target_name)
        return await self.get_accounts()

    async def start_account(self, account_id: str | int) -> None:
        account_id_text = str(account_id or "").strip()
        if not account_id_text:
            raise RuntimeError("account_id 不能为空")
        lock = self._start_locks.setdefault(account_id_text, asyncio.Lock())
        async with lock:
            if account_id_text in self._runtimes:
                await self._set_runtime_status(account_id_text, runtimeState="running")
                return

            account = self._find_account(account_id_text)
            if not account:
                raise RuntimeError(f"账号不存在: {account_id_text}")

            attempts = self.start_retry_max_attempts
            last_error = ""
            for attempt in range(1, attempts + 1):
                now_ms = int(time.time() * 1000)
                await self._set_runtime_status(
                    account_id_text,
                    runtimeState="starting" if attempt == 1 else "retrying",
                    lastStartAt=now_ms,
                    startRetryCount=max(0, attempt - 1),
                    lastStartError=last_error if attempt > 1 else "",
                )

                runtime = AccountRuntime(
                    account=account,
                    settings=self._get_account_settings(account_id_text),
                    session_config=self.session_config,
                    config_data=self.config_data,
                    heartbeat_interval_sec=self.heartbeat_interval_sec,
                    rpc_timeout_sec=self.rpc_timeout_sec,
                    share_file_path=self.data_dir / "share.txt",
                    logger=self.logger,
                    log_callback=self._on_runtime_log,
                    kicked_callback=self._on_runtime_kicked,
                    runtime_state_persist=lambda patch, aid=account_id_text: self._persist_runtime_state_patch(aid, patch),
                )
                self._runtimes[account_id_text] = runtime
                try:
                    await runtime.start()
                    await self._set_runtime_status(
                        account_id_text,
                        runtimeState="running",
                        lastStartSuccessAt=int(time.time() * 1000),
                        lastStartError="",
                        startRetryCount=max(0, attempt - 1),
                    )
                    return
                except Exception as e:
                    try:
                        await runtime.stop()
                    except Exception:
                        pass
                    self._runtimes.pop(account_id_text, None)
                    last_error = self._normalize_start_error(str(e))
                    can_retry = (
                        attempt < attempts
                        and self._is_retryable_start_error(last_error)
                    )
                    await self._set_runtime_status(
                        account_id_text,
                        runtimeState="retrying" if can_retry else "failed",
                        lastStartError=last_error,
                        startRetryCount=attempt,
                    )
                    if not can_retry:
                        self._log(
                            "system",
                            f"account start failed permanently: {last_error}",
                            is_warn=True,
                            module="system",
                            event="start_failed",
                            result="error",
                            accountId=account_id_text,
                            retry=attempt,
                        )
                        raise RuntimeError(
                            f"账号启动失败(重试{attempt}/{attempts}): {last_error}"
                        )
                    delay = min(
                        self.start_retry_max_delay_sec,
                        self.start_retry_base_delay_sec * (2 ** (attempt - 1)),
                    )
                    self._log(
                        "系统",
                        (
                            f"账号启动失败 {account_id_text}: {last_error}，"
                            f"{delay:.1f}s 后重试({attempt}/{attempts})"
                        ),
                        is_warn=True,
                        module="system",
                        event="start_retry",
                        accountId=account_id_text,
                        retry=attempt,
                        delaySec=delay,
                    )
                    await asyncio.sleep(delay)

    async def stop_account(self, account_id: str | int) -> None:
        account_id_text = str(account_id or "").strip()
        runtime = self._runtimes.get(account_id_text)
        if not runtime:
            await self._set_runtime_status(account_id_text, runtimeState="stopped")
            return
        try:
            await runtime.stop()
        finally:
            self._runtimes.pop(account_id_text, None)
            await self._set_runtime_status(account_id_text, runtimeState="stopped")

    async def get_status(self, account_id: str | int) -> dict[str, Any]:
        account_id_text = str(account_id or "").strip()
        runtime = self._runtimes.get(account_id_text)
        if runtime:
            result = await runtime.get_status()
            result.update(self._runtime_status_view(account_id_text, is_running=True))
            return result
        account = self._find_account(account_id_text)
        if not account:
            raise RuntimeError("账号不存在")
        result = {
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
            "dailyRoutines": self._get_account_settings(account_id_text).get("dailyRoutines", {}),
        }
        result.update(self._runtime_status_view(account_id_text, is_running=False))
        return result

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

    async def get_email_list(self, account_id: str | int, box_type: int = 1) -> dict[str, Any]:
        return await self._require_runtime(account_id).get_email_list(_to_int(box_type, 1))

    async def claim_email(
        self,
        account_id: str | int,
        box_type: int = 1,
        email_id: str = "",
        *,
        batch: bool = False,
    ) -> dict[str, Any]:
        return await self._require_runtime(account_id).claim_email(
            _to_int(box_type, 1),
            str(email_id or ""),
            batch=bool(batch),
        )

    async def get_mall_goods(self, account_id: str | int, slot_type: int = 1) -> dict[str, Any]:
        return await self._require_runtime(account_id).get_mall_goods(_to_int(slot_type, 1))

    async def purchase_mall_goods(self, account_id: str | int, goods_id: int, count: int = 1) -> dict[str, Any]:
        return await self._require_runtime(account_id).purchase_mall_goods(_to_int(goods_id, 0), _to_int(count, 1))

    async def get_monthcard_infos(self, account_id: str | int) -> dict[str, Any]:
        return await self._require_runtime(account_id).get_monthcard_infos()

    async def claim_monthcard_reward(self, account_id: str | int, goods_id: int) -> dict[str, Any]:
        return await self._require_runtime(account_id).claim_monthcard_reward(_to_int(goods_id, 0))

    async def get_vip_daily_status(self, account_id: str | int) -> dict[str, Any]:
        return await self._require_runtime(account_id).get_vip_daily_status()

    async def claim_vip_daily_gift(self, account_id: str | int) -> dict[str, Any]:
        return await self._require_runtime(account_id).claim_vip_daily_gift()

    async def check_can_share(self, account_id: str | int) -> dict[str, Any]:
        return await self._require_runtime(account_id).check_can_share()

    async def report_share(self, account_id: str | int, shared: bool = True) -> dict[str, Any]:
        return await self._require_runtime(account_id).report_share(bool(shared))

    async def claim_share_reward(self, account_id: str | int, claimed: bool = True) -> dict[str, Any]:
        return await self._require_runtime(account_id).claim_share_reward(bool(claimed))

    async def run_daily_routine(self, account_id: str | int, routine: str, force: bool = False) -> dict[str, Any]:
        return await self._require_runtime(account_id).run_daily_routine(str(routine or ""), force=bool(force))

    async def run_daily_routines(self, account_id: str | int, force: bool = False) -> dict[str, Any]:
        return await self._require_runtime(account_id).run_daily_routines(force=bool(force))

    async def get_daily_routines(self, account_id: str | int) -> dict[str, Any]:
        return await self._require_runtime(account_id).get_daily_routines_state()

    async def get_analytics(self, account_id: str | int, sort_by: str) -> list[dict[str, Any]]:
        runtime = self._runtimes.get(str(account_id))
        if runtime:
            return await runtime.get_analytics(sort_by)
        return self.analytics.get_plant_rankings(sort_by)

    async def set_automation(self, account_id: str | int, key: str, value: Any) -> dict[str, Any]:
        payload = {"automation": {str(key): value}}
        return await self.save_settings(account_id, payload)

    async def get_push_settings(self, account_id: str | int) -> dict[str, Any]:
        account_id_text = str(account_id or "").strip()
        cfg = self._get_account_settings(account_id_text)
        return {"push": dict(cfg.get("push", {}))}

    async def save_push_settings(self, account_id: str | int, patch: dict[str, Any]) -> dict[str, Any]:
        account_id_text = str(account_id or "").strip()
        if not account_id_text:
            raise RuntimeError("account_id 不能为空")
        payload = patch if isinstance(patch, dict) else {}
        return await self.save_settings(account_id_text, {"push": payload})

    async def send_push_test(
        self,
        account_id: str | int,
        title: str = "",
        content: str = "",
    ) -> dict[str, Any]:
        account_id_text = str(account_id or "").strip()
        cfg = self._get_account_settings(account_id_text)
        push_cfg = self._normalize_push_settings(cfg.get("push", {}))
        test_title = str(title or "").strip() or "QFarm Push"
        test_content = str(content or "").strip() or "manual push test"
        try:
            result = await self._send_push_with_retry(
                account_id=account_id_text,
                push_cfg=push_cfg,
                title=test_title,
                content=test_content,
                context={
                    "reason": "manual_test",
                    "module": "push",
                    "event": "manual_test",
                    "result": "test",
                },
            )
            return {
                "ok": bool(result.get("ok")),
                "message": str(result.get("message") or "ok"),
                "attempt": _to_int(result.get("attempt"), 1),
                "httpStatus": _to_int(result.get("httpStatus"), 0),
            }
        except Exception as e:
            return {"ok": False, "message": str(e), "attempt": 0, "httpStatus": 0}

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

    async def _persist_runtime_state_patch(self, account_id: str, payload: dict[str, Any]) -> None:
        account_id_text = str(account_id or "").strip()
        if not account_id_text:
            return
        patch = payload if isinstance(payload, dict) else {}
        if not patch:
            return
        async with self._state_lock:
            current = self._get_account_settings(account_id_text)
            next_cfg = self._merge_settings(current, patch)
            cfg_map = self._settings.setdefault("accountConfigs", {})
            cfg_map[account_id_text] = next_cfg
            self._save_json_atomic(self.settings_path, self._settings)

    async def get_settings(self, account_id: str | int) -> dict[str, Any]:
        account_id_text = str(account_id or "").strip()
        cfg = self._get_account_settings(account_id_text)
        return {
            "intervals": cfg.get("intervals", {}),
            "strategy": cfg.get("strategy", "preferred"),
            "preferredSeed": cfg.get("preferredSeedId", 0),
            "friendQuietHours": cfg.get("friendQuietHours", {}),
            "automation": cfg.get("automation", {}),
            "dailyRoutines": cfg.get("dailyRoutines", {}),
            "push": cfg.get("push", {}),
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
        if self._runtime_logs_dirty:
            self._persist_runtime_logs()
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
        if self._runtime_logs_dirty:
            self._persist_runtime_logs()
        safe = max(1, min(300, _to_int(limit, 100)))
        return list(reversed(self._account_logs[-safe:]))

    async def debug_sell(self, account_id: str | int) -> dict[str, Any]:
        return await self._require_runtime(account_id).debug_sell()

    async def qr_create(self) -> dict[str, Any]:
        payload = await self.qr_login.create()
        login_url = str(payload.get("url") or "").strip()
        if not login_url:
            raise RuntimeError("扫码登录链接为空")
        cleanup_qr_cache(self.qr_cache_dir, ttl_sec=self.qr_cache_ttl_sec)
        try:
            payload["qrcode"] = save_qr_png(login_url, self.qr_cache_dir)
        except QRCodeRenderError as e:
            raise RuntimeError(f"本地二维码生成失败: {e}") from e
        return payload

    async def qr_check(self, code: str) -> dict[str, Any]:
        return await self.qr_login.check(str(code or ""))

    def _require_runtime(self, account_id: str | int) -> AccountRuntime:
        account_id_text = str(account_id or "").strip()
        runtime = self._runtimes.get(account_id_text)
        if not runtime:
            state = self._runtime_status_view(account_id_text, is_running=False)
            reason = str(state.get("lastStartError") or "").strip()
            if reason:
                raise RuntimeError(f"账号未运行，最近启动失败: {reason}")
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
            allowed = set((DEFAULT_ACCOUNT_CONFIG.get("automation") or {}).keys())
            merged_auto = result.setdefault("automation", {})
            for key, value in src.get("automation", {}).items():
                if key not in allowed:
                    continue
                if key == "fertilizer":
                    mode = str(value or "both").strip().lower()
                    merged_auto[key] = mode if mode in {"both", "normal", "organic", "none"} else "both"
                    continue
                merged_auto[key] = bool(value)
        if isinstance(src.get("intervals"), dict):
            result.setdefault("intervals", {}).update(src.get("intervals"))
        if isinstance(src.get("friendQuietHours"), dict):
            result.setdefault("friendQuietHours", {}).update(src.get("friendQuietHours"))
        if isinstance(src.get("dailyRoutines"), dict):
            result["dailyRoutines"] = self._merge_daily_routines(result.get("dailyRoutines"), src.get("dailyRoutines"))
        if isinstance(src.get("push"), dict):
            merged_push = result.setdefault("push", {})
            for key, value in src.get("push", {}).items():
                if key == "enabled":
                    merged_push["enabled"] = bool(value)
                elif key == "channel":
                    channel = str(value or "webhook").strip().lower()
                    merged_push["channel"] = channel if channel in {"webhook"} else "webhook"
                elif key in {"endpoint", "url"}:
                    merged_push["endpoint"] = str(value or "").strip()
                elif key == "token":
                    merged_push["token"] = str(value or "").strip()
                elif key == "autoEvents":
                    merged_push["autoEvents"] = "core"
                elif key == "retryMax":
                    merged_push["retryMax"] = max(0, min(5, _to_int(value, 2)))
                elif key in {"allowPrivateEndpoint", "allow_private_endpoint"}:
                    merged_push["allowPrivateEndpoint"] = bool(value)
                elif key in {"bodyTokenEnabled", "body_token_enabled"}:
                    merged_push["bodyTokenEnabled"] = bool(value)
                elif key in {"maxConcurrency", "max_concurrency"}:
                    merged_push["maxConcurrency"] = max(1, min(32, _to_int(value, 8)))
                elif key in {"maxPerMinute", "max_per_minute"}:
                    merged_push["maxPerMinute"] = max(1, min(600, _to_int(value, 60)))
            result["push"] = self._normalize_push_settings(merged_push)
        return result

    @staticmethod
    def _merge_daily_routines(base: Any, patch: Any) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for source in (base, patch):
            if not isinstance(source, dict):
                continue
            for key, value in source.items():
                routine_key = str(key or "").strip()
                if not routine_key:
                    continue
                current = result.setdefault(routine_key, {})
                if not isinstance(value, dict):
                    continue
                if "doneDateKey" in value:
                    current["doneDateKey"] = str(value.get("doneDateKey") or "")
                if "lastCheckAt" in value:
                    current["lastCheckAt"] = max(0, _to_int(value.get("lastCheckAt"), 0))
                if "lastClaimAt" in value:
                    current["lastClaimAt"] = max(0, _to_int(value.get("lastClaimAt"), 0))
                if "lastResult" in value:
                    current["lastResult"] = str(value.get("lastResult") or "")
                if "lastError" in value:
                    current["lastError"] = str(value.get("lastError") or "")
        return result

    @staticmethod
    def _normalize_push_settings(raw: Any) -> dict[str, Any]:
        base = dict((DEFAULT_ACCOUNT_CONFIG.get("push") or {}))
        data = raw if isinstance(raw, dict) else {}
        if "enabled" in data:
            base["enabled"] = bool(data.get("enabled"))
        channel = str(data.get("channel", base.get("channel", "webhook")) or "webhook").strip().lower()
        base["channel"] = channel if channel in {"webhook"} else "webhook"
        endpoint = data.get("endpoint", data.get("url", base.get("endpoint", "")))
        base["endpoint"] = str(endpoint or "").strip()
        base["token"] = str(data.get("token", base.get("token", "")) or "").strip()
        base["autoEvents"] = "core"
        base["retryMax"] = max(0, min(5, _to_int(data.get("retryMax", base.get("retryMax", 2)), 2)))
        base["allowPrivateEndpoint"] = bool(
            data.get(
                "allowPrivateEndpoint",
                data.get("allow_private_endpoint", base.get("allowPrivateEndpoint", False)),
            )
        )
        base["bodyTokenEnabled"] = bool(
            data.get(
                "bodyTokenEnabled",
                data.get("body_token_enabled", base.get("bodyTokenEnabled", False)),
            )
        )
        base["maxConcurrency"] = max(
            1,
            min(
                32,
                _to_int(
                    data.get(
                        "maxConcurrency",
                        data.get("max_concurrency", base.get("maxConcurrency", 8)),
                    ),
                    8,
                ),
            ),
        )
        base["maxPerMinute"] = max(
            1,
            min(
                600,
                _to_int(
                    data.get(
                        "maxPerMinute",
                        data.get("max_per_minute", base.get("maxPerMinute", 60)),
                    ),
                    60,
                ),
            ),
        )
        return base

    @staticmethod
    def _extract_entry_account_id(entry: dict[str, Any]) -> str:
        account_id = str(entry.get("accountId") or "").strip()
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        if not account_id:
            account_id = str(meta.get("accountId") or "").strip()
        return account_id

    def _should_auto_push_entry(self, entry: dict[str, Any]) -> bool:
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        module = str(meta.get("module") or "").strip()
        event = str(meta.get("event") or "").strip()
        result = str(meta.get("result") or "").strip().lower()
        if module == "push":
            return False
        if not event:
            return False
        if module == "task":
            if event == "daily_summary":
                return True
            return event in CORE_PUSH_TASK_ERROR_EVENTS and result == "error"
        if module == "system":
            return event in CORE_PUSH_SYSTEM_EVENTS
        return False

    def _build_push_title_content(self, entry: dict[str, Any]) -> tuple[str, str]:
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        module = str(meta.get("module") or "").strip() or "system"
        event = str(meta.get("event") or "").strip() or "unknown"
        result = str(meta.get("result") or "").strip() or "-"
        account_id = self._extract_entry_account_id(entry) or "-"
        time_text = str(entry.get("time") or "")
        msg = str(entry.get("msg") or "")
        title = f"QFarm {module}/{event}"
        content = f"[{time_text}] account={account_id} result={result}\n{msg}"
        return title, content

    def _schedule_auto_push(self, entry: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._auto_push_pending >= self._auto_push_pending_limit:
            self._on_runtime_log(
                self._extract_entry_account_id(entry),
                "push",
                "auto push dropped: pending queue overflow",
                True,
                {
                    "module": "push",
                    "event": "queue_drop",
                    "result": "error",
                    "pending": self._auto_push_pending,
                    "limit": self._auto_push_pending_limit,
                },
            )
            return
        try:
            self._auto_push_pending += 1
            task = loop.create_task(self._deliver_auto_push(entry))
            task.add_done_callback(self._on_auto_push_task_done)
        except Exception:
            self._auto_push_pending = max(0, self._auto_push_pending - 1)
            return

    def _on_auto_push_task_done(self, _: asyncio.Task[Any]) -> None:
        self._auto_push_pending = max(0, self._auto_push_pending - 1)

    async def _deliver_auto_push(self, entry: dict[str, Any]) -> None:
        account_id = self._extract_entry_account_id(entry)
        if not account_id:
            return
        cfg = self._get_account_settings(account_id)
        push_cfg = self._normalize_push_settings(cfg.get("push", {}))
        if not bool(push_cfg.get("enabled", False)):
            return
        if str(push_cfg.get("autoEvents") or "core").strip().lower() != "core":
            return
        title, content = self._build_push_title_content(entry)
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        context = {
            "reason": "auto_event",
            "module": str(meta.get("module") or ""),
            "event": str(meta.get("event") or ""),
            "result": str(meta.get("result") or ""),
            "time": str(entry.get("time") or ""),
        }
        try:
            await self._send_push_with_retry(
                account_id=account_id,
                push_cfg=push_cfg,
                title=title,
                content=content,
                context=context,
            )
        except Exception:
            return

    async def _send_push_with_retry(
        self,
        *,
        account_id: str,
        push_cfg: dict[str, Any],
        title: str,
        content: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = self._normalize_push_settings(push_cfg)
        retry_max = max(0, min(5, _to_int(cfg.get("retryMax"), 2)))
        last_error: Exception | None = None
        for idx in range(retry_max + 1):
            attempt = idx + 1
            try:
                await self._acquire_push_rate_slot(account_id=account_id, push_cfg=cfg)
                async with self._push_semaphore:
                    result = await self._send_push_once(
                        account_id=account_id,
                        push_cfg=cfg,
                        title=title,
                        content=content,
                        context=context,
                    )
                self._on_runtime_log(
                    account_id,
                    "push",
                    f"push delivered: {self._sanitize_push_text(str(result.get('message') or 'ok'))}",
                    False,
                    {
                        "module": "push",
                        "event": "deliver",
                        "result": "ok",
                        "attempt": attempt,
                        "channel": str(cfg.get("channel") or "webhook"),
                        "accountId": account_id,
                        "httpStatus": _to_int(result.get("httpStatus"), 0),
                        "errorCode": "",
                        "reason": str(context.get("reason") or ""),
                    },
                )
                return {
                    "ok": True,
                    "attempt": attempt,
                    "httpStatus": _to_int(result.get("httpStatus"), 0),
                    "message": str(result.get("message") or "ok"),
                }
            except Exception as e:
                last_error = e
                if isinstance(e, PushDeliverError):
                    http_status = e.http_status
                    error_code = e.error_code or "deliver_error"
                    err_msg = self._sanitize_push_text(str(e))
                else:
                    http_status = 0
                    error_code = type(e).__name__
                    err_msg = self._sanitize_push_text(str(e))
                self._on_runtime_log(
                    account_id,
                    "push",
                    f"push deliver failed: {err_msg}",
                    True,
                    {
                        "module": "push",
                        "event": "deliver",
                        "result": "error",
                        "attempt": attempt,
                        "channel": str(cfg.get("channel") or "webhook"),
                        "accountId": account_id,
                        "httpStatus": http_status,
                        "errorCode": error_code,
                        "reason": str(context.get("reason") or ""),
                    },
                )
                if idx >= retry_max:
                    break
                delay = min(4.0, 0.5 * (2 ** idx))
                await asyncio.sleep(delay)
        raise RuntimeError(str(last_error) if last_error else "push deliver failed")

    async def _send_push_once(
        self,
        *,
        account_id: str,
        push_cfg: dict[str, Any],
        title: str,
        content: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = self._normalize_push_settings(push_cfg)
        channel = str(cfg.get("channel") or "webhook").strip().lower()
        if channel != "webhook":
            raise PushDeliverError(f"unsupported push channel: {channel}", error_code="channel_unsupported")
        endpoint = str(cfg.get("endpoint") or "").strip()
        if not endpoint:
            raise PushDeliverError("push endpoint is empty", error_code="endpoint_empty")
        endpoint = self._validate_push_endpoint(
            endpoint,
            allow_private=bool(cfg.get("allowPrivateEndpoint", False)),
        )
        token = str(cfg.get("token") or "").strip()
        include_body_token = bool(cfg.get("bodyTokenEnabled", False))
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {
            "title": str(title or "QFarm Push"),
            "content": str(content or ""),
            "accountId": str(account_id or ""),
            "module": str(context.get("module") or ""),
            "event": str(context.get("event") or ""),
            "result": str(context.get("result") or ""),
            "time": str(context.get("time") or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())),
        }
        if include_body_token and token:
            body["token"] = token
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(endpoint, json=body, headers=headers) as response:
                    text = (await response.text()).strip()
                    if response.status < 200 or response.status >= 300:
                        snippet = self._sanitize_push_text(text if text else "no_response_body")
                        raise PushDeliverError(
                            f"push webhook http {response.status}: {snippet}",
                            http_status=response.status,
                            error_code=f"http_{response.status}",
                        )
                    return {"httpStatus": int(response.status), "message": self._sanitize_push_text(text) or "ok"}
        except asyncio.TimeoutError as e:
            raise PushDeliverError("push request timeout", error_code="timeout") from e
        except aiohttp.ClientError as e:
            raise PushDeliverError(self._sanitize_push_text(f"push request error: {e}"), error_code="request_error") from e

    async def _acquire_push_rate_slot(self, *, account_id: str, push_cfg: dict[str, Any]) -> None:
        key = str(account_id or "").strip()
        if not key:
            return
        limit = max(1, min(600, _to_int(push_cfg.get("maxPerMinute"), 60)))
        now = time.monotonic()
        async with self._push_rate_lock:
            window = self._push_rate_windows.setdefault(key, deque())
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= limit:
                raise PushDeliverError("push rate limit exceeded", error_code="rate_limited")
            window.append(now)

    @staticmethod
    def _is_private_host(host: str) -> bool:
        name = str(host or "").strip().lower()
        if not name:
            return True
        if name == "localhost" or name.endswith(".localhost"):
            return True
        try:
            ip = ipaddress.ip_address(name)
        except ValueError:
            return False
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )

    def _validate_push_endpoint(self, endpoint: str, *, allow_private: bool) -> str:
        target = str(endpoint or "").strip()
        parsed = urlparse(target)
        scheme = str(parsed.scheme or "").strip().lower()
        host = str(parsed.hostname or "").strip()
        if scheme != "https":
            raise PushDeliverError("push endpoint scheme must be https", error_code="endpoint_scheme")
        if not host:
            raise PushDeliverError("push endpoint host is empty", error_code="endpoint_host_empty")
        if parsed.username or parsed.password:
            raise PushDeliverError("push endpoint should not embed credentials", error_code="endpoint_credentials")
        if not allow_private and self._is_private_host(host):
            raise PushDeliverError("push endpoint private host is not allowed", error_code="endpoint_private")
        return target

    def _sanitize_push_text(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        sanitized = re.sub(
            r"(?i)(authorization|token|cookie)\s*[:=]\s*[^,\s;]+",
            r"\1=***",
            raw,
        )
        return sanitized[: self._PUSH_ERROR_SNIPPET_MAX]

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

    def _runtime_status_view(self, account_id: str, *, is_running: bool) -> dict[str, Any]:
        status_map = self._runtime_data.get("status") or {}
        row = status_map.get(str(account_id), {}) if isinstance(status_map, dict) else {}
        if not isinstance(row, dict):
            row = {}
        runtime_state = str(row.get("runtimeState") or "")
        if is_running:
            runtime_state = "running"
        elif not runtime_state:
            runtime_state = "stopped"
        return {
            "runtimeState": runtime_state,
            "lastStartError": str(row.get("lastStartError") or ""),
            "lastStartAt": _to_int(row.get("lastStartAt"), 0),
            "lastStartSuccessAt": _to_int(row.get("lastStartSuccessAt"), 0),
            "startRetryCount": _to_int(row.get("startRetryCount"), 0),
        }

    async def _set_runtime_status(self, account_id: str, **patch: Any) -> None:
        account_id_text = str(account_id or "").strip()
        if not account_id_text:
            return
        async with self._runtime_status_lock:
            status_map = self._runtime_data.setdefault("status", {})
            if not isinstance(status_map, dict):
                status_map = {}
                self._runtime_data["status"] = status_map
            current = status_map.get(account_id_text, {})
            if not isinstance(current, dict):
                current = {}
            merged = {**current, **patch}
            status_map[account_id_text] = merged
            self._save_json_atomic(self.runtime_path, self._runtime_data)

    async def _clear_runtime_status(self, account_id: str) -> None:
        async with self._runtime_status_lock:
            status_map = self._runtime_data.get("status")
            if not isinstance(status_map, dict):
                return
            if str(account_id) not in status_map:
                return
            status_map.pop(str(account_id), None)
            self._save_json_atomic(self.runtime_path, self._runtime_data)

    def _is_retryable_start_error(self, error: str) -> bool:
        text = str(error or "").strip().lower()
        if not text:
            return False
        non_retryable = (
            "missing login code",
            "code 不能为空",
            ".login error=",
            "userservice.login error=",
            "账号不存在",
            "account_id",
            "invalid response status",
            "status', url='wss://",
            " 400",
        )
        if any(word in text for word in non_retryable):
            return False
        retryable = (
            "websocket disconnected",
            "websocket connect failed",
            "connect failed",
            "cannot connect",
            "request timeout",
            "timeout",
            "timed out",
            "connection reset",
            "broken pipe",
            "network",
            "temporarily unavailable",
            "ws",
        )
        return any(word in text for word in retryable)

    def _normalize_start_error(self, error: str) -> str:
        text = str(error or "").strip()
        lowered = text.lower()
        if not text:
            return "未知错误"
        if "invalid response status" in lowered and "400" in lowered:
            return "网关鉴权失败(HTTP 400)，登录凭据可能已失效，请重新绑定 code 或重新扫码绑定。"
        if "missing login code" in lowered or "code 不能为空" in lowered:
            return "缺少登录凭据 code，请重新绑定 code 或重新扫码绑定。"
        if ".login error=" in lowered or "userservice.login error=" in lowered:
            return f"登录鉴权失败，请重新绑定 code 或重新扫码绑定。原始错误: {text}"
        return text

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
        if len(self._global_logs) > self.runtime_log_max_entries:
            self._global_logs = self._global_logs[-self.runtime_log_max_entries :]
        self._schedule_runtime_logs_persist()
        try:
            if self._should_auto_push_entry(entry):
                self._schedule_auto_push(entry)
        except Exception:
            return

    async def _on_runtime_kicked(self, account_id: str, reason: str) -> None:
        self._add_account_log("kickout_delete", f"账号被踢下线，已删除: {reason}", account_id, "", reason=reason)
        self._on_runtime_log(
            str(account_id or ""),
            "system",
            f"account kicked and removed: {reason}",
            True,
            {
                "module": "system",
                "event": "kickout_delete",
                "result": "error",
                "reason": str(reason or ""),
                "accountId": str(account_id or ""),
            },
        )
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
        account_log_max = max(300, min(2000, self.runtime_log_max_entries))
        if len(self._account_logs) > account_log_max:
            self._account_logs = self._account_logs[-account_log_max:]
        self._schedule_runtime_logs_persist()

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

    async def _get_account_view_by_id(self, account_id: str) -> dict[str, Any] | None:
        data = await self.get_accounts()
        accounts = data.get("accounts", []) if isinstance(data, dict) else []
        for row in accounts:
            if str(row.get("id") or "").strip() == str(account_id or "").strip():
                return dict(row)
        return None

    def _load_persisted_runtime_logs(self) -> None:
        if not self.persist_runtime_logs:
            return
        default = {"global": [], "account": []}
        raw = self._load_json(self.runtime_logs_path, default)
        global_logs: list[dict[str, Any]] = []
        account_logs: list[dict[str, Any]] = []
        for row in raw.get("global", []) if isinstance(raw, dict) else []:
            if not isinstance(row, dict):
                continue
            entry = dict(row)
            entry["time"] = str(entry.get("time") or "")
            entry["tag"] = str(entry.get("tag") or "")
            entry["msg"] = str(entry.get("msg") or "")
            entry["isWarn"] = bool(entry.get("isWarn"))
            entry["accountId"] = str(entry.get("accountId") or "")
            entry["meta"] = dict(entry.get("meta") or {})
            entry["ts"] = _to_int(entry.get("ts"), 0)
            entry["_searchText"] = (
                f"{entry['msg']} {entry['tag']} {json.dumps(entry['meta'], ensure_ascii=False)}".lower()
            )
            global_logs.append(entry)
        for row in raw.get("account", []) if isinstance(raw, dict) else []:
            if not isinstance(row, dict):
                continue
            account_logs.append(dict(row))
        if len(global_logs) > self.runtime_log_max_entries:
            global_logs = global_logs[-self.runtime_log_max_entries :]
        account_log_max = max(300, min(2000, self.runtime_log_max_entries))
        if len(account_logs) > account_log_max:
            account_logs = account_logs[-account_log_max:]
        self._global_logs = global_logs
        self._account_logs = account_logs
        self._runtime_logs_dirty = False
        self._runtime_logs_pending = 0
        self._runtime_logs_last_flush_at = time.monotonic()

    def _schedule_runtime_logs_persist(self) -> None:
        if not self.persist_runtime_logs:
            return
        self._runtime_logs_dirty = True
        self._runtime_logs_pending += 1
        elapsed = time.monotonic() - self._runtime_logs_last_flush_at
        should_flush = (
            self._runtime_logs_pending >= self.runtime_log_flush_batch
            or elapsed >= self.runtime_log_flush_interval_sec
        )
        if should_flush:
            self._persist_runtime_logs()

    def _persist_runtime_logs(self, *, force: bool = False) -> None:
        if not self.persist_runtime_logs:
            return
        if not force and not self._runtime_logs_dirty:
            return
        payload = {
            "global": [{k: v for k, v in row.items() if k != "_searchText"} for row in self._global_logs],
            "account": list(self._account_logs),
        }
        try:
            self._save_json_atomic(self.runtime_logs_path, payload)
        except Exception:
            return
        self._runtime_logs_dirty = False
        self._runtime_logs_pending = 0
        self._runtime_logs_last_flush_at = time.monotonic()

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

