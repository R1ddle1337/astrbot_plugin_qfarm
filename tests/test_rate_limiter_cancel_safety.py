from __future__ import annotations

import asyncio

import pytest

from astrbot_plugin_qfarm.services.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_cancelled_writer_waiting_for_account_lock_does_not_leak_global_semaphore():
    limiter = RateLimiter(
        read_cooldown_sec=0.0,
        write_cooldown_sec=0.0,
        global_concurrency=2,
        account_write_serialized=True,
    )
    lease1 = await limiter.acquire("u1", is_write=True, account_id="acc-1")

    task = asyncio.create_task(limiter.acquire("u2", is_write=True, account_id="acc-1"))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    lease3 = await asyncio.wait_for(
        limiter.acquire("u3", is_write=True, account_id="acc-2"),
        timeout=0.5,
    )
    lease3.release()
    lease1.release()
