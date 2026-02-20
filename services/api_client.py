from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import aiohttp
import asyncio


class QFarmApiError(RuntimeError):
    """qfarm HTTP API 调用失败。"""


class QFarmApiClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3000,
        admin_password: str = "",
        timeout_sec: int = 15,
        logger: Any | None = None,
    ) -> None:
        self.host = str(host or "127.0.0.1").strip()
        self.port = int(port)
        self.admin_password = str(admin_password or "")
        self.timeout_sec = max(1, int(timeout_sec))
        self.logger = logger

        self._base_url = f"http://{self.host}:{self.port}"
        self._session: aiohttp.ClientSession | None = None
        self._token: str = ""
        self._auth_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._token = ""

    async def request(
        self,
        method: str,
        path: str,
        *,
        account_id: str | int | None = None,
        json_data: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        auth: bool = True,
        retry_on_401: bool = True,
    ) -> Any:
        session = await self._ensure_session()
        url = urljoin(self._base_url, path)
        headers: dict[str, str] = {}
        if auth:
            await self._ensure_login()
            if self._token:
                headers["x-admin-token"] = self._token
        if account_id is not None:
            headers["x-account-id"] = str(account_id)

        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        try:
            async with session.request(
                method.upper(),
                url,
                json=json_data,
                params=query,
                headers=headers,
                timeout=timeout,
            ) as resp:
                body = await self._read_response_body(resp)
                if resp.status == 401 and auth and retry_on_401:
                    self._token = ""
                    return await self.request(
                        method,
                        path,
                        account_id=account_id,
                        json_data=json_data,
                        query=query,
                        auth=auth,
                        retry_on_401=False,
                    )
                if resp.status >= 400:
                    raise QFarmApiError(self._extract_error_message(body, fallback=f"HTTP {resp.status}"))

                if isinstance(body, dict) and "ok" in body:
                    if not body.get("ok"):
                        raise QFarmApiError(self._extract_error_message(body, fallback="接口返回失败"))
                    return body.get("data")
                return body
        except aiohttp.ClientError as e:
            raise QFarmApiError(f"网络请求失败: {e}") from e

    async def login(self) -> str:
        data = await self.request(
            "POST",
            "/api/login",
            json_data={"password": self.admin_password},
            auth=False,
            retry_on_401=False,
        )
        token = ""
        if isinstance(data, dict):
            token = str(data.get("token") or "")
        if not token:
            raise QFarmApiError("登录成功但未返回 token。")
        self._token = token
        return token

    async def ping(self) -> bool:
        await self.request("GET", "/api/ping")
        return True

    async def get_accounts(self) -> dict[str, Any]:
        data = await self.request("GET", "/api/accounts")
        return data or {"accounts": [], "nextId": 1}

    async def upsert_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self.request("POST", "/api/accounts", json_data=payload or {})
        return data or {}

    async def delete_account(self, account_id: str | int) -> dict[str, Any]:
        data = await self.request("DELETE", f"/api/accounts/{account_id}")
        return data or {}

    async def start_account(self, account_id: str | int) -> None:
        await self.request("POST", f"/api/accounts/{account_id}/start")

    async def stop_account(self, account_id: str | int) -> None:
        await self.request("POST", f"/api/accounts/{account_id}/stop")

    async def get_status(self, account_id: str | int) -> dict[str, Any]:
        data = await self.request("GET", "/api/status", account_id=account_id)
        return data or {}

    async def get_lands(self, account_id: str | int) -> dict[str, Any]:
        data = await self.request("GET", "/api/lands", account_id=account_id)
        return data or {}

    async def get_friends(self, account_id: str | int) -> list[dict[str, Any]]:
        data = await self.request("GET", "/api/friends", account_id=account_id)
        return data or []

    async def get_friend_lands(self, account_id: str | int, friend_gid: str | int) -> dict[str, Any]:
        data = await self.request("GET", f"/api/friend/{friend_gid}/lands", account_id=account_id)
        return data or {}

    async def do_friend_op(self, account_id: str | int, friend_gid: str | int, op_type: str) -> dict[str, Any]:
        data = await self.request(
            "POST",
            f"/api/friend/{friend_gid}/op",
            account_id=account_id,
            json_data={"opType": op_type},
        )
        return data or {}

    async def get_seeds(self, account_id: str | int) -> list[dict[str, Any]]:
        data = await self.request("GET", "/api/seeds", account_id=account_id)
        return data or []

    async def get_bag(self, account_id: str | int) -> dict[str, Any]:
        data = await self.request("GET", "/api/bag", account_id=account_id)
        return data or {}

    async def do_farm_operation(self, account_id: str | int, op_type: str) -> None:
        await self.request("POST", "/api/farm/operate", account_id=account_id, json_data={"opType": op_type})

    async def get_analytics(self, account_id: str | int, sort_by: str) -> list[dict[str, Any]]:
        data = await self.request("GET", "/api/analytics", account_id=account_id, query={"sort": sort_by})
        return data or []

    async def get_settings(self, account_id: str | int) -> dict[str, Any]:
        data = await self.request("GET", "/api/settings", account_id=account_id)
        return data or {}

    async def set_automation(self, account_id: str | int, key: str, value: Any) -> dict[str, Any]:
        data = await self.request("POST", "/api/automation", account_id=account_id, json_data={key: value})
        return data or {}

    async def save_settings(self, account_id: str | int, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self.request("POST", "/api/settings/save", account_id=account_id, json_data=payload or {})
        return data or {}

    async def set_theme(self, theme: str) -> dict[str, Any]:
        data = await self.request("POST", "/api/settings/theme", json_data={"theme": theme})
        return data or {}

    async def get_logs(self, account_id: str | int, **filters: Any) -> list[dict[str, Any]]:
        params = {key: value for key, value in filters.items() if value is not None and value != ""}
        data = await self.request("GET", "/api/logs", account_id=account_id, query=params)
        return data or []

    async def get_account_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        data = await self.request("GET", "/api/account-logs", query={"limit": int(limit)})
        return data or []

    async def debug_sell(self, account_id: str | int) -> None:
        await self.request("POST", "/api/sell/debug", account_id=account_id)

    async def qr_create(self) -> dict[str, Any]:
        data = await self.request("POST", "/api/qr/create", auth=False, retry_on_401=False)
        return data or {}

    async def qr_check(self, code: str) -> dict[str, Any]:
        data = await self.request(
            "POST",
            "/api/qr/check",
            json_data={"code": code},
            auth=False,
            retry_on_401=False,
        )
        return data or {}

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        self._session = aiohttp.ClientSession()
        return self._session

    async def _ensure_login(self) -> None:
        if self._token:
            return
        async with self._auth_lock:
            if self._token:
                return
            await self.login()

    async def _read_response_body(self, resp: aiohttp.ClientResponse) -> Any:
        text = await resp.text()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": text}

    def _extract_error_message(self, body: Any, fallback: str) -> str:
        if isinstance(body, dict):
            msg = body.get("error") or body.get("message") or body.get("msg")
            if msg:
                return str(msg)
        return fallback
