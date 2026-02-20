from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .services.api_client import QFarmApiClient
from .services.command_router import QFarmCommandRouter
from .services.image_renderer import QFarmImageRenderer
from .services.process_manager import NodeProcessManager
from .services.rate_limiter import RateLimiter
from .services.render_payload_builder import build_qfarm_payload_pages
from .services.state_store import QFarmStateStore


@register(
    "astrbot_plugin_qfarm",
    "riddle",
    "QQ农场插件（AstrBot + NapCat，命令全量化）",
    "1.0.0",
    "https://github.com/R1ddle1337/astrbot_plugin_qfarm",
)
class QFarmPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}

        self.plugin_root = Path(__file__).resolve().parent
        self.plugin_data_dir = self._resolve_plugin_data_dir()

        self.state_store: QFarmStateStore | None = None
        self.api_client: QFarmApiClient | None = None
        self.process_manager: NodeProcessManager | None = None
        self.rate_limiter: RateLimiter | None = None
        self.router: QFarmCommandRouter | None = None
        self.image_renderer: QFarmImageRenderer | None = None

        self._super_admin_ids = self._load_super_admin_ids()

    async def initialize(self) -> None:
        managed_mode = self._cfg_bool("managed_mode", True)
        node_command = self._cfg_str("node_command", "node")
        service_host = self._cfg_str("service_host", "127.0.0.1")
        service_port = self._cfg_int("service_port", 3000)
        service_bind_host = self._cfg_str("service_bind_host", "127.0.0.1")
        disable_webui = self._cfg_bool("disable_webui", True)
        timeout_sec = self._cfg_int("request_timeout_sec", 15)
        enable_image_render = self._cfg_bool("enable_image_render", True)
        render_service_url = self._cfg_str("render_service_url", "http://172.17.0.1:51234")
        render_timeout_sec = self._cfg_int("render_timeout_sec", 30)
        render_healthcheck_sec = self._cfg_int("render_healthcheck_sec", 3)

        static_users = self._cfg_list("allowed_user_ids")
        static_groups = self._cfg_list("allowed_group_ids")
        self.state_store = QFarmStateStore(
            data_dir=self.plugin_data_dir,
            static_allowed_users=static_users,
            static_allowed_groups=static_groups,
        )

        service_admin_password = self.state_store.get_service_admin_password(
            self._cfg_str("service_admin_password", "")
        )
        self.process_manager = NodeProcessManager(
            plugin_root=self.plugin_root,
            node_command=node_command,
            service_port=service_port,
            service_bind_host=service_bind_host,
            admin_password=service_admin_password,
            disable_webui=disable_webui,
            managed_mode=managed_mode,
            logger=logger,
        )
        self.api_client = QFarmApiClient(
            host=service_host,
            port=service_port,
            admin_password=service_admin_password,
            timeout_sec=timeout_sec,
            logger=logger,
        )
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
        )
        if enable_image_render:
            self.image_renderer = QFarmImageRenderer(
                service_url=render_service_url,
                cache_dir=self.plugin_data_dir / "render_cache",
                timeout_sec=render_timeout_sec,
                logger=logger,
            )
            ok = await self.image_renderer.check_health(timeout_sec=render_healthcheck_sec)
            if ok:
                logger.info(f"[qfarm] 图片渲染服务可用: {render_service_url}")
            else:
                logger.warning(f"[qfarm] 图片渲染服务不可用，将自动回退文本: {render_service_url}")
            self.image_renderer.cleanup_cache()
        else:
            self.image_renderer = None

        if managed_mode:
            try:
                await self.process_manager.start()
            except Exception as e:
                logger.warning(f"[qfarm] 托管模式启动 Node 服务失败: {e}")

        await self._warmup_api()
        logger.info("[qfarm] 插件初始化完成")

    async def terminate(self) -> None:
        if self.router:
            await self.router.shutdown()
        if self.api_client:
            await self.api_client.close()
        if self.image_renderer:
            try:
                self.image_renderer.cleanup_cache()
                await self.image_renderer.close()
            except Exception as e:
                logger.warning(f"[qfarm] 关闭图片渲染客户端失败: {e}")
        if self.process_manager and self.process_manager.managed_mode:
            try:
                await self.process_manager.stop()
            except Exception as e:
                logger.warning(f"[qfarm] 关闭 Node 服务失败: {e}")
        logger.info("[qfarm] 插件已卸载")

    @filter.command("qfarm", alias={"农场"})
    async def qfarm_entry(self, event: AstrMessageEvent):
        if self.router is None:
            yield event.plain_result("qfarm 插件尚未初始化。")
            return
        replies = await self.router.handle(event)
        for reply in replies:
            rendered = False
            if (
                reply.text
                and reply.prefer_image
                and self.image_renderer is not None
                and self.state_store is not None
            ):
                theme = self.state_store.get_render_theme("light")
                payloads = build_qfarm_payload_pages(reply.text, theme=theme)
                rendered_images: list[str] = []
                for payload in payloads:
                    image_path = await self.image_renderer.render_qfarm(payload)
                    if not image_path:
                        rendered_images = []
                        break
                    rendered_images.append(image_path)
                if rendered_images:
                    rendered = True
                    for image_path in rendered_images:
                        yield event.image_result(image_path)
                else:
                    logger.warning("[qfarm] 回复图片渲染失败，自动回退文本输出。")
            if reply.text and not rendered:
                yield event.plain_result(reply.text)
            if reply.image_url:
                yield event.image_result(reply.image_url)

    async def _send_active_message(self, umo: Any, text: str) -> None:
        chain = MessageChain().message(text)
        await self.context.send_message(umo, chain)

    async def _warmup_api(self) -> None:
        if not self.api_client:
            return
        for _ in range(5):
            try:
                await self.api_client.ping()
                return
            except Exception:
                await asyncio.sleep(1)

    def _is_super_admin(self, user_id: str) -> bool:
        return str(user_id or "").strip() in self._super_admin_ids

    def _load_super_admin_ids(self) -> set[str]:
        values: list[str] = []
        try:
            context_cfg = self.context.get_config() if hasattr(self.context, "get_config") else {}
        except Exception:
            context_cfg = {}
        if isinstance(context_cfg, dict):
            for key in ("admins_id", "admins", "admin_ids", "superusers"):
                raw = context_cfg.get(key, [])
                if isinstance(raw, list):
                    values.extend([str(v).strip() for v in raw if str(v).strip()])
                elif raw:
                    values.append(str(raw).strip())
        return set(values)

    def _resolve_plugin_data_dir(self) -> Path:
        try:
            data_dir = StarTools.get_data_dir("astrbot_plugin_qfarm")
            path = Path(data_dir)
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
        except Exception:
            value = default
        try:
            return int(value)
        except Exception:
            return int(default)

    def _cfg_float(self, key: str, default: float) -> float:
        try:
            value = self.config.get(key, default)  # type: ignore[attr-defined]
        except Exception:
            value = default
        try:
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
