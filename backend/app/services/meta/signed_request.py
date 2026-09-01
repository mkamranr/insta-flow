"""Meta's `signed_request` payload.

Sent to the deauthorize and data-deletion callbacks. It is the only evidence
that such a request genuinely came from Meta, so acting on an unverified one
would let anyone disconnect an account by POSTing to a public URL.

Format: `<base64url signature>.<base64url json payload>`, where the signature is
HMAC-SHA256 of the *encoded* payload keyed by the app secret.
"""

import base64
import hashlib
import hmac
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _b64url_decode(value: str) -> bytes:
    # Meta strips the padding; restore it before decoding.
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def parse(signed_request: str, app_secret: str) -> dict[str, Any] | None:
    """Verify and decode. Returns the payload, or None if it is not genuine."""
    if not signed_request or not app_secret or "." not in signed_request:
        return None

    encoded_signature, _, encoded_payload = signed_request.partition(".")

    try:
        signature = _b64url_decode(encoded_signature)
        payload = json.loads(_b64url_decode(encoded_payload))
    except (ValueError, TypeError, json.JSONDecodeError):
        logger.warning("signed_request_unparseable")
        return None

    expected = hmac.new(app_secret.encode(), encoded_payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        logger.warning("signed_request_bad_signature")
        return None

    if not isinstance(payload, dict):
        return None

    # Meta has only ever used HMAC-SHA256 here, but the field exists precisely so
    # an unexpected algorithm can be rejected rather than assumed.
    algorithm = str(payload.get("algorithm", "")).upper().replace("-", "")
    if algorithm and algorithm != "HMACSHA256":
        logger.warning("signed_request_unexpected_algorithm", extra={"algorithm": algorithm})
        return None

    return payload
