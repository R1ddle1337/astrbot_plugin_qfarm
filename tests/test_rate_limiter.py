from __future__ import annotations

import asyncio

import pytest

from astrbot_plugin_qfarm.services.rate_limiter import RateLimitError, RateLimiter


@pytest.mark.asyncio
async def test_user_read_cooldown():
    limiter = RateLimiter(read_cooldown_sec=0.4, write_cooldown_sec=0.0, global_concurrency=10)
    lease = await limiter.acquire("u1", is_write=False)
    lease.release()

    with pytest.raises(RateLimitError):
        await limiter.acquire("u1", is_write=False)

    await asyncio.sleep(0.45)
    lease2 = await limiter.acquire("u1", is_write=False)
    lease2.release()


@pytest.mark.asyncio
async def test_account_write_serialized():
    limiter = RateLimiter(
        read_cooldown_sec=0.0,
        write_cooldown_sec=0.0,
        global_concurrency=10,
        account_write_serialized=True,
    )
    lease1 = await limiter.acquire("u1", is_write=True, account_id="acc-x")

    acquired_second = False

    async def acquire_second():
        nonlocal acquired_second
        lease2 = await limiter.acquire("u2", is_write=True, account_id="acc-x")
        acquired_second = True
        lease2.release()

    task = asyncio.create_task(acquire_second())
    await asyncio.sleep(0.1)
    assert acquired_second is False

    lease1.release()
    await asyncio.wait_for(task, timeout=1.0)
    assert acquired_second is True

