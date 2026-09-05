"""Meta OAuth for Instagram Login.

Every URL and parameter here is recorded in docs/meta-api.md §3, verified against
official documentation. Nothing outside this package may construct these URLs.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.errors import MetaAPIError, MetaTokenError
from app.services.meta.credentials import MetaCredentials

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"  # noqa: S105 - a URL, not a secret
GRAPH_HOST = "https://graph.instagram.com"

SCOPES = (
    "instagram_business_basic",
    "instagram_business_manage_comments",
    "instagram_business_manage_messages",
)

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    instagram_user_id: str = ""
    expires_at: datetime | None = None
    permissions: tuple[str, ...] = ()


def build_authorize_url(creds: MetaCredentials, state: str) -> str:
    """The URL the admin is redirected to in order to grant access (§3.1).

    Takes credentials rather than `Settings` because which app -- and therefore
    which redirect URI -- depends on the workspace, not the process.
    """
    params = httpx.QueryParams(
        {
            "client_id": creds.app_id,
            "redirect_uri": creds.redirect_uri,
            "response_type": "code",
            "scope": ",".join(SCOPES),
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{params}"


def _expires_at(payload: dict[str, Any]) -> datetime | None:
    try:
        seconds = int(payload["expires_in"])
    except (KeyError, TypeError, ValueError):
        return None
    return datetime.now(UTC) + timedelta(seconds=seconds)


def _raise_for_meta_error(response: httpx.Response, context: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:500]}

    if response.is_success and "error" not in body:
        return body

    error = body.get("error") or body
    message = error.get("error_message") or error.get("message") or f"{context} failed."
    error_class = MetaTokenError if response.status_code in (400, 401) else MetaAPIError
    raise error_class(
        message,
        status_code=response.status_code,
        error_code=error.get("code") or error.get("error_type"),
        raw_response=body,
    )


async def exchange_code(
    creds: MetaCredentials, code: str, *, client: httpx.AsyncClient | None = None
) -> TokenBundle:
    """Authorization code -> short-lived token (§3.2). The code is single-use."""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": creds.app_id,
                "client_secret": creds.app_secret,
                "grant_type": "authorization_code",
                # Must be byte-identical to the one used at authorize time, and
                # to one registered on this app. Both come from `creds`, so they
                # cannot drift apart per workspace.
                "redirect_uri": creds.redirect_uri,
                "code": code,
            },
        )
        body = _raise_for_meta_error(response, "Authorization code exchange")
    finally:
        if owns_client:
            await client.aclose()

    permissions = body.get("permissions") or ()
    if isinstance(permissions, str):
        permissions = tuple(p.strip() for p in permissions.split(",") if p.strip())

    return TokenBundle(
        access_token=str(body.get("access_token", "")),
        instagram_user_id=str(body.get("user_id", "")),
        permissions=tuple(permissions),
    )


async def exchange_for_long_lived(
    creds: MetaCredentials, short_lived_token: str, *, client: httpx.AsyncClient | None = None
) -> TokenBundle:
    """Short-lived -> long-lived token, valid 60 days (§3.3)."""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        response = await client.get(
            f"{GRAPH_HOST}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": creds.app_secret,
                "access_token": short_lived_token,
            },
        )
        body = _raise_for_meta_error(response, "Long-lived token exchange")
    finally:
        if owns_client:
            await client.aclose()

    return TokenBundle(access_token=str(body.get("access_token", "")), expires_at=_expires_at(body))


async def refresh_long_lived(
    token: str, *, client: httpx.AsyncClient | None = None
) -> TokenBundle:
    """Refresh a long-lived token (§3.4).

    Meta requires the token to be at least 24 hours old, and a token not
    refreshed within 60 days is dead — the account must reconnect.

    Takes no credentials, and never did: `ig_refresh_token` authenticates with
    the token itself. It previously accepted a `settings` argument it never read,
    which would now read as "this call needs the workspace's app" and be wrong --
    token refresh works identically whichever app minted the token.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    try:
        response = await client.get(
            f"{GRAPH_HOST}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
        )
        body = _raise_for_meta_error(response, "Token refresh")
    finally:
        if owns_client:
            await client.aclose()

    return TokenBundle(access_token=str(body.get("access_token", "")), expires_at=_expires_at(body))
