"""Reading and writing Meta access tokens on account records.

The plaintext token exists only inside this module's return value and the Meta
client. It is never stored, logged, or returned to the frontend (§50, §51).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.constants import TOKEN_LIFETIME_DAYS, TOKEN_MIN_REFRESH_AGE_HOURS
from app.services.security.encryption import encrypt

# Refresh well before the 60-day expiry so a quiet week cannot kill the account.
REFRESH_WHEN_DAYS_LEFT = 10


def token_fields(access_token: str, expires_at: datetime | None, secret: str) -> dict[str, Any]:
    """Build the account fields that carry a token. Encrypts on the way in."""
    return {
        "access_token_encrypted": encrypt(access_token, secret),
        "token_expires_at": expires_at
        or datetime.now(UTC) + timedelta(days=TOKEN_LIFETIME_DAYS),
        "token_obtained_at": datetime.now(UTC),
    }


def needs_refresh(account: dict[str, Any], now: datetime | None = None) -> bool:
    """Is this token close enough to expiry to be worth refreshing?

    Meta refuses to refresh a token less than 24 hours old, so a freshly issued
    one is reported as not needing it regardless of the expiry window.
    """
    now = now or datetime.now(UTC)

    obtained = account.get("token_obtained_at")
    if isinstance(obtained, datetime):
        if obtained.tzinfo is None:
            obtained = obtained.replace(tzinfo=UTC)
        if now - obtained < timedelta(hours=TOKEN_MIN_REFRESH_AGE_HOURS):
            return False

    expires = account.get("token_expires_at")
    if not isinstance(expires, datetime):
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires - now <= timedelta(days=REFRESH_WHEN_DAYS_LEFT)


def is_expired(account: dict[str, Any], now: datetime | None = None) -> bool:
    expires = account.get("token_expires_at")
    if not isinstance(expires, datetime):
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires <= (now or datetime.now(UTC))
