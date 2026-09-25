"""Fetching the signed CDN links Meta puts in `media_url` and `thumbnail_url`.

These are **not** stable addresses. They carry `&oe=<hex unix ts>` and stop
working when it passes, with `403 URL signature expired` (docs/meta-api.md §2d).
Re-fetching `/me/media` hands back a different URL string with the *same* `oe`,
so the expiry belongs to the media object, not to the request — the bytes have
to be copied while the link is alive.

Deliberately separate from `MetaInstagramClient._request`: that builds its URL
from a *path* and parses JSON, so an absolute URL through it would produce
`…/v25.0/https://…` — the same trap §2c records for `paging.next`.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from app.errors import MediaUnavailableError, MetaAPIError

logger = logging.getLogger(__name__)

# Meta serves media from these two, and nothing else may be fetched here. This
# is an SSRF control rather than tidiness: the URL is read out of MongoDB and
# the fetch runs inside the compose network, next to `mongodb:27017` and
# `redis:6379`. An allowlist of suffixes is the only thing standing between a
# tampered row and a request to a neighbour service or a cloud metadata address.
ALLOWED_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")

# Generous enough for a full-resolution photo, small enough that a surprise
# never buffers into memory. Matches the attachment image cap.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Short: this runs inside the 15-minute cron and, on a cache miss, inside a
# request. `job_timeout` is class-wide at 60s, so a slow CDN must fail long
# before it can take the whole job down with no exception to classify.
FETCH_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# Meta signs with `oe` in lowercase hex. Absent on some URLs, which is why
# `expires_at` returns None rather than treating it as already expired.
_OE = re.compile(r"[?&]oe=([0-9A-Fa-f]+)")

# Checked against the first bytes on the wire, not just the declared type. A CDN
# that answers with an HTML error page still sets a status and a content-type;
# without this it would land on disk labelled as an image.
MAGIC_BYTES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


@dataclass(frozen=True)
class MediaBytes:
    """One downloaded image, ready to be written to the cache."""

    data: bytes
    content_type: str


def is_media_cdn_url(url: str | None) -> bool:
    """Whether this is an https URL on a host Meta actually serves media from."""
    if not url:
        return False
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    host = parts.hostname.lower()
    return any(
        host == suffix.lstrip(".") or host.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES
    )


def expires_at(url: str | None) -> datetime | None:
    """When the signature stops working, or None if the URL carries no `oe`.

    None means "unknown", never "expired" — a URL without the parameter is left
    alone rather than being refetched on every pass.
    """
    if not url:
        return None
    found = _OE.search(url)
    if not found:
        return None
    try:
        return datetime.fromtimestamp(int(found.group(1), 16), tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def is_expired(url: str | None, *, now: datetime | None = None) -> bool:
    """True only when the URL says so. An unparseable `oe` is not expired."""
    deadline = expires_at(url)
    return deadline is not None and deadline <= (now or datetime.now(UTC))


def sniff_content_type(data: bytes) -> str | None:
    """The type the bytes actually are, or None if they are not an image we keep.

    WebP needs both halves of its container header: `RIFF....WEBP`.
    """
    for prefix, content_type in MAGIC_BYTES:
        if data.startswith(prefix):
            return content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def fetch(client: httpx.AsyncClient, url: str) -> MediaBytes:
    """Download one image, or raise.

    `MediaUnavailableError` means the link is dead for good and the caller must
    record that rather than trying again; everything else keeps the usual
    retryable/not semantics so a flaky CDN is picked up on the next pass.
    """
    if not is_media_cdn_url(url):
        raise MediaUnavailableError("Not an Instagram media URL.", status_code=None)

    try:
        response = await client.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True)
    except httpx.TimeoutException as exc:
        raise MetaAPIError("Media fetch timed out.", status_code=None, retryable=True) from exc
    except httpx.RequestError as exc:
        raise MetaAPIError(
            f"Could not reach Meta's CDN: {type(exc).__name__}", status_code=None, retryable=True
        ) from exc

    if response.status_code in (403, 404, 410):
        # 403 is the expiry: the body reads "URL signature expired".
        raise MediaUnavailableError(
            f"Media URL is no longer valid (HTTP {response.status_code}).",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise MetaAPIError(
            f"Media fetch returned HTTP {response.status_code}.",
            status_code=response.status_code,
            retryable=response.status_code >= 500,
        )

    data = response.content
    if len(data) > MAX_IMAGE_BYTES:
        cap = MAX_IMAGE_BYTES // (1024 * 1024)
        raise MediaUnavailableError(
            f"Image is {len(data) // (1024 * 1024)}MB, over the {cap}MB cap.", status_code=None
        )

    content_type = sniff_content_type(data)
    if content_type is None:
        # Not retryable: the same URL will keep returning the same non-image.
        raise MediaUnavailableError("Response was not a JPEG, PNG or WebP.", status_code=None)

    return MediaBytes(data=data, content_type=content_type)
