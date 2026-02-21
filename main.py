from __future__ import annotations

import asyncio
from pathlib import Path
import re
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .services.api_client import QFarmApiClient
from .services.command_router import QFarmCommandRouter
from .services.image_renderer import QFarmImageRenderer
from .services.process_manager import NodeProcessManager
from .services.rate_limiter import RateLimiter
from .services.render_payload_builder import build_qfarm_payload_pages, should_render_qfarm_image
from .services.state_store import QFarmStateStore


@register(
    "astrbot_plugin_qfarm",
    "riddle",
    "AstrBot + NapCat 的 QQ 农场全量命令插件（纯 Python 实现）",
    "2.2.1",
    "https://github.com/R1ddle1337/astrbot_plugin_qfarm",
)
class QFarmPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}

        self.plugin_root = Path(__file__).resolve().parent
        self.plugin_data_dir = self._resolve_plugin_data_dir()
        self.state_store: QFarmStateStore | None = None
        self.process_manager: NodeProcessManager | None = None
        self.api_client: QFarmApiClient | None = None
        self.rate_limiter: RateLimiter | None = None
        self.router: QFarmCommandRouter | None = None
        self.image_renderer: QFarmImageRenderer | None = None
        self._super_admin_ids = self._load_super_admin_ids()

    async def initialize(self) -> None:
        self._super_admin_ids = self._load_super_admin_ids()
        gateway_ws_url = self._cfg_str("gateway_ws_url", "wss://gate-obt.nqf.qq.com/prod/ws")
        client_version = self._cfg_str("client_version", "1.6.0.5_20251224")
        platform = self._cfg_str("platform", "qq")
        heartbeat_interval_sec = self._cfg_int("heartbeat_interval_sec", 25)
        rpc_timeout_sec = self._cfg_int("rpc_timeout_sec", 10)
        enable_image_render = self._cfg_bool("enable_image_render", True)
        render_service_url = self._cfg_str("render_service_url", "http://172.17.0.1:51234")
        render_timeout_sec = self._cfg_int("render_timeout_sec", 30)
        render_healthcheck_sec = self._cfg_int("render_healthcheck_sec", 3)
        managed_mode = self._cfg_bool("managed_mode", True)
        start_retry_max_attempts = self._cfg_int("start_retry_max_attempts", 3)
        start_retry_base_delay_sec = self._cfg_float("start_retry_base_delay_sec", 1.0)
        start_retry_max_delay_sec = self._cfg_float("start_retry_max_delay_sec", 8.0)
        auto_start_concurrency = self._cfg_int("auto_start_concurrency", 5)
        persist_runtime_logs = self._cfg_bool("persist_runtime_logs", True)
        runtime_log_max_entries = self._cfg_int("runtime_log_max_entries", 3000)
        per_user_inflight_limit = self._cfg_int("per_user_inflight_limit", 1)

        self.state_store = QFarmStateStore(
            data_dir=self.plugin_data_dir,
            static_allowed_users=self._cfg_list("allowed_user_ids"),
            static_allowed_groups=self._cfg_list("allowed_group_ids"),
        )

        self.process_manager = NodeProcessManager(
            plugin_root=self.plugin_root,
            data_dir=self.plugin_data_dir,
            gateway_ws_url=gateway_ws_url,
            client_version=client_version,
            platform=platform,
            heartbeat_interval_sec=heartbeat_interval_sec,
            rpc_timeout_sec=rpc_timeout_sec,
            start_retry_max_attempts=start_retry_max_attempts,
            start_retry_base_delay_sec=start_retry_base_delay_sec,
            start_retry_max_delay_sec=start_retry_max_delay_sec,
            auto_start_concurrency=auto_start_concurrency,
            persist_runtime_logs=persist_runtime_logs,
            runtime_log_max_entries=runtime_log_max_entries,
            managed_mode=managed_mode,
            logger=logger,
        )
        self.api_client = QFarmApiClient(self.process_manager.backend, logger=logger)
        self.rate_limiter = RateLimiter(
            read_cooldown_sec=self._cfg_float("rate_limit_read_sec", 1.0),
            write_cooldown_sec=self._cfg_float("rate_limit_write_sec", 2.0),
            global_concurrency=self._cfg_int("global_concurrency", 20),
            account_write_serialized=self._cfg_bool("account_write_serialized", True),
        )
        self.router = QFarmCommandRouter(
            api_client=self.api_client,
            state_store=self.state_store,
            rate_limiter=self.rate_limiter,
            process_manager=self.process_manager,
            is_super_admin=self._is_super_admin,
            send_active_message=self._send_active_message,
            logger=logger,
            per_user_inflight_limit=per_user_inflight_limit,
        )

        if enable_image_render:
            self.image_renderer = QFarmImageRenderer(
                service_url=render_service_url,
                cache_dir=self.plugin_data_dir / "render_cache",
                timeout_sec=render_timeout_sec,
                logger=logger,
            )
            if await self.image_renderer.check_health(timeout_sec=render_healthcheck_sec):
                logger.info(f"[qfarm] 图片渲染服务可用: {render_service_url}")
            else:
                logger.warning(f"[qfarm] 图片渲染服务不可用，将自动回退文本: {render_service_url}")
            self.image_renderer.cleanup_cache()

        if managed_mode:
            await self.process_manager.start()
        await self._warmup_api()
        logger.info("[qfarm] 插件初始化完成")

    async def terminate(self) -> None:
        if self.router:
            await self.router.shutdown()
        if self.image_renderer:
            self.image_renderer.cleanup_cache()
            await self.image_renderer.close()
        if self.process_manager:
            await self.process_manager.stop()
        logger.info("[qfarm] 插件已卸载")

    @filter.command("qfarm", alias={"农场", "qfram"})
    async def qfarm_entry(self, *args: Any, **kwargs: Any):
        current_event = self._resolve_command_event(args, kwargs)
        if current_event is None:
            logger.error(
                f"[qfarm] 无法解析命令事件对象，args={[type(x) for x in args]}, kwargs={list(kwargs.keys())}"
            )
            return
        if self.router is None:
            plain = self._build_plain_result(current_event, "qfarm 插件尚未初始化。")
            if plain is not None:
                yield plain
            return
        replies = await self.router.handle(current_event)
        for reply in replies:
            rendered = False
            if (
                reply.text
                and reply.prefer_image
                and self.image_renderer is not None
                and self.state_store is not None
                and should_render_qfarm_image(reply.text)
            ):
                payloads = build_qfarm_payload_pages(reply.text, theme=self.state_store.get_render_theme("light"))
                images: list[str] = []
                for payload in payloads:
                    image_path = await self.image_renderer.render_qfarm(payload)
                    if not image_path:
                        images = []
                        break
                    images.append(image_path)
                if images:
                    rendered = True
                    for image_path in images:
                        image = self._build_image_result(current_event, image_path)
                        if image is not None:
                            yield image
            if reply.text and not rendered:
                plain = self._build_plain_result(current_event, reply.text)
                if plain is not None:
                    yield plain
            if reply.image_url:
                image = self._build_image_result(current_event, reply.image_url)
                if image is not None:
                    yield image

    async def _send_active_message(self, umo: Any, text: str) -> None:
        chain = MessageChain().message(text)
        await self.context.send_message(umo, chain)

    async def _warmup_api(self) -> None:
        if not self.api_client:
            return
        for _ in range(3):
            try:
                await self.api_client.ping()
                return
            except Exception:
                await asyncio.sleep(1)

    def _is_super_admin(self, user_id: str) -> bool:
        return str(user_id or "").strip() in self._super_admin_ids

    def _load_super_admin_ids(self) -> set[str]:
        values: list[str] = []
        for key in ("super_admin_ids", "admins_id", "admins", "admin_ids", "superusers"):
            values.extend(self._normalize_id_values(self._cfg_raw(key, [])))
        try:
            cfg = self.context.get_config() if hasattr(self.context, "get_config") else {}
        except Exception:
            cfg = {}
        if isinstance(cfg, dict):
            for key in ("admins_id", "admins", "admin_ids", "superusers"):
                values.extend(self._normalize_id_values(cfg.get(key, [])))
        return {item for item in values if item}

    def _resolve_plugin_data_dir(self) -> Path:
        try:
            path = Path(StarTools.get_data_dir("astrbot_plugin_qfarm"))
            path.mkdir(parents=True, exist_ok=True)
            return path
        except Exception:
            fallback = self.plugin_root / "data"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    def _cfg_str(self, key: str, default: str) -> str:
        try:
            value = self.config.get(key, default)  # type: ignore[attr-defined]
        except Exception:
            value = default
        return str(value if value is not None else default)

    def _cfg_bool(self, key: str, default: bool) -> bool:
        try:
            value = self.config.get(key, default)  # type: ignore[attr-defined]
        except Exception:
            value = default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "on", "yes", "y", "开", "开启"}:
            return True
        if text in {"0", "false", "off", "no", "n", "关", "关闭"}:
            return False
        return bool(default)

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            value = self.config.get(key, default)  # type: ignore[attr-defined]
            return int(value)
        except Exception:
            return int(default)

    def _cfg_float(self, key: str, default: float) -> float:
        try:
            value = self.config.get(key, default)  # type: ignore[attr-defined]
            return float(value)
        except Exception:
            return float(default)

    def _cfg_list(self, key: str) -> list[str]:
        try:
            value = self.config.get(key, [])  # type: ignore[attr-defined]
        except Exception:
            value = []
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            text = str(item).strip()
            if text:
                result.append(text)
        return result

    def _cfg_raw(self, key: str, default: Any) -> Any:
        try:
            return self.config.get(key, default)  # type: ignore[attr-defined]
        except Exception:
            return default

    def _normalize_id_values(self, raw: Any) -> list[str]:
        if isinstance(raw, list):
            result: list[str] = []
            for item in raw:
                text = str(item).strip()
                if text:
                    result.append(text)
            return result
        if raw is None:
            return []
        text = str(raw).strip()
        if not text:
            return []
        parts = re.split(r"[\s,;，；|]+", text)
        return [item for item in (part.strip() for part in parts) if item]

    def _resolve_command_event(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> AstrMessageEvent | None:
        candidates: list[Any] = []
        for key in ("event", "message_event", "msg_event"):
            if key in kwargs:
                candidates.append(kwargs.get(key))
        candidates.extend(args)
        for item in candidates:
            if item is None:
                continue
            if item is self:
                continue
            plain_fn = self._get_callable(item, "plain_result")
            if plain_fn is not None:
                return item
            if isinstance(item, (list, tuple)):
                for nested in item:
                    if nested is None or nested is self:
                        continue
                    if self._get_callable(nested, "plain_result") is not None:
                        return nested
        return None

    @staticmethod
    def _get_callable(target: Any, name: str) -> Any | None:
        try:
            fn = getattr(target, name, None)
        except Exception:
            return None
        return fn if callable(fn) else None

    def _build_plain_result(self, event: Any, text: str) -> Any | None:
        fn = self._get_callable(event, "plain_result")
        if fn is None:
            logger.error(f"[qfarm] 事件对象不支持 plain_result: {type(event)}")
            return None
        try:
            return fn(text)
        except Exception as e:
            logger.error(f"[qfarm] 调用 plain_result 失败: {e}")
            return None

    def _build_image_result(self, event: Any, image: str) -> Any | None:
        fn = self._get_callable(event, "image_result")
        if fn is None:
            logger.error(f"[qfarm] 事件对象不支持 image_result: {type(event)}")
            return None
        try:
            return fn(image)
        except Exception as e:
            logger.error(f"[qfarm] 调用 image_result 失败: {e}")
            return None
