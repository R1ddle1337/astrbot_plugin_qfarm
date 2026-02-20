from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import aiohttp


class QFarmImageRenderer:
    """qfarm 图片渲染客户端。"""

    def __init__(
        self,
        service_url: str,
        cache_dir: Path,
        timeout_sec: int = 30,
        logger: Any | None = None,
    ) -> None:
        self.service_url = str(service_url or "").strip().rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_sec = max(1, int(timeout_sec))
        self.logger = logger
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def check_health(self, timeout_sec: int = 3) -> bool:
        session = await self._ensure_session()
        timeout = aiohttp.ClientTimeout(total=max(1, int(timeout_sec)))
        try:
            async with session.get(f"{self.service_url}/health", timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def render_qfarm(self, payload: dict[str, Any]) -> str | None:
        session = await self._ensure_session()
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        try:
            async with session.post(f"{self.service_url}/api/qfarm", json=payload, timeout=timeout) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    self._log_warning(f"qfarm 渲染失败: HTTP {resp.status}, body={body[:240]}")
                    return None
                content = await resp.read()
                if not content:
                    self._log_warning("qfarm 渲染失败: 响应为空")
                    return None
                image_path = self._allocate_image_path()
                image_path.write_bytes(content)
                return str(image_path)
        except Exception as e:
            self._log_warning(f"qfarm 渲染请求异常: {e}")
            return None

    def cleanup_cache(self, max_age_sec: int = 86400) -> int:
        now = time.time()
        removed = 0
        ttl = max(60, int(max_age_sec))
        for path in self.cache_dir.glob("*.png"):
            try:
                if now - path.stat().st_mtime >= ttl:
                    path.unlink(missing_ok=True)
                    removed += 1
            except Exception:
                continue
        return removed

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session and not self._session.closed:
            return self._session
        self._session = aiohttp.ClientSession(
            headers={"User-Agent": "qfarm-renderer/1.0"},
            json_serialize=lambda data: json.dumps(data, ensure_ascii=False),
        )
        return self._session

    def _allocate_image_path(self) -> Path:
        ts = int(time.time() * 1000)
        token = uuid.uuid4().hex[:8]
        return self.cache_dir / f"qfarm_{ts}_{token}.png"

    def _log_warning(self, message: str) -> None:
        if self.logger and hasattr(self.logger, "warning"):
            self.logger.warning(message)
        elif self.logger and hasattr(self.logger, "warn"):
            self.logger.warn(message)
        else:
            print(message)
