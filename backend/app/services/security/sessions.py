"""Signed session cookies.

The cookie carries only a user id and a session epoch — never a token, a hash,
or any personal data. It is signed with `SESSION_SECRET` and carries its own
timestamp, so expiry is enforced on the server at verify time.

Logout increments the user's `session_epoch`, which invalidates every cookie
already issued to that user. That gives real server-side revocation without a
sessions collection, keeping the schema at the 11 collections in §7-§17.
"""

from typing import Any, NamedTuple

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SALT = "instaflow-session-v1"


class SessionData(NamedTuple):
    user_id: str
    epoch: int


def _serializer(secret: str) -> URLSafeTimedSerializer:
    if not secret:
        raise RuntimeError("SESSION_SECRET is not configured")
    return URLSafeTimedSerializer(secret, salt=SALT)


def issue(secret: str, user_id: str, epoch: int) -> str:
    return _serializer(secret).dumps({"uid": str(user_id), "epoch": int(epoch)})


def verify(secret: str, cookie: str, max_age_seconds: int) -> SessionData | None:
    """Return the session, or None if it is missing, tampered with, or expired."""
    if not cookie:
        return None
    try:
        payload: Any = _serializer(secret).loads(cookie, max_age=max_age_seconds)
    except (SignatureExpired, BadSignature):
        return None
    if not isinstance(payload, dict) or "uid" not in payload:
        return None
    try:
        return SessionData(user_id=str(payload["uid"]), epoch=int(payload.get("epoch", 0)))
    except (TypeError, ValueError):
        return None
