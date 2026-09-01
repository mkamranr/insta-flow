"""Redis-backed attempt throttling.

Login is the one endpoint an unauthenticated attacker can hammer, and Argon2
makes each attempt expensive for us as well as for them — so an unthrottled
login is both a credential-stuffing risk and a cheap denial of service.

Redis is the right store: these counters are ephemeral by nature, and losing
them on a restart fails open rather than locking anyone out permanently.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

PREFIX = "throttle:"


class Throttle:
    def __init__(self, redis: Any, *, limit: int, window_seconds: int) -> None:
        self._redis = redis
        self.limit = limit
        self.window = window_seconds

    async def hit(self, key: str) -> tuple[bool, int]:
        """Count an attempt. Returns (allowed, attempts_so_far).

        Fails open: if Redis is unreachable the request is allowed rather than
        locking every operator out of their own dashboard.
        """
        full_key = f"{PREFIX}{key}"
        try:
            count = await self._redis.incr(full_key)
            if count == 1:
                await self._redis.expire(full_key, self.window)
            return count <= self.limit, int(count)
        except Exception:
            logger.warning("throttle_unavailable", exc_info=True)
            return True, 0

    async def reset(self, key: str) -> None:
        """Clear the counter, called on a successful login."""
        try:
            await self._redis.delete(f"{PREFIX}{key}")
        except Exception:
            logger.warning("throttle_reset_failed", exc_info=True)
