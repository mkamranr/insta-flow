"""Single-use, signed, expiring OAuth state (§50).

The signature stops an attacker minting a state; the Redis nonce makes it
single-use, which is what actually blocks a replayed callback. A signature alone
would let the same callback URL be submitted repeatedly.
"""

import logging
import secrets
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

SALT = "instaflow-oauth-state-v1"
STATE_TTL_SECONDS = 600
REDIS_PREFIX = "oauth:state:"


def _serializer(secret: str) -> URLSafeTimedSerializer:
    if not secret:
        raise RuntimeError("SESSION_SECRET is not configured")
    return URLSafeTimedSerializer(secret, salt=SALT)


async def issue(redis: Any, secret: str, user_id: str) -> str:
    nonce = secrets.token_urlsafe(24)
    await redis.set(f"{REDIS_PREFIX}{nonce}", str(user_id), ex=STATE_TTL_SECONDS)
    return _serializer(secret).dumps({"n": nonce, "u": str(user_id)})


async def consume(redis: Any, secret: str, state: str) -> str | None:
    """Validate and burn the state. Returns the user id, or None if invalid.

    The Redis delete is the single-use check: it returns 1 only for the first
    caller, so a replayed callback gets 0 and is rejected.
    """
    if not state:
        return None
    try:
        payload: Any = _serializer(secret).loads(state, max_age=STATE_TTL_SECONDS)
    except (SignatureExpired, BadSignature):
        logger.warning("oauth_state_invalid_signature")
        return None
    if not isinstance(payload, dict) or "n" not in payload:
        return None

    deleted = await redis.delete(f"{REDIS_PREFIX}{payload['n']}")
    if not deleted:
        logger.warning("oauth_state_replayed_or_expired")
        return None
    return str(payload.get("u", ""))
