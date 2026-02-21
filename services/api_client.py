from __future__ import annotations

import asyncio
from typing import Any

from .runtime.runtime_manager import QFarmRuntimeManager


class QFarmApiError(RuntimeError):
    """qfarm 本地后端调用失败。"""


class QFarmApiClient:
    def __init__(
        self,
        backend: QFarmRuntimeManager,
        logger: Any | None = None,
        request_timeout_sec: int = 15,
    ) -> None:
        self.backend = backend
        self.logger = logger
        self.request_timeout_sec = max(1, int(request_timeout_sec))

    async def close(self) -> None:
        return

    async def ping(self) -> bool:
        return await self._wrap(self.backend.ping())

    async def get_accounts(self) -> dict[str, Any]:
        return await self._wrap(self.backend.get_accounts())

    async def upsert_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._wrap(self.backend.upsert_account(payload or {}))

    async def delete_account(self, account_id: str | int) -> dict[str, Any]:
        return await self._wrap(self.backend.delete_account(account_id))

    async def start_account(self, account_id: str | int) -> None:
        await self._wrap(self.backend.start_account(account_id))

    async def stop_account(self, account_id: str | int) -> None:
        await self._wrap(self.backend.stop_account(account_id))

    async def get_status(self, account_id: str | int) -> dict[str, Any]:
        return await self._wrap(self.backend.get_status(account_id))

    async def get_lands(self, account_id: str | int) -> dict[str, Any]:
        return await self._wrap(self.backend.get_lands(account_id))

    async def get_friends(self, account_id: str | int) -> list[dict[str, Any]]:
        return await self._wrap(self.backend.get_friends(account_id))

    async def get_friend_lands(self, account_id: str | int, friend_gid: str | int) -> dict[str, Any]:
        return await self._wrap(self.backend.get_friend_lands(account_id, friend_gid))

    async def do_friend_op(self, account_id: str | int, friend_gid: str | int, op_type: str) -> dict[str, Any]:
        return await self._wrap(self.backend.do_friend_op(account_id, friend_gid, op_type))

    async def get_seeds(self, account_id: str | int) -> list[dict[str, Any]]:
        return await self._wrap(self.backend.get_seeds(account_id))

    async def get_bag(self, account_id: str | int) -> dict[str, Any]:
        return await self._wrap(self.backend.get_bag(account_id))

    async def do_farm_operation(self, account_id: str | int, op_type: str) -> dict[str, Any]:
        return await self._wrap(self.backend.do_farm_op(account_id, op_type))

    async def get_analytics(self, account_id: str | int, sort_by: str) -> list[dict[str, Any]]:
        return await self._wrap(self.backend.get_analytics(account_id, sort_by))

    async def get_settings(self, account_id: str | int) -> dict[str, Any]:
        return await self._wrap(self.backend.get_settings(account_id))

    async def set_automation(self, account_id: str | int, key: str, value: Any) -> dict[str, Any]:
        return await self._wrap(self.backend.set_automation(account_id, key, value))

    async def save_settings(self, account_id: str | int, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._wrap(self.backend.save_settings(account_id, payload or {}))

    async def set_theme(self, theme: str) -> dict[str, Any]:
        return await self._wrap(self.backend.set_theme(theme))

    async def get_logs(self, account_id: str | int, **filters: Any) -> list[dict[str, Any]]:
        return await self._wrap(self.backend.get_logs(account_id, **filters))

    async def get_account_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._wrap(self.backend.get_account_logs(limit))

    async def debug_sell(self, account_id: str | int) -> None:
        await self._wrap(self.backend.debug_sell(account_id))

    async def qr_create(self) -> dict[str, Any]:
        return await self._wrap(self.backend.qr_create())

    async def qr_check(self, code: str) -> dict[str, Any]:
        return await self._wrap(self.backend.qr_check(code))

    async def _wrap(self, awaitable):
        timeout = max(1, int(self.request_timeout_sec))
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout)
        except asyncio.TimeoutError as e:
            raise QFarmApiError(f"请求超时({timeout}s)，请稍后重试。") from e
        except QFarmApiError:
            raise
        except Exception as e:
            raise QFarmApiError(str(e)) from e
