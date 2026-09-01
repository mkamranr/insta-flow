"""Trackable link tokens.

Button URLs are rewritten to point at our own redirect, so a click is
attributable. The token is random rather than derived from the target, so a
token cannot be guessed from a URL someone already knows.
"""

import secrets

TOKEN_BYTES = 8  # ~11 URL-safe characters; short enough not to dominate the DM.
REDIRECT_PREFIX = "/r/"


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def tracking_url(public_base_url: str, token: str) -> str:
    return f"{public_base_url.rstrip('/')}{REDIRECT_PREFIX}{token}"


def is_safe_target(url: str) -> bool:
    """Only ever redirect to http(s).

    An open redirect to `javascript:` or `data:` would turn our domain into an
    attack vector, and the URL comes from a form.
    """
    return url.lower().startswith(("https://", "http://"))
