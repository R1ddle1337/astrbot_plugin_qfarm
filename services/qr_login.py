from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

QR_LOGIN_MODE_AUTO = "auto"
QR_LOGIN_MODE_MINIAPP = "miniapp"
QR_LOGIN_MODE_PC = "pc"
QR_LOGIN_MODES = {QR_LOGIN_MODE_AUTO, QR_LOGIN_MODE_MINIAPP, QR_LOGIN_MODE_PC}


class QRLoginError(RuntimeError):
    pass


@dataclass(slots=True)
class QRLoginConfig:
    appid: str = "1112386029"
    qua: str = "V1_HT5_QDT_0.70.2209190_x64_0_DEV_D"
    timeout_sec: int = 15

    # PC Web QR defaults (QZone preset)
    pc_appid: str = "549000912"
    pc_daid: str = "5"
    pc_redirect_uri: str = "https://qzs.qzone.qq.com/qzone/v5/loginsucc.html?para=izone"
    pc_referrer: str = "https://qzone.qq.com/"


def normalize_login_mode(mode: str | None, *, default: str = QR_LOGIN_MODE_AUTO) -> str:
    value = str(mode or "").strip().lower()
    if value in QR_LOGIN_MODES:
        return value
    fallback = str(default or QR_LOGIN_MODE_AUTO).strip().lower()
    if fallback in QR_LOGIN_MODES:
        return fallback
    return QR_LOGIN_MODE_AUTO


class QFarmQRLogin:
    def __init__(self, config: QRLoginConfig | None = None) -> None:
        self.config = config or QRLoginConfig()
        self._timeout = aiohttp.ClientTimeout(total=max(5, int(self.config.timeout_sec)))

    # ---- MiniApp (legacy behavior) ----
    async def request_login_code(self) -> dict[str, Any]:
        url = "https://q.qq.com/ide/devtoolAuth/GetLoginCode"
        async with aiohttp.ClientSession(timeout=self._timeout, headers=self._miniapp_headers()) as session:
            async with session.get(url) as resp:
                data = await self._read_json(resp)

        if _to_int(data.get("code"), -1) != 0:
            raise QRLoginError("failed to get miniapp login code")

        payload = data.get("data", {}) if isinstance(data, dict) else {}
        code = str(payload.get("code") or "").strip()
        if not code:
            raise QRLoginError("miniapp login code is empty")

        login_url = f"https://h5.qzone.qq.com/qqq/code/{code}?_proxy=1&from=ide"
        return {"code": code, "url": login_url}

    async def query_status(self, code: str) -> dict[str, Any]:
        code_text = str(code or "").strip()
        if not code_text:
            raise QRLoginError("code cannot be empty")

        url = (
            "https://q.qq.com/ide/devtoolAuth/syncScanSateGetTicket"
            f"?code={aiohttp.helpers.quote(code_text, safe='')}"
        )
        async with aiohttp.ClientSession(timeout=self._timeout, headers=self._miniapp_headers()) as session:
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
        async with aiohttp.ClientSession(timeout=self._timeout, headers=self._miniapp_headers()) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    return ""
                data = await self._read_json(resp)
        return str(data.get("code") or "")

    # ---- Unified API ----
    async def create(self, mode: str = QR_LOGIN_MODE_AUTO) -> dict[str, Any]:
        selected = normalize_login_mode(mode)
        if selected == QR_LOGIN_MODE_MINIAPP:
            return await self._create_miniapp()
        if selected == QR_LOGIN_MODE_PC:
            return await self._create_pc()
        return await self._create_auto()

    async def check(
        self,
        code: str,
        mode: str = QR_LOGIN_MODE_AUTO,
        *,
        poll_timeout: float | None = None,
        auto_retry: bool = False,
        retry_backoff: float = 1.0,
    ) -> dict[str, Any]:
        code_text = str(code or "").strip()
        if not code_text:
            raise QRLoginError("code cannot be empty")

        selected = normalize_login_mode(mode)
        if not auto_retry:
            return await self._check_once(code_text, selected)

        timeout_sec = float(poll_timeout if poll_timeout is not None else 120)
        timeout_sec = max(1.0, timeout_sec)
        backoff_sec = max(0.1, float(retry_backoff or 1.0))
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while True:
            result = await self._check_once(code_text, selected)
            if str(result.get("status") or "") != "Wait":
                return result
            remain = deadline - asyncio.get_running_loop().time()
            if remain <= 0:
                result["timeout"] = True
                return result
            await asyncio.sleep(min(backoff_sec, remain))

    # ---- Internal mode dispatch ----
    async def _create_auto(self) -> dict[str, Any]:
        mini_error: Exception | None = None
        try:
            return await self._create_miniapp()
        except Exception as e:
            mini_error = e
        try:
            payload = await self._create_pc()
            payload["fallback"] = "miniapp_to_pc"
            return payload
        except Exception as e:
            if mini_error is not None:
                raise QRLoginError(f"create failed in auto mode: miniapp={mini_error}; pc={e}") from e
            raise

    async def _create_miniapp(self) -> dict[str, Any]:
        payload = await self.request_login_code()
        payload["mode"] = QR_LOGIN_MODE_MINIAPP
        return payload

    async def _create_pc(self) -> dict[str, Any]:
        params = {
            "appid": self.config.pc_appid,
            "e": "2",
            "l": "M",
            "s": "3",
            "d": "72",
            "v": "4",
            "t": str(asyncio.get_running_loop().time()),
            "daid": self.config.pc_daid,
            "u1": self.config.pc_redirect_uri,
        }
        url = f"https://ssl.ptlogin2.qq.com/ptqrshow?{urlencode(params)}"
        headers = {
            "Referer": self.config.pc_referrer or "https://xui.ptlogin2.qq.com/",
            "User-Agent": CHROME_UA,
        }
        async with aiohttp.ClientSession(timeout=self._timeout, headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise QRLoginError(f"failed to get pc qrcode: HTTP {resp.status}")
                qrcode_png = await resp.read()
                qrsig = _extract_cookie_value(resp.headers.getall("Set-Cookie", []), "qrsig")

        if not qrsig:
            raise QRLoginError("failed to get qrsig from pc qrcode response")
        if not isinstance(qrcode_png, (bytes, bytearray)) or not qrcode_png:
            raise QRLoginError("empty pc qrcode image payload")
        return {
            "mode": QR_LOGIN_MODE_PC,
            "code": qrsig,
            "url": url,
            "qrcode_png": bytes(qrcode_png),
        }

    async def _check_once(self, code: str, mode: str) -> dict[str, Any]:
        if mode == QR_LOGIN_MODE_MINIAPP:
            return await self._check_miniapp(code)
        if mode == QR_LOGIN_MODE_PC:
            return await self._check_pc(code)
        return await self._check_auto(code)

    async def _check_auto(self, code: str) -> dict[str, Any]:
        mini_error = ""
        try:
            mini = await self._check_miniapp(code)
            mini_status = str(mini.get("status") or "")
            if mini_status in {"Wait", "Used", "OK"}:
                return mini
            mini_error = str(mini.get("error") or "miniapp check failed")
        except Exception as e:
            mini_error = str(e or "miniapp check failed")

        try:
            pc = await self._check_pc(code)
            if str(pc.get("status") or "") != "Error":
                return pc
            pc_error = str(pc.get("error") or "pc check failed")
            return {"status": "Error", "error": f"miniapp: {mini_error}; pc: {pc_error}"}
        except Exception as e:
            return {"status": "Error", "error": f"miniapp: {mini_error}; pc: {e}"}

    async def _check_miniapp(self, code: str) -> dict[str, Any]:
        status = await self.query_status(code)
        if status.get("status") != "OK":
            if status.get("status") == "Used":
                return {"status": "Used", "mode": QR_LOGIN_MODE_MINIAPP}
            if status.get("status") == "Wait":
                return {"status": "Wait", "mode": QR_LOGIN_MODE_MINIAPP}
            return {
                "status": "Error",
                "error": str(status.get("msg") or "miniapp status error"),
                "mode": QR_LOGIN_MODE_MINIAPP,
            }

        ticket = str(status.get("ticket") or "")
        uin = str(status.get("uin") or "")
        auth_code = await self.get_auth_code(ticket)
        avatar = f"https://q1.qlogo.cn/g?b=qq&nk={uin}&s=640" if uin else ""
        return {
            "status": "OK",
            "code": auth_code,
            "uin": uin,
            "avatar": avatar,
            "mode": QR_LOGIN_MODE_MINIAPP,
        }

    async def _check_pc(self, code: str) -> dict[str, Any]:
        status = await self._query_pc_status(code)
        state = str(status.get("status") or "")
        if state == "Wait":
            return {"status": "Wait", "mode": QR_LOGIN_MODE_PC}
        if state == "Used":
            return {"status": "Used", "mode": QR_LOGIN_MODE_PC}
        if state != "OK":
            return {
                "status": "Error",
                "error": str(status.get("msg") or "pc status error"),
                "mode": QR_LOGIN_MODE_PC,
            }

        uin = str(status.get("uin") or "")
        code_text = str(status.get("code") or "")
        avatar = f"https://q1.qlogo.cn/g?b=qq&nk={uin}&s=640" if uin else ""
        return {
            "status": "OK",
            "code": code_text,
            "uin": uin,
            "avatar": avatar,
            "mode": QR_LOGIN_MODE_PC,
        }

    async def _query_pc_status(self, qrsig: str) -> dict[str, Any]:
        token = _ptqrtoken(qrsig)
        params = {
            "ptqrtoken": str(token),
            "from_ui": "1",
            "aid": self.config.pc_appid,
            "daid": self.config.pc_daid,
            "action": f"0-0-{int(asyncio.get_running_loop().time() * 1000)}",
            "pt_uistyle": "40",
            "js_ver": "21020514",
            "js_type": "1",
            "u1": self.config.pc_redirect_uri,
        }
        url = f"https://ssl.ptlogin2.qq.com/ptqrlogin?{urlencode(params)}"
        headers = {
            "Cookie": f"qrsig={qrsig}",
            "Referer": self.config.pc_referrer or "https://xui.ptlogin2.qq.com/",
            "User-Agent": CHROME_UA,
        }
        async with aiohttp.ClientSession(timeout=self._timeout, headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return {"status": "Error", "msg": f"HTTP {resp.status}"}
                text = await resp.text(errors="ignore")
                cookies = resp.headers.getall("Set-Cookie", [])
        return _parse_pc_login_status(text, cookies)

    def _miniapp_headers(self) -> dict[str, str]:
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
            raise QRLoginError(f"qr endpoint returned invalid json: {e}") from e
        if isinstance(data, dict):
            return data
        raise QRLoginError("qr endpoint returned non-object json")


def _extract_cookie_value(set_cookie: list[str] | tuple[str, ...], key: str) -> str:
    name = str(key or "").strip()
    if not name:
        return ""
    for row in set_cookie or []:
        for part in str(row).split(";"):
            segment = part.strip()
            if not segment or "=" not in segment:
                continue
            k, v = segment.split("=", 1)
            if k.strip() == name:
                return v.strip()
    return ""


def _extract_uin_from_cookies(set_cookie: list[str] | tuple[str, ...]) -> str:
    value = (
        _extract_cookie_value(set_cookie, "wxuin")
        or _extract_cookie_value(set_cookie, "uin")
        or _extract_cookie_value(set_cookie, "ptui_loginuin")
    )
    if not value:
        return ""
    return re.sub(r"^o0*", "", str(value))


def _ptqrtoken(qrsig: str) -> int:
    token = 0
    for ch in str(qrsig or ""):
        token += (token << 5) + ord(ch)
    return token & 0x7FFFFFFF


def _extract_code_from_url(url: str) -> str:
    target = str(url or "").strip()
    if not target:
        return ""
    try:
        parsed = urlparse(target)
        query = parse_qs(parsed.query)
    except Exception:
        return ""
    for key in ("code", "auth_code", "ticket"):
        values = query.get(key) or []
        if values and str(values[0]).strip():
            return str(values[0]).strip()
    return ""


def _parse_pc_login_status(text: str, set_cookie: list[str] | tuple[str, ...]) -> dict[str, Any]:
    match = re.search(r"ptuiCB\((.+)\)", str(text or ""))
    if not match:
        return {"status": "Error", "msg": "invalid ptqrlogin response"}
    args = re.findall(r"'([^']*)'", match.group(1))
    if not args:
        return {"status": "Error", "msg": "empty ptqrlogin response args"}

    ret = str(args[0] or "")
    jump_url = str(args[2] if len(args) > 2 else "")
    msg = str(args[4] if len(args) > 4 else "")

    if ret in {"66", "67"}:
        return {"status": "Wait", "msg": msg}
    if ret in {"65", "68"}:
        return {"status": "Used", "msg": msg}
    if ret != "0":
        return {"status": "Error", "msg": msg or f"ptqr ret={ret}"}

    return {
        "status": "OK",
        "code": _extract_code_from_url(jump_url),
        "uin": _extract_uin_from_cookies(set_cookie),
        "msg": msg,
    }


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)
