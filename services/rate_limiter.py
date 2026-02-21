from __future__ import annotations

import asyncio
import time


class RateLimitError(RuntimeError):
    """命令触发限流时抛出。"""


class _RateLease:
    def __init__(self, semaphore: asyncio.Semaphore, account_lock: asyncio.Lock | None) -> None:
        self._semaphore = semaphore
        self._account_lock = account_lock
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._account_lock and self._account_lock.locked():
            self._account_lock.release()
        self._semaphore.release()


class RateLimiter:
    """三级控制：用户冷却、全局并发、账号写串行。"""

    def __init__(
        self,
        read_cooldown_sec: float = 1.0,
        write_cooldown_sec: float = 2.0,
        global_concurrency: int = 20,
        account_write_serialized: bool = True,
    ) -> None:
        self.read_cooldown_sec = max(0.0, float(read_cooldown_sec))
        self.write_cooldown_sec = max(0.0, float(write_cooldown_sec))
        self.account_write_serialized = bool(account_write_serialized)
        self._global_sem = asyncio.Semaphore(max(1, int(global_concurrency)))

        self._state_lock = asyncio.Lock()
        self._next_read_ts: dict[str, float] = {}
        self._next_write_ts: dict[str, float] = {}
        self._account_locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, user_id: str | int, is_write: bool, account_id: str | int | None = None) -> _RateLease:
        uid = str(user_id or "").strip()
        if not uid:
            raise RateLimitError("无法识别用户身份，拒绝执行。")

        now = time.monotonic()
        cooldown = self.write_cooldown_sec if is_write else self.read_cooldown_sec
        tracking = self._next_write_ts if is_write else self._next_read_ts
        cmd_type = "写操作" if is_write else "读操作"

        async with self._state_lock:
            next_ts = float(tracking.get(uid, 0.0))
            if next_ts > now:
                wait_sec = max(0.1, next_ts - now)
                raise RateLimitError(f"{cmd_type}过于频繁，请 {wait_sec:.1f}s 后再试。")
            tracking[uid] = now + cooldown

        account_lock = None
        acquired_global = False
        acquired_account_lock = False
        try:
            await self._global_sem.acquire()
            acquired_global = True

            if is_write and self.account_write_serialized and account_id is not None:
                aid = str(account_id).strip()
                if aid:
                    async with self._state_lock:
                        account_lock = self._account_locks.setdefault(aid, asyncio.Lock())
                    await account_lock.acquire()
                    acquired_account_lock = True

            return _RateLease(self._global_sem, account_lock if acquired_account_lock else None)
        except BaseException:
            if acquired_account_lock and account_lock and account_lock.locked():
                account_lock.release()
            if acquired_global:
                self._global_sem.release()
            raise
