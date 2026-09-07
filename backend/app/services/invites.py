"""Invitation policy: how long a link lives, and what it points at.

Kept separate from the repository so the API layer holds no crypto and no magic
strings, and separate from the route so the expiry rule has one home.
"""

from datetime import datetime, timedelta
from typing import Any, Final

from app.db.repositories.base import utcnow

INVITE_EXPIRY_KEY: Final = "invite_expiry_hours"

DEFAULT_EXPIRY_HOURS: Final = 48
# Floor of one hour: below that the mail may not even be delivered in time.
MIN_EXPIRY_HOURS: Final = 1
# Ceiling of thirty days. A bearer credential that mints an account and is valid
# for a quarter is not an invitation, it is a back door.
MAX_EXPIRY_HOURS: Final = 720


def expiry_hours(saved: dict[str, Any] | None) -> int:
    """The configured window, defaulted at read time.

    `saved.get(key, DEFAULT)` rather than a written-on-first-boot row, so a
    deployment that never opened the Settings page behaves identically to one
    that saved 48 -- the pattern `get_disclosure` already uses.
    """
    raw = (saved or {}).get(INVITE_EXPIRY_KEY, DEFAULT_EXPIRY_HOURS)
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_EXPIRY_HOURS
    return max(MIN_EXPIRY_HOURS, min(MAX_EXPIRY_HOURS, hours))


def expires_at(saved: dict[str, Any] | None, *, now: datetime | None = None) -> datetime:
    """The deadline to stamp on an invite, computed **at send time**.

    Stored rather than recomputed at redemption, and the reason is the email:
    it states a deadline in words. Recomputing later would mean shortening the
    setting retroactively kills links whose message promised otherwise, and
    lengthening it silently resurrects links the operator believed dead. The
    stored value is the one the recipient was actually told.

    It also keeps redemption a pure comparison, with no settings read on the one
    endpoint an unauthenticated caller can hammer.
    """
    return (now or utcnow()) + timedelta(hours=expiry_hours(saved))


def activation_url(origin: str, token: str) -> str:
    """The link that goes in the email.

    Built from the deployment's configured origin -- `credentials.origin_for`,
    which prefers the saved override and falls back to `PUBLIC_BASE_URL` --
    and never from the request's Host header. Otherwise anyone who could reach
    the API with a forged Host, or point a second hostname at the same Caddy,
    could make the server email *their* domain a live token.

    Bare, not workspace-prefixed: the recipient has no workspace yet, and the
    activation page is served by the SPA at the root.
    """
    return f"{origin.rstrip('/')}/invite?token={token}"


def is_expired(invite: dict[str, Any], *, now: datetime | None = None) -> bool:
    deadline = invite.get("expires_at")
    if not isinstance(deadline, datetime):
        return False
    return deadline <= (now or utcnow())


def display_status(invite: dict[str, Any], *, now: datetime | None = None) -> str:
    """`pending | expired | accepted | revoked` for the admin list.

    `expired` is computed here and nowhere stored, so the clock is the single
    authority. See the repository docstring.
    """
    status = str(invite.get("status") or "")
    if status == "pending" and is_expired(invite, now=now):
        return "expired"
    return status
