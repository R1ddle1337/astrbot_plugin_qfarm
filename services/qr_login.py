from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class QRLoginError(RuntimeError):
    pass


@dataclass(slots=True)
class QRLoginConfig:
    appid: str = "1112386029"
    qua: str = "V1_HT5_QDT_0.70.2209190_x64_0_DEV_D"
    timeout_sec: int = 15


class QFarmQRLogin:
    def __init__(self, config: QRLoginConfig | None = None) -> None:
        self.config = config or QRLoginConfig()
        self._timeout = aiohttp.ClientTimeout(total=max(5, int(self.config.timeout_sec)))

    async def request_login_code(self) -> dict[str, Any]:
        url = "https://q.qq.com/ide/devtoolAuth/GetLoginCode"
        async with aiohttp.ClientSession(timeout=self._timeout, headers=self._headers()) as session:
            async with session.get(url) as resp:
                data = await self._read_json(resp)
        if _to_int(data.get("code"), -1) != 0:
            raise QRLoginError("获取登录码失败")
        payload = data.get("data", {}) if isinstance(data, dict) else {}
        code = str(payload.get("code") or "").strip()
        if not code:
            raise QRLoginError("登录码为空")
        login_url = f"https://h5.qzone.qq.com/qqq/code/{code}?_proxy=1&from=ide"
        qrcode_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={aiohttp.helpers.quote(login_url, safe='')}"
        return {"code": code, "url": login_url, "qrcode": qrcode_url}

    async def query_status(self, code: str) -> dict[str, Any]:
        code_text = str(code or "").strip()
        if not code_text:
            raise QRLoginError("code 不能为空")
        url = f"https://q.qq.com/ide/devtoolAuth/syncScanSateGetTicket?code={aiohttp.helpers.quote(code_text, safe='')}"
        async with aiohttp.ClientSession(timeout=self._timeout, headers=self._headers()) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return {"status": "Error", "msg": f"HTTP {resp.status}"}
                data = await self._read_json(resp)

        res_code = _to_int(data.get("code"), -1)
        payload = data.get("data", {}) if isinstance(data, dict) else {}
        if res_code == 0:
            if _to_int(payload.get("ok"), 0) != 1:
                return {"status": "Wait"}
            return {
                "status": "OK",
                "ticket": str(payload.get("ticket") or ""),
                "uin": str(payload.get("uin") or ""),
            }
        if res_code == -10003:
            return {"status": "Used"}
        return {"status": "Error", "msg": f"Code: {res_code}"}

    async def get_auth_code(self, ticket: str) -> str:
        ticket_text = str(ticket or "").strip()
        if not ticket_text:
            return ""
        url = "https://q.qq.com/ide/login"
        payload = {"appid": self.config.appid, "ticket": ticket_text}
        async with aiohttp.ClientSession(timeout=self._timeout, headers=self._headers()) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    return ""
                data = await self._read_json(resp)
        return str(data.get("code") or "")

    async def create(self) -> dict[str, Any]:
        return await self.request_login_code()

    async def check(self, code: str) -> dict[str, Any]:
        status = await self.query_status(code)
        if status.get("status") != "OK":
            if status.get("status") == "Used":
                return {"status": "Used"}
            if status.get("status") == "Wait":
                return {"status": "Wait"}
            return {"status": "Error", "error": str(status.get("msg") or "未知错误")}
        ticket = str(status.get("ticket") or "")
        uin = str(status.get("uin") or "")
        auth_code = await self.get_auth_code(ticket)
        avatar = f"https://q1.qlogo.cn/g?b=qq&nk={uin}&s=640" if uin else ""
        return {"status": "OK", "code": auth_code, "uin": uin, "avatar": avatar}

    def _headers(self) -> dict[str, str]:
        return {
            "qua": self.config.qua,
            "host": "q.qq.com",
            "accept": "application/json",
            "content-type": "application/json",
            "user-agent": CHROME_UA,
        }

    @staticmethod
    async def _read_json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
        try:
            data = await resp.json(content_type=None)
        except Exception as e:
            raise QRLoginError(f"扫码接口返回异常: {e}") from e
        if isinstance(data, dict):
            return data
        raise QRLoginError("扫码接口返回非 JSON 对象")


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)
