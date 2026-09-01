"""Redis connection used for the ARQ job queue.

Redis is a queue and cache only. MongoDB is authoritative (§66) — nothing here
may be the sole record of anything.
"""

import logging
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

logger = logging.getLogger(__name__)

_pool: ArqRedis | None = None


async def connect(redis_url: str) -> ArqRedis:
    global _pool
    _pool = await create_pool(RedisSettings.from_dsn(redis_url))
    logger.info("redis_connected")
    return _pool


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        logger.info("redis_disconnected")
    _pool = None


def get_pool() -> ArqRedis:
    if _pool is None:
        raise RuntimeError("Redis is not connected; call connect() during startup")
    return _pool


async def ping() -> bool:
    """Cheap liveness probe used by /ready. Never raises."""
    try:
        if _pool is None:
            return False
        await _pool.ping()
        return True
    except Exception:
        logger.warning("redis_ping_failed", exc_info=True)
        return False


async def enqueue(job: str, *args: Any, **kwargs: Any) -> str | None:
    result = await get_pool().enqueue_job(job, *args, **kwargs)
    return result.job_id if result else None
