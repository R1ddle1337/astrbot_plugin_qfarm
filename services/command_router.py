from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .api_client import QFarmApiClient, QFarmApiError
from .process_manager import NodeProcessManager
from .rate_limiter import RateLimitError, RateLimiter
from .state_store import QFarmStateStore


def tokenize_command(message: str) -> list[str]:
    return [seg for seg in re.split(r"\s+", str(message or "").strip()) if seg]


COMPOUND_COMMAND_MAP: dict[str, list[str]] = {
    "\u519c\u7530\u67e5\u770b": ["\u519c\u7530", "\u67e5\u770b"],
    "\u519c\u7530\u64cd\u4f5c": ["\u519c\u7530", "\u64cd\u4f5c"],
    "\u597d\u53cb\u5217\u8868": ["\u597d\u53cb", "\u5217\u8868"],
    "\u8d26\u53f7\u67e5\u770b": ["\u8d26\u53f7", "\u67e5\u770b"],
    "\u8d26\u53f7\u542f\u52a8": ["\u8d26\u53f7", "\u542f\u52a8"],
    "\u8d26\u53f7\u505c\u6b62": ["\u8d26\u53f7", "\u505c\u6b62"],
    "\u8d26\u53f7\u89e3\u7ed1": ["\u8d26\u53f7", "\u89e3\u7ed1"],
    "\u8d26\u53f7\u7ed1\u5b9a\u626b\u7801": ["\u8d26\u53f7", "\u7ed1\u5b9a\u626b\u7801"],
    "\u8d26\u53f7\u53d6\u6d88\u626b\u7801": ["\u8d26\u53f7", "\u53d6\u6d88\u626b\u7801"],
    "\u81ea\u52a8\u5316\u67e5\u770b": ["\u81ea\u52a8\u5316", "\u67e5\u770b"],
    "\u80cc\u5305\u67e5\u770b": ["\u80cc\u5305", "\u67e5\u770b"],
    "\u79cd\u5b50\u5217\u8868": ["\u79cd\u5b50", "\u5217\u8868"],
    "\u670d\u52a1\u72b6\u6001": ["\u670d\u52a1", "\u72b6\u6001"],
    "\u670d\u52a1\u542f\u52a8": ["\u670d\u52a1", "\u542f\u52a8"],
    "\u670d\u52a1\u505c\u6b62": ["\u670d\u52a1", "\u505c\u6b62"],
    "\u670d\u52a1\u91cd\u542f": ["\u670d\u52a1", "\u91cd\u542f"],
}


def normalize_compound_tokens(tokens: list[str]) -> list[str]:
    if not tokens:
        return []
    first = str(tokens[0]).strip()
    mapped = COMPOUND_COMMAND_MAP.get(first)
    if not mapped:
        return tokens
    return mapped + tokens[1:]


def parse_key_value_args(tokens: list[str]) -> tuple[int | None, dict[str, str]]:
    limit = None
    options: dict[str, str] = {}
    for token in tokens:
        raw = str(token).strip()
        if not raw:
            continue
        if limit is None and raw.isdigit():
            limit = int(raw)
            continue
        if "=" in raw:
            key, value = raw.split("=", 1)
            options[key.strip()] = value.strip()
    return limit, options


@dataclass
class RouterReply:
    text: str = ""
    image_url: str | None = None
    prefer_image: bool = False


class QFarmCommandRouter:
    FARM_OPS = {"all", "harvest", "clear", "plant", "upgrade"}
    FRIEND_OPS = {"steal", "water", "weed", "bug", "bad"}
    ANALYTICS_SORTS = {"exp", "fert", "profit", "fert_profit", "level"}
    STRATEGIES = {
        "preferred",
        "level",
        "max_exp",
        "max_fert_exp",
        "max_profit",
        "max_fert_profit",
    }
    FERTILIZER_MODES = {"both", "normal", "organic", "none"}
    AUTOMATION_KEYS = {
        "farm",
        "farm_push",
        "land_upgrade",
        "friend",
        "friend_steal",
        "friend_help",
        "friend_bad",
        "task",
        "sell",
    }

    def __init__(
        self,
        api_client: QFarmApiClient,
        state_store: QFarmStateStore,
        rate_limiter: RateLimiter,
        process_manager: NodeProcessManager,
        is_super_admin: Callable[[str], bool],
        send_active_message: Callable[[Any, str], Awaitable[None]] | None = None,
        logger: Any | None = None,
    ) -> None:
        self.api = api_client
        self.state_store = state_store
        self.rate_limiter = rate_limiter
        self.process_manager = process_manager
        self.is_super_admin = is_super_admin
        self.send_active_message = send_active_message
        self.logger = logger
        self._qr_tasks: dict[str, asyncio.Task] = {}

    async def shutdown(self) -> None:
        for task in list(self._qr_tasks.values()):
            task.cancel()
        if self._qr_tasks:
            await asyncio.gather(*self._qr_tasks.values(), return_exceptions=True)
        self._qr_tasks.clear()

    async def handle(self, event: Any) -> list[RouterReply]:
        tokens = tokenize_command(getattr(event, "message_str", ""))
        if tokens and self._token(tokens[0]) in {"qfarm", "农场"}:
            tokens = tokens[1:]
        elif tokens and str(tokens[0]).strip().lower().startswith("qfarm"):
            merged = str(tokens[0]).strip()
            suffix = merged[5:].strip()
            if suffix:
                tokens = [suffix, *tokens[1:]]

        tokens = normalize_compound_tokens(tokens)
        if not tokens:
            return [RouterReply(text=self._help_text())]

        user_id = self._get_user_id(event)
        group_id = self._get_group_id(event)
        if not user_id:
            return [RouterReply(text="无法识别发送者身份，拒绝执行。")]

        top = self._token(tokens[0])
        if self._is_super_admin_cmd(top) and not self.is_super_admin(user_id):
            return [RouterReply(text="权限不足：该命令仅超级管理员可用。")]

        access_ok, deny_msg = self._check_access(user_id, group_id)
        if not access_ok and not self.is_super_admin(user_id):
            return [RouterReply(text=deny_msg)]

        is_write = self._is_write_command(tokens)
        bound_account_for_lock = self.state_store.get_bound_account(user_id) if is_write else None
        lease = None
        try:
            lease = await self.rate_limiter.acquire(
                user_id=user_id,
                is_write=is_write,
                account_id=bound_account_for_lock,
            )
            replies = await self._dispatch(event, user_id, tokens)
            return self._mark_render_candidates(replies)
        except RateLimitError as e:
            return [RouterReply(text=str(e))]
        except QFarmApiError as e:
            return [RouterReply(text=f"操作失败: {e}")]
        except Exception as e:
            self._log_warning(f"命令处理异常: {e}")
            return [RouterReply(text=f"命令执行异常: {e}")]
        finally:
            if lease is not None:
                lease.release()

    async def _dispatch(self, event: Any, user_id: str, tokens: list[str]) -> list[RouterReply]:
        cmd = self._token(tokens[0])
        args = tokens[1:]
        if cmd in {"帮助", "help", "h", "?"}:
            return [RouterReply(text=self._help_text())]
        if cmd in {"服务", "service"}:
            return await self._cmd_service(args)
        if cmd in {"账号", "account"}:
            return await self._cmd_account(event, user_id, args)
        if cmd in {"状态", "status"}:
            return await self._cmd_status(user_id)
        if cmd in {"农田", "farm"}:
            return await self._cmd_farm(user_id, args)
        if cmd in {"好友", "friend"}:
            return await self._cmd_friend(user_id, args)
        if cmd in {"种子", "seed", "seeds"}:
            return await self._cmd_seeds(user_id, args)
        if cmd in {"背包", "bag"}:
            return await self._cmd_bag(user_id, args)
        if cmd in {"分析", "analytics", "analysis"}:
            return await self._cmd_analytics(user_id, args)
        if cmd in {"自动化", "automation", "auto"}:
            return await self._cmd_automation(user_id, args)
        if cmd in {"设置", "setting", "settings"}:
            return await self._cmd_settings(user_id, args)
        if cmd in {"主题", "theme"}:
            return await self._cmd_theme(args)
        if cmd in {"日志", "log", "logs"}:
            return await self._cmd_logs(user_id, args)
        if cmd in {"账号日志", "accountlogs", "account-logs"}:
            return await self._cmd_account_logs(args)
        if cmd in {"调试", "debug"}:
            return await self._cmd_debug(user_id, args)
        if cmd in {"白名单", "whitelist"}:
            return await self._cmd_whitelist(args)
        return [RouterReply(text=f"未知命令: {tokens[0]}\n\n{self._help_text()}")]

    async def _cmd_service(self, args: list[str]) -> list[RouterReply]:
        action = self._token(args[0]) if args else "状态"
        if action in {"状态", "status"}:
            ping_ok = False
            ping_error = ""
            try:
                await self.api.ping()
                ping_ok = True
            except Exception as e:
                ping_error = str(e)
            p = self.process_manager.status()
            lines = [
                "【服务状态】",
                f"托管模式: {'开' if p.get('managed_mode') else '关'}",
                f"进程运行: {'是' if p.get('running') else '否'}",
                f"PID: {p.get('pid') or '-'}",
                f"运行账号数: {p.get('runtimeCount', '-')}",
                f"启动重试中: {p.get('retryingCount', 0)}",
                f"启动失败账号: {p.get('failedCount', 0)}",
                f"API可达: {'是' if ping_ok else '否'}",
            ]
            if ping_error:
                lines.append(f"API错误: {ping_error}")
            failed_accounts = p.get("failedAccounts") if isinstance(p, dict) else []
            if isinstance(failed_accounts, list) and failed_accounts:
                lines.append("失败摘要:")
                for row in failed_accounts[:5]:
                    if not isinstance(row, dict):
                        continue
                    aid = row.get("accountId") or "-"
                    retry = row.get("retryCount")
                    err = row.get("error") or "-"
                    lines.append(f"- 账号{aid} (重试{retry}): {err}")
                if len(failed_accounts) > 5:
                    lines.append(f"... 共 {len(failed_accounts)} 个失败账号")
            return [RouterReply(text="\n".join(lines))]
        if action in {"启动", "start"}:
            await self.process_manager.start()
            ok, message = await self._wait_for_api_ready()
            return [RouterReply(text="服务已启动。" if ok else f"服务进程已启动，但 API 尚未就绪: {message}")]
        if action in {"停止", "stop"}:
            await self.process_manager.stop()
            return [RouterReply(text="服务已停止。")]
        if action in {"重启", "restart"}:
            await self.process_manager.restart()
            ok, message = await self._wait_for_api_ready()
            return [RouterReply(text="服务已重启。" if ok else f"服务已重启，但 API 尚未就绪: {message}")]
        return [RouterReply(text="用法: qfarm 服务 状态|启动|停止|重启")]

    async def _cmd_account(self, event: Any, user_id: str, args: list[str]) -> list[RouterReply]:
        if not args:
            return [RouterReply(text="用法: qfarm 账号 查看|绑定|解绑|启动|停止|重连|取消扫码")]

        sub = self._token(args[0])
        if sub in {"查看", "view"}:
            info = self.state_store.get_bound_account_info(user_id)
            if not info:
                return [RouterReply(text="你还没有绑定账号。使用: qfarm 账号 绑定 code <code> [备注名]")]
            account = await self._fetch_account_by_id(info["account_id"])
            if not account:
                self.state_store.unbind_account(user_id)
                return [RouterReply(text="检测到绑定账号已不存在，已自动解绑，请重新绑定。")]

            status_data = await self.api.get_status(info["account_id"])
            runtime_state = status_data.get("runtimeState", "stopped")
            retry_count = status_data.get("startRetryCount", 0)
            last_error = status_data.get("lastStartError", "")
            lines = [
                "【账号绑定】",
                f"用户ID: {user_id}",
                f"账号ID: {account.get('id')}",
                f"账号名: {account.get('name') or '-'}",
                f"平台: {account.get('platform') or '-'}",
                f"QQ/UIN: {account.get('qq') or account.get('uin') or '-'}",
                f"运行中: {'是' if account.get('running') else '否'}",
                f"运行态: {runtime_state}",
                f"启动重试次数: {retry_count}",
            ]
            if last_error:
                lines.append(f"最近启动错误: {last_error}")
            return [RouterReply(text="\n".join(lines))]

        if sub in {"绑定扫码", "bindscan", "扫码绑定"} or (
            sub in {"绑定", "bind"} and len(args) >= 2 and self._token(args[1]) in {"扫码", "scan"}
        ):
            return await self._start_qr_bind(event, user_id)

        if sub in {"取消扫码", "cancelscan"}:
            task = self._qr_tasks.pop(user_id, None)
            if not task:
                return [RouterReply(text="当前没有进行中的扫码绑定任务。")]
            task.cancel()
            return [RouterReply(text="已取消扫码绑定。")]

        if sub in {"绑定", "bind"}:
            if len(args) < 3 or self._token(args[1]) != "code":
                return [RouterReply(text="用法: qfarm 账号 绑定 code <code> [备注名]")]
            code = args[2].strip()
            if not code:
                return [RouterReply(text="code 不能为空。")]
            name = " ".join(args[3:]).strip()
            account = await self._bind_account_with_code(user_id, code=code, account_name=name)
            return [RouterReply(text=f"绑定成功: 账号ID={account.get('id')} 名称={account.get('name') or '-'}")]

        if sub in {"解绑", "unbind"}:
            account_id = self.state_store.get_bound_account(user_id)
            if not account_id:
                return [RouterReply(text="你当前没有已绑定账号。")]
            try:
                await self.api.delete_account(account_id)
            except Exception as e:
                self._log_warning(f"删除账号失败(忽略): {e}")
            self.state_store.unbind_account(user_id)
            return [RouterReply(text=f"解绑成功，账号 {account_id} 已删除并解除绑定。")]

        if sub in {"启动", "start"}:
            account_id, _ = await self._require_bound_account(user_id)
            await self.api.start_account(account_id)
            status_data = await self.api.get_status(account_id)
            runtime_state = status_data.get("runtimeState", "running")
            retry_count = status_data.get("startRetryCount", 0)
            return [RouterReply(text=f"账号启动完成: state={runtime_state}, retries={retry_count}")]

        if sub in {"停止", "stop"}:
            account_id, _ = await self._require_bound_account(user_id)
            await self.api.stop_account(account_id)
            return [RouterReply(text="账号停止指令已发送。")]

        if sub in {"重连", "reconnect"}:
            account_id, account = await self._require_bound_account(user_id)
            if len(args) >= 2:
                new_code = args[1].strip()
                if not new_code:
                    return [RouterReply(text="reconnect 的 code 不能为空。")]
                payload = {
                    "id": account_id,
                    "name": account.get("name") or f"用户{user_id}",
                    "platform": account.get("platform") or "qq",
                    "code": new_code,
                    "uin": account.get("uin") or "",
                    "qq": account.get("qq") or account.get("uin") or "",
                    "avatar": account.get("avatar") or "",
                }
                await self.api.upsert_account(payload)
                return [RouterReply(text="账号 code 已更新并触发重连。")]
            await self.api.stop_account(account_id)
            await self.api.start_account(account_id)
            return [RouterReply(text="账号已执行停止+启动重连。")]

        return [RouterReply(text="未知账号子命令。")]

    async def _cmd_status(self, user_id: str) -> list[RouterReply]:
        account_id, _ = await self._require_bound_account(user_id)
        data = await self.api.get_status(account_id)
        return [RouterReply(text="\n".join(self._format_status(data)))]

    async def _cmd_farm(self, user_id: str, args: list[str]) -> list[RouterReply]:
        if not args:
            return [RouterReply(text="用法: qfarm 农田 查看 | qfarm 农田 操作 all|harvest|clear|plant|upgrade")]
        sub = self._token(args[0])
        account_id, _ = await self._require_bound_account(user_id)
        if sub in {"查看", "view"}:
            data = await self.api.get_lands(account_id)
            return [RouterReply(text="\n".join(self._format_lands(data)))]
        if sub in {"操作", "op", "operate"}:
            if len(args) < 2:
                return [RouterReply(text="用法: qfarm 农田 操作 all|harvest|clear|plant|upgrade")]
            op_type = self._token(args[1])
            if op_type not in self.FARM_OPS:
                return [RouterReply(text=f"不支持的农田操作: {op_type}")]
            await self.api.do_farm_operation(account_id, op_type)
            return [RouterReply(text=f"农田操作已提交: {op_type}")]
        return [RouterReply(text="未知农田子命令。")]

    async def _cmd_friend(self, user_id: str, args: list[str]) -> list[RouterReply]:
        if not args:
            return [RouterReply(text="用法: qfarm 好友 列表 | 好友 农田 <gid> | 好友 操作 <gid> <op>")]
        sub = self._token(args[0])
        account_id, _ = await self._require_bound_account(user_id)
        if sub in {"列表", "list"}:
            data = await self.api.get_friends(account_id)
            return [RouterReply(text="\n".join(self._format_friends(data)))]
        if sub in {"农田", "lands"}:
            if len(args) < 2:
                return [RouterReply(text="用法: qfarm 好友 农田 <gid>")]
            gid = args[1]
            data = await self.api.get_friend_lands(account_id, gid)
            return [RouterReply(text="\n".join(self._format_friend_lands(gid, data)))]
        if sub in {"操作", "op", "operate"}:
            if len(args) < 3:
                return [RouterReply(text="用法: qfarm 好友 操作 <gid> steal|water|weed|bug|bad")]
            gid = args[1]
            op_type = self._token(args[2])
            if op_type not in self.FRIEND_OPS:
                return [RouterReply(text=f"不支持的好友操作: {op_type}")]
            result = await self.api.do_friend_op(account_id, gid, op_type)
            text = f"好友操作完成: gid={gid}, op={op_type}"
            if isinstance(result, dict):
                if result.get("message"):
                    text += f"\n结果: {result.get('message')}"
                if result.get("count") is not None:
                    text += f"\n数量: {result.get('count')}"
            return [RouterReply(text=text)]
        return [RouterReply(text="未知好友子命令。")]

    async def _cmd_seeds(self, user_id: str, args: list[str]) -> list[RouterReply]:
        if args and self._token(args[0]) not in {"查看", "list", "列表"}:
            return [RouterReply(text="用法: qfarm 种子 列表")]
        account_id, _ = await self._require_bound_account(user_id)
        seeds = await self.api.get_seeds(account_id)
        lines = ["【种子列表】"]
        if not seeds:
            lines.append("无数据。")
            return [RouterReply(text="\n".join(lines))]
        for seed in seeds[:60]:
            seed_id = seed.get("seedId")
            name = seed.get("name") or f"种子{seed_id}"
            price = seed.get("price")
            level = seed.get("requiredLevel")
            marks = []
            if seed.get("locked"):
                marks.append("未解锁")
            if seed.get("soldOut"):
                marks.append("售罄")
            mark_text = f" [{'|'.join(marks)}]" if marks else ""
            lines.append(f"- {seed_id}: {name} Lv{level} 价格{price}{mark_text}")
        if len(seeds) > 60:
            lines.append(f"... 共 {len(seeds)} 条，仅展示前 60 条。")
        return [RouterReply(text="\n".join(lines))]

    async def _cmd_bag(self, user_id: str, args: list[str]) -> list[RouterReply]:
        if args and self._token(args[0]) not in {"查看", "view", "列表", "list"}:
            return [RouterReply(text="用法: qfarm 背包 查看")]
        account_id, _ = await self._require_bound_account(user_id)
        bag = await self.api.get_bag(account_id)
        items = bag.get("items", []) if isinstance(bag, dict) else []
        total = bag.get("totalKinds", len(items)) if isinstance(bag, dict) else len(items)
        lines = [f"【背包】种类数: {total}"]
        if not items:
            lines.append("暂无物品。")
            return [RouterReply(text="\n".join(lines))]
        for item in items[:80]:
            item_id = item.get("id")
            name = item.get("name") or f"物品{item_id}"
            count = item.get("count", 0)
            category = item.get("category") or "item"
            extra = item.get("hoursText") or ""
            detail = f" ({extra})" if extra else ""
            lines.append(f"- {item_id}: {name} x{count} [{category}]{detail}")
        if len(items) > 80:
            lines.append(f"... 共 {len(items)} 条，仅展示前 80 条。")
        return [RouterReply(text="\n".join(lines))]

    async def _cmd_analytics(self, user_id: str, args: list[str]) -> list[RouterReply]:
        sort_by = self._token(args[0]) if args else "exp"
        if sort_by not in self.ANALYTICS_SORTS:
            return [RouterReply(text="用法: qfarm 分析 [exp|fert|profit|fert_profit|level]")]
        account_id, _ = await self._require_bound_account(user_id)
        rows = await self.api.get_analytics(account_id, sort_by)
        lines = [f"【作物分析】排序: {sort_by}"]
        if not rows:
            lines.append("暂无数据。")
            return [RouterReply(text="\n".join(lines))]
        metric_key = {
            "exp": "expPerHour",
            "fert": "normalFertilizerExpPerHour",
            "profit": "profitPerHour",
            "fert_profit": "fertProfitPerHour",
            "level": "requiredLevel",
        }[sort_by]
        for row in rows[:60]:
            seed_id = row.get("seedId")
            name = row.get("name") or f"seed-{seed_id}"
            metric = row.get(metric_key)
            lines.append(f"- {name}({seed_id}) => {metric_key}={metric}")
        if len(rows) > 60:
            lines.append(f"... 共 {len(rows)} 条，仅展示前 60 条。")
        return [RouterReply(text="\n".join(lines))]

    async def _cmd_automation(self, user_id: str, args: list[str]) -> list[RouterReply]:
        if not args:
            return [RouterReply(text="用法: qfarm 自动化 查看 | 设置 <key> <on|off> | 施肥 <both|normal|organic|none>")]
        sub = self._token(args[0])
        account_id, _ = await self._require_bound_account(user_id)

        if sub in {"查看", "view"}:
            settings = await self.api.get_settings(account_id)
            auto = settings.get("automation", {}) if isinstance(settings, dict) else {}
            lines = ["【自动化配置】"]
            if not auto:
                lines.append("暂无自动化配置。")
            else:
                for key in sorted(auto.keys()):
                    lines.append(f"- {key}: {auto.get(key)}")
            return [RouterReply(text="\n".join(lines))]

        if sub in {"设置", "set"}:
            if len(args) < 3:
                return [RouterReply(text="用法: qfarm 自动化 设置 <key> <on|off>")]
            key = self._token(args[1])
            value = self._parse_bool(args[2])
            if key not in self.AUTOMATION_KEYS or value is None:
                return [RouterReply(text="自动化设置参数非法。")]
            await self.api.set_automation(account_id, key, value)
            return [RouterReply(text=f"自动化已更新: {key}={value}")]

        if sub in {"施肥", "fertilizer"}:
            if len(args) < 2:
                return [RouterReply(text="用法: qfarm 自动化 施肥 <both|normal|organic|none>")]
            mode = self._token(args[1])
            if mode not in self.FERTILIZER_MODES:
                return [RouterReply(text="施肥模式非法，仅支持 both|normal|organic|none")]

            used_fallback = False
            try:
                await self.api.save_settings(account_id, {"automation": {"fertilizer": mode}})
                settings = await self.api.get_settings(account_id)
                automation = settings.get("automation", {}) if isinstance(settings, dict) else {}
                current_mode = self._token(str(automation.get("fertilizer") or ""))
                if current_mode != mode:
                    await self.api.set_automation(account_id, "fertilizer", mode)
                    used_fallback = True
            except Exception as e:
                self._log_warning(f"施肥模式写入 settings/save 失败，回退 automation: {e}")
                await self.api.set_automation(account_id, "fertilizer", mode)
                used_fallback = True

            if used_fallback:
                return [RouterReply(text=f"施肥模式已更新: {mode}（兼容回退已启用）")]
            return [RouterReply(text=f"施肥模式已更新: {mode}")]

        return [RouterReply(text="未知自动化子命令。")]

    async def _cmd_settings(self, user_id: str, args: list[str]) -> list[RouterReply]:
        if not args:
            return [RouterReply(text="用法: qfarm 设置 策略|种子|间隔|静默 ...")]
        sub = self._token(args[0])
        account_id, _ = await self._require_bound_account(user_id)

        if sub in {"策略", "strategy"}:
            if len(args) < 2:
                return [RouterReply(text="用法: qfarm 设置 策略 <preferred|level|max_exp|max_fert_exp|max_profit|max_fert_profit>")]
            strategy = self._token(args[1])
            if strategy not in self.STRATEGIES:
                return [RouterReply(text="策略非法。")]
            await self.api.save_settings(account_id, {"strategy": strategy})
            return [RouterReply(text=f"策略已更新: {strategy}")]

        if sub in {"种子", "seed"}:
            if len(args) < 2 or not str(args[1]).isdigit():
                return [RouterReply(text="用法: qfarm 设置 种子 <seedId>")]
            seed_id = int(args[1])
            if seed_id < 0:
                return [RouterReply(text="seedId 必须 >= 0。")]
            await self.api.save_settings(account_id, {"seedId": seed_id})
            return [RouterReply(text=f"偏好种子已更新: {seed_id}")]

        if sub in {"间隔", "interval"}:
            if len(args) < 4:
                return [RouterReply(text="用法: qfarm 设置 间隔 农场 <minSec> <maxSec> | 间隔 好友 <minSec> <maxSec>")]
            target = self._token(args[1])
            if not args[2].isdigit() or not args[3].isdigit():
                return [RouterReply(text="间隔参数必须是正整数秒。")]
            min_sec = max(1, int(args[2]))
            max_sec = max(1, int(args[3]))
            if min_sec > max_sec:
                return [RouterReply(text="间隔参数非法：minSec 不能大于 maxSec。")]

            settings = await self.api.get_settings(account_id)
            intervals = settings.get("intervals", {}) if isinstance(settings, dict) else {}
            if not isinstance(intervals, dict):
                intervals = {}

            if target in {"农场", "farm"}:
                intervals["farmMin"] = min_sec
                intervals["farmMax"] = max_sec
                intervals["farm"] = min_sec
            elif target in {"好友", "friend"}:
                intervals["friendMin"] = min_sec
                intervals["friendMax"] = max_sec
                intervals["friend"] = min_sec
            else:
                return [RouterReply(text="用法: qfarm 设置 间隔 农场 <minSec> <maxSec> | 间隔 好友 <minSec> <maxSec>")]

            await self.api.save_settings(account_id, {"intervals": intervals})
            return [RouterReply(text=f"间隔已更新: {target} {min_sec}-{max_sec}s")]

        if sub in {"静默", "quiet"}:
            if len(args) < 4:
                return [RouterReply(text="用法: qfarm 设置 静默 <on|off> <HH:MM> <HH:MM>")]
            enabled = self._parse_bool(args[1])
            start = args[2]
            end = args[3]
            if enabled is None:
                return [RouterReply(text="静默开关非法，请使用 on/off。")]
            if not self._is_valid_time(start) or not self._is_valid_time(end):
                return [RouterReply(text="时间格式非法，请使用 HH:MM（24小时制）。")]
            await self.api.save_settings(
                account_id,
                {"friendQuietHours": {"enabled": enabled, "start": start, "end": end}},
            )
            return [RouterReply(text=f"好友静默已更新: enabled={enabled}, {start}-{end}")]

        return [RouterReply(text="未知设置子命令。")]

    async def _cmd_theme(self, args: list[str]) -> list[RouterReply]:
        if not args:
            return [RouterReply(text="用法: qfarm 主题 <dark|light>")]
        theme = self._token(args[0])
        if theme not in {"dark", "light"}:
            return [RouterReply(text="主题仅支持 dark|light")]
        await self.api.set_theme(theme)
        try:
            self.state_store.set_render_theme(theme)
        except Exception as e:
            self._log_warning(f"写入渲染主题失败(忽略): {e}")
        return [RouterReply(text=f"面板主题已更新: {theme}")]

    async def _cmd_logs(self, user_id: str, args: list[str]) -> list[RouterReply]:
        account_id, _ = await self._require_bound_account(user_id)
        limit, options = parse_key_value_args(args)
        safe_limit = 50 if limit is None else min(300, max(1, int(limit)))
        logs = await self.api.get_logs(
            account_id,
            limit=safe_limit,
            module=options.get("module", ""),
            event=options.get("event", ""),
            keyword=options.get("keyword", ""),
            isWarn=options.get("isWarn", ""),
            timeFrom=options.get("timeFrom", ""),
            timeTo=options.get("timeTo", ""),
        )
        lines = [f"【日志】数量: {len(logs)} (limit={safe_limit})"]
        if not logs:
            lines.append("无匹配日志。")
            return [RouterReply(text="\n".join(lines))]
        for entry in logs[:safe_limit]:
            time_text = str(entry.get("time") or "")
            msg = str(entry.get("msg") or "")
            tag = str(entry.get("tag") or "")
            warn = bool(entry.get("isWarn"))
            level = "WARN" if warn else "INFO"
            lines.append(f"- [{level}] {time_text} [{tag}] {msg}")
        return [RouterReply(text="\n".join(lines))]

    async def _cmd_account_logs(self, args: list[str]) -> list[RouterReply]:
        limit = 50
        if args and str(args[0]).isdigit():
            limit = min(300, max(1, int(args[0])))
        logs = await self.api.get_account_logs(limit=limit)
        lines = [f"【账号日志】数量: {len(logs)} (limit={limit})"]
        if not logs:
            lines.append("暂无账号日志。")
            return [RouterReply(text="\n".join(lines))]
        for row in logs:
            time_text = str(row.get("time") or "")
            action = str(row.get("action") or "")
            msg = str(row.get("msg") or "")
            aid = str(row.get("accountId") or "")
            name = str(row.get("accountName") or "")
            lines.append(f"- {time_text} [{action}] account={aid}/{name} {msg}")
        return [RouterReply(text="\n".join(lines))]

    async def _cmd_debug(self, user_id: str, args: list[str]) -> list[RouterReply]:
        if not args:
            return [RouterReply(text="用法: qfarm 调试 出售")]
        sub = self._token(args[0])
        if sub not in {"出售", "sell"}:
            return [RouterReply(text="调试子命令仅支持: 出售")]
        account_id, _ = await self._require_bound_account(user_id)
        await self.api.debug_sell(account_id)
        return [RouterReply(text="调试出售已触发。")]

    async def _cmd_whitelist(self, args: list[str]) -> list[RouterReply]:
        if len(args) < 2:
            return [RouterReply(text="用法: qfarm 白名单 用户|群 列表|添加|删除 <id>")]

        target = self._token(args[0])
        action = self._token(args[1])
        is_user = target in {"用户", "user"}
        is_group = target in {"群", "group"}
        if not is_user and not is_group:
            return [RouterReply(text="白名单目标仅支持: 用户|群")]

        if action in {"列表", "list"}:
            items = self.state_store.list_whitelist_users() if is_user else self.state_store.list_whitelist_groups()
            title = "用户白名单" if is_user else "群白名单"
            lines = [f"【{title}】数量: {len(items)}"]
            if not items:
                lines.append("空")
            else:
                for value in items:
                    lines.append(f"- {value}")
            return [RouterReply(text="\n".join(lines))]

        if len(args) < 3:
            return [RouterReply(text="请提供要操作的 ID。")]
        target_id = str(args[2]).strip()
        if not target_id:
            return [RouterReply(text="ID 不能为空。")]

        if action in {"添加", "add"}:
            changed = self.state_store.add_whitelist_user(target_id) if is_user else self.state_store.add_whitelist_group(target_id)
            return [RouterReply(text=f"{'已添加' if changed else '已存在'}: {target_id}")]
        if action in {"删除", "移除", "del", "remove"}:
            changed = self.state_store.remove_whitelist_user(target_id) if is_user else self.state_store.remove_whitelist_group(target_id)
            return [RouterReply(text=f"{'已删除' if changed else '不存在'}: {target_id}")]

        return [RouterReply(text="白名单动作仅支持 列表|添加|删除")]

    def _help_text(self) -> str:
        return (
            "qfarm 命令总览\n"
            "1) qfarm 帮助\n"
            "2) qfarm 服务 状态|启动|停止|重启 (超管)\n"
            "3) qfarm 账号 查看\n"
            "4) qfarm 账号 绑定 code <code> [备注名]\n"
            "5) qfarm 账号 绑定扫码\n"
            "6) qfarm 账号 取消扫码\n"
            "7) qfarm 账号 解绑\n"
            "8) qfarm 账号 启动\n"
            "9) qfarm 账号 停止\n"
            "10) qfarm 账号 重连 [code]\n"
            "11) qfarm 状态\n"
            "12) qfarm 农田 查看\n"
            "13) qfarm 农田 操作 all|harvest|clear|plant|upgrade\n"
            "14) qfarm 好友 列表\n"
            "15) qfarm 好友 农田 <gid>\n"
            "16) qfarm 好友 操作 <gid> steal|water|weed|bug|bad\n"
            "17) qfarm 种子 列表\n"
            "18) qfarm 背包 查看\n"
            "19) qfarm 分析 [exp|fert|profit|fert_profit|level]\n"
            "20) qfarm 自动化 查看\n"
            "21) qfarm 自动化 设置 <key> <on|off>\n"
            "22) qfarm 自动化 施肥 <both|normal|organic|none>\n"
            "23) qfarm 设置 策略 <preferred|level|max_exp|max_fert_exp|max_profit|max_fert_profit>\n"
            "24) qfarm 设置 种子 <seedId>\n"
            "25) qfarm 设置 间隔 农场 <minSec> <maxSec>\n"
            "26) qfarm 设置 间隔 好友 <minSec> <maxSec>\n"
            "27) qfarm 设置 静默 <on|off> <HH:MM> <HH:MM>\n"
            "28) qfarm 主题 <dark|light>\n"
            "29) qfarm 日志 [limit] [module=...] [event=...] [keyword=...] [isWarn=0|1]\n"
            "30) qfarm 账号日志 [limit]\n"
            "31) qfarm 调试 出售 (超管)\n"
            "32) qfarm 白名单 用户 列表|添加|删除 <uid> (超管)\n"
            "33) qfarm 白名单 群 列表|添加|删除 <gid> (超管)\n"
            "\n同样支持中文别名命令: 农场 ..."
        )

    async def _start_qr_bind(self, event: Any, user_id: str) -> list[RouterReply]:
        if user_id in self._qr_tasks:
            return [RouterReply(text="已有扫码绑定任务进行中，请先取消或等待完成。")]
        data = await self.api.qr_create()
        code = str(data.get("code") or "")
        qr_url = str(data.get("qrcode") or "")
        login_url = str(data.get("url") or "")
        if not code:
            raise QFarmApiError("扫码绑定失败：未获取到 code。")
        umo = getattr(event, "unified_msg_origin", None)
        task = asyncio.create_task(self._poll_qr_login(user_id=user_id, code=code, umo=umo))
        self._qr_tasks[user_id] = task
        task.add_done_callback(lambda _: self._qr_tasks.pop(user_id, None))
        lines = ["已创建扫码登录任务，请在 120 秒内完成扫码。", f"轮询code: {code}"]
        if login_url:
            lines.append(f"登录链接: {login_url}")
        replies = [RouterReply(text="\n".join(lines))]
        if qr_url:
            replies.append(RouterReply(image_url=qr_url))
        return replies

    async def _poll_qr_login(self, user_id: str, code: str, umo: Any) -> None:
        try:
            for _ in range(120):
                await asyncio.sleep(1)
                data = await self.api.qr_check(code)
                status = str(data.get("status") or "")
                if status == "Wait":
                    continue
                if status == "OK":
                    auth_code = str(data.get("code") or "")
                    if not auth_code:
                        await self._notify_active(umo, "扫码成功但未获取到授权 code，请重试。")
                        return
                    uin = str(data.get("uin") or "")
                    account_name = uin or f"用户{user_id}"
                    account = await self._bind_account_with_code(
                        user_id,
                        code=auth_code,
                        account_name=account_name,
                        extra_fields={
                            "uin": uin,
                            "qq": uin,
                            "avatar": str(data.get("avatar") or ""),
                        },
                    )
                    account_id = str(account.get("id") or "")
                    status_data = await self.api.get_status(account_id)
                    runtime_state = str(status_data.get("runtimeState") or "stopped")
                    last_error = str(status_data.get("lastStartError") or "")
                    if runtime_state == "running":
                        await self._notify_active(
                            umo,
                            f"扫码绑定并启动成功: 账号ID={account_id} 名称={account.get('name') or '-'}",
                        )
                    else:
                        await self._notify_active(
                            umo,
                            (
                                f"扫码绑定成功，但自动启动失败: {last_error or runtime_state}\n"
                                "可手动执行: qfarm 账号 启动"
                            ),
                        )
                    return
                if status == "Used":
                    await self._notify_active(umo, "二维码已失效，请重新发起 `qfarm 账号 绑定扫码`。")
                    return
                if status == "Error":
                    await self._notify_active(umo, f"扫码登录失败: {data.get('error') or '未知错误'}")
                    return
            await self._notify_active(umo, "扫码登录超时（120秒）。")
        except asyncio.CancelledError:
            await self._notify_active(umo, "扫码绑定任务已取消。")
        except Exception as e:
            self._log_warning(f"扫码绑定轮询异常: {e}")
            await self._notify_active(umo, f"扫码绑定异常: {e}")

    async def _bind_account_with_code(
        self,
        user_id: str,
        code: str,
        account_name: str = "",
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        code = str(code or "").strip()
        if not code:
            raise QFarmApiError("code 不能为空。")

        existing_id = self.state_store.get_bound_account(user_id)
        before_accounts = await self.api.get_accounts()
        before_list = before_accounts.get("accounts", []) if isinstance(before_accounts, dict) else []
        before_map = {str(acc.get("id")): acc for acc in before_list if isinstance(acc, dict)}

        account: dict[str, Any] | None = None
        if existing_id:
            account = before_map.get(str(existing_id))
            if not account:
                self.state_store.unbind_account(user_id)
                existing_id = None

        if existing_id:
            payload = {
                "id": existing_id,
                "name": account_name or (account.get("name") if account else "") or f"用户{user_id}",
                "platform": (account.get("platform") if account else "") or "qq",
                "code": code,
                "uin": (account.get("uin") if account else "") or "",
                "qq": (account.get("qq") if account else "") or "",
                "avatar": (account.get("avatar") if account else "") or "",
            }
            if extra_fields:
                payload.update({k: v for k, v in extra_fields.items() if v is not None and v != ""})
            await self.api.upsert_account(payload)
            after_account = await self._fetch_account_by_id(existing_id)
            if not after_account:
                raise QFarmApiError("更新账号后未找到目标账号，请检查服务状态。")
            self.state_store.bind_account(user_id, existing_id, after_account.get("name") or "")
            return after_account

        payload = {"name": account_name or f"用户{user_id}", "code": code, "platform": "qq"}
        if extra_fields:
            payload.update({k: v for k, v in extra_fields.items() if v is not None and v != ""})
        await self.api.upsert_account(payload)

        after_accounts = await self.api.get_accounts()
        after_list = after_accounts.get("accounts", []) if isinstance(after_accounts, dict) else []
        before_ids = set(before_map.keys())
        candidates = [acc for acc in after_list if isinstance(acc, dict) and str(acc.get("id")) not in before_ids]
        if not candidates:
            candidates = [acc for acc in after_list if isinstance(acc, dict) and str(acc.get("code") or "") == code]
        if not candidates:
            raise QFarmApiError("创建账号后未能识别新账号ID。")

        candidates.sort(key=lambda x: int(x.get("updatedAt") or x.get("createdAt") or 0), reverse=True)
        created = candidates[0]
        account_id = str(created.get("id") or "").strip()
        if not account_id:
            raise QFarmApiError("创建账号后缺少账号ID。")

        self.state_store.bind_account(user_id, account_id, created.get("name") or "")
        return created

    async def _require_bound_account(self, user_id: str) -> tuple[str, dict[str, Any]]:
        account_id = self.state_store.get_bound_account(user_id)
        if not account_id:
            raise QFarmApiError("当前用户未绑定账号，请先执行 `qfarm 账号 绑定 code <code>`。")
        account = await self._fetch_account_by_id(account_id)
        if not account:
            self.state_store.unbind_account(user_id)
            raise QFarmApiError("绑定账号不存在或已被删除，已自动解绑，请重新绑定。")
        return account_id, account

    async def _fetch_account_by_id(self, account_id: str | int) -> dict[str, Any] | None:
        data = await self.api.get_accounts()
        accounts = data.get("accounts", []) if isinstance(data, dict) else []
        for account in accounts:
            if str(account.get("id")) == str(account_id):
                return account
        return None

    async def _wait_for_api_ready(self, timeout_sec: int = 20) -> tuple[bool, str]:
        deadline = asyncio.get_event_loop().time() + max(1, timeout_sec)
        last_error = ""
        while asyncio.get_event_loop().time() < deadline:
            try:
                await self.api.ping()
                return True, ""
            except Exception as e:
                last_error = str(e)
                await asyncio.sleep(1)
        return False, last_error or "未知错误"

    async def _notify_active(self, umo: Any, text: str) -> None:
        if not self.send_active_message or umo is None:
            return
        try:
            await self.send_active_message(umo, text)
        except Exception as e:
            self._log_warning(f"主动消息发送失败: {e}")

    def _check_access(self, user_id: str, group_id: str | None) -> tuple[bool, str]:
        if self.is_super_admin(user_id):
            return True, ""
        if not self.state_store.is_user_allowed(user_id):
            return False, "权限不足：你不在用户白名单中。"
        if group_id and not self.state_store.is_group_allowed(group_id):
            return False, "权限不足：当前群不在群白名单中。"
        return True, ""

    def _is_super_admin_cmd(self, top: str) -> bool:
        return top in {"服务", "service", "白名单", "whitelist", "调试", "debug"}

    def _is_write_command(self, tokens: list[str]) -> bool:
        cmd = self._token(tokens[0]) if tokens else ""
        args = [self._token(item) for item in tokens[1:]]
        if cmd in {"服务", "service"}:
            return not args or args[0] not in {"状态", "status"}
        if cmd in {"账号", "account"}:
            if not args:
                return False
            return args[0] not in {"查看", "view"}
        if cmd in {
            "状态",
            "status",
            "种子",
            "seed",
            "seeds",
            "背包",
            "bag",
            "分析",
            "analytics",
            "analysis",
            "日志",
            "log",
            "logs",
            "账号日志",
            "accountlogs",
            "account-logs",
        }:
            return False
        if cmd in {"农田", "farm"}:
            return len(args) >= 1 and args[0] in {"操作", "op", "operate"}
        if cmd in {"好友", "friend"}:
            return len(args) >= 1 and args[0] in {"操作", "op", "operate"}
        if cmd in {"自动化", "automation", "auto", "设置", "setting", "settings", "主题", "theme", "白名单", "whitelist", "调试", "debug"}:
            return True
        return False

    def _mark_render_candidates(self, replies: list[RouterReply]) -> list[RouterReply]:
        for reply in replies:
            if reply.image_url:
                continue
            if not reply.text:
                continue
            if self._is_normal_reply_text(reply.text):
                reply.prefer_image = True
        return replies

    def _is_normal_reply_text(self, text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return False
        bad_prefixes = (
            "用法:",
            "权限不足",
            "操作失败:",
            "命令执行异常:",
            "未知",
            "无法识别",
        )
        if any(content.startswith(prefix) for prefix in bad_prefixes):
            return False
        bad_keywords = (
            "不能为空",
            "非法",
            "拒绝执行",
            "过于频繁",
            "失败",
        )
        if any(word in content for word in bad_keywords):
            return False
        return True

    def _get_user_id(self, event: Any) -> str:
        try:
            user_id = getattr(event, "get_sender_id")()
            return str(user_id or "").strip()
        except Exception:
            pass
        try:
            message_obj = getattr(event, "message_obj", None)
            return str(getattr(message_obj, "user_id", "") or "").strip()
        except Exception:
            return ""

    def _get_group_id(self, event: Any) -> str | None:
        try:
            group_id = getattr(event, "get_group_id")()
            text = str(group_id or "").strip()
            if text:
                return text
        except Exception:
            pass
        try:
            message_obj = getattr(event, "message_obj", None)
            group_id = getattr(message_obj, "group_id", None)
            text = str(group_id or "").strip()
            return text or None
        except Exception:
            return None

    def _format_status(self, data: dict[str, Any]) -> list[str]:
        status = data.get("status", {}) if isinstance(data, dict) else {}
        conn = data.get("connection", {}) if isinstance(data, dict) else {}
        ops = data.get("operations", {}) if isinstance(data, dict) else {}
        exp_progress = data.get("expProgress", {}) if isinstance(data, dict) else {}
        next_checks = data.get("nextChecks", {}) if isinstance(data, dict) else {}
        runtime_state = data.get("runtimeState", "stopped") if isinstance(data, dict) else "stopped"
        retry_count = data.get("startRetryCount", 0) if isinstance(data, dict) else 0
        last_error = str(data.get("lastStartError") or "") if isinstance(data, dict) else ""

        lines = [
            "【农场状态】",
            f"连接: {'在线' if conn.get('connected') else '离线'}",
            f"运行态: {runtime_state}",
            f"启动重试次数: {retry_count}",
            f"昵称: {status.get('name') or '-'}",
            f"等级: Lv{status.get('level', 0)}",
            f"金币: {status.get('gold', 0)}",
            f"经验: {status.get('exp', 0)}",
            f"点券: {status.get('coupon', 0)}",
            (
                "会话收益: "
                f"经验 {data.get('sessionExpGained', 0)} / "
                f"金币 {data.get('sessionGoldGained', 0)} / "
                f"点券 {data.get('sessionCouponGained', 0)}"
            ),
            f"下次农田: {next_checks.get('farmRemainSec', '--')}s",
            f"下次好友巡查: {next_checks.get('friendRemainSec', '--')}s",
            f"经验进度: {exp_progress.get('current', 0)}/{exp_progress.get('needed', 0)}",
        ]
        if last_error:
            lines.append(f"最近启动错误: {last_error}")

        op_parts = []
        for key in (
            "harvest",
            "water",
            "weed",
            "bug",
            "plant",
            "steal",
            "helpWater",
            "helpWeed",
            "helpBug",
            "taskClaim",
            "sell",
            "upgrade",
        ):
            if key in ops:
                op_parts.append(f"{key}:{ops.get(key)}")
        if op_parts:
            lines.append("操作计数: " + " ".join(op_parts))
        return lines

    def _format_lands(self, data: dict[str, Any]) -> list[str]:
        lands = data.get("lands", []) if isinstance(data, dict) else []
        summary = data.get("summary", {}) if isinstance(data, dict) else {}
        lines = [
            "【农田详情】",
            (
                f"收获:{summary.get('harvestable', 0)} "
                f"长成:{summary.get('growing', 0)} "
                f"空地:{summary.get('empty', 0)} "
                f"枯萎:{summary.get('dead', 0)} "
                f"水:{summary.get('needWater', 0)} "
                f"草:{summary.get('needWeed', 0)} "
                f"虫:{summary.get('needBug', 0)}"
            ),
        ]
        if not lands:
            lines.append("暂无土地数据。")
            return lines

        for land in lands[:80]:
            land_id = land.get("id")
            status = land.get("status") or "-"
            plant = land.get("plantName") or "-"
            phase = land.get("phaseName") or "-"
            level = land.get("level", 0)
            needs = []
            if land.get("needWater"):
                needs.append("水")
            if land.get("needWeed"):
                needs.append("草")
            if land.get("needBug"):
                needs.append("虫")
            needs_text = f" 需:{'/'.join(needs)}" if needs else ""
            mature = land.get("matureInSec")
            mature_text = f" 成熟剩余:{mature}s" if isinstance(mature, int) and mature > 0 else ""
            lines.append(f"- #{land_id} [{status}] Lv{level} {plant} / {phase}{needs_text}{mature_text}")

        if len(lands) > 80:
            lines.append(f"... 共 {len(lands)} 块，仅展示前 80 块。")
        return lines

    def _format_friends(self, friends: list[dict[str, Any]]) -> list[str]:
        lines = [f"【好友列表】总数: {len(friends)}"]
        if not friends:
            lines.append("暂无好友或接口无数据。")
            return lines

        for friend in friends[:80]:
            gid = friend.get("gid")
            name = friend.get("name") or f"GID:{gid}"
            plant = friend.get("plant") or {}
            preview = (
                f"偷{plant.get('stealNum', 0)} "
                f"水{plant.get('dryNum', 0)} "
                f"草{plant.get('weedNum', 0)} "
                f"虫{plant.get('insectNum', 0)}"
            )
            lines.append(f"- {name} ({gid}) => {preview}")

        if len(friends) > 80:
            lines.append(f"... 共 {len(friends)} 人，仅展示前 80 人。")
        return lines

    def _format_friend_lands(self, gid: str, data: dict[str, Any]) -> list[str]:
        lands = data.get("lands", []) if isinstance(data, dict) else []
        summary = data.get("summary", {}) if isinstance(data, dict) else {}
        stealable = summary.get("stealable", 0)
        need_water = summary.get("needWater", 0)
        need_weed = summary.get("needWeed", 0)
        need_bug = summary.get("needBug", 0)
        if isinstance(stealable, list):
            stealable = len(stealable)
        if isinstance(need_water, list):
            need_water = len(need_water)
        if isinstance(need_weed, list):
            need_weed = len(need_weed)
        if isinstance(need_bug, list):
            need_bug = len(need_bug)

        lines = [
            f"【好友农田】gid={gid}",
            f"可偷:{stealable} 可浇水:{need_water} 可除草:{need_weed} 可除虫:{need_bug}",
        ]
        if not lands:
            lines.append("无土地明细。")
            return lines

        for land in lands[:80]:
            land_id = land.get("id")
            status = land.get("status") or "-"
            plant = land.get("plantName") or "-"
            phase = land.get("phaseName") or "-"
            needs = []
            if land.get("needWater"):
                needs.append("水")
            if land.get("needWeed"):
                needs.append("草")
            if land.get("needBug"):
                needs.append("虫")
            needs_text = f" 需:{'/'.join(needs)}" if needs else ""
            lines.append(f"- #{land_id} [{status}] {plant} / {phase}{needs_text}")

        if len(lands) > 80:
            lines.append(f"... 共 {len(lands)} 块，仅展示前 80 块。")
        return lines

    def _parse_bool(self, value: str) -> bool | None:
        text = self._token(value)
        if text in {"1", "true", "on", "yes", "y", "开", "开启", "是"}:
            return True
        if text in {"0", "false", "off", "no", "n", "关", "关闭", "否"}:
            return False
        return None

    def _is_valid_time(self, value: str) -> bool:
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(value).strip())
        if not m:
            return False
        hh = int(m.group(1))
        mm = int(m.group(2))
        return 0 <= hh <= 23 and 0 <= mm <= 59

    def _token(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if re.fullmatch(r"[A-Za-z0-9_\-]+", text):
            return text.lower()
        return text

    def _log_warning(self, message: str) -> None:
        if self.logger and hasattr(self.logger, "warning"):
            self.logger.warning(message)
        elif self.logger and hasattr(self.logger, "warn"):
            self.logger.warn(message)
        else:
            print(message)










