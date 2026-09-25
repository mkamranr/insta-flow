"""Our own copy of the post and story thumbnails.

Meta's `media_url` and `thumbnail_url` expire and cannot be renewed: re-fetching
returns a fresh URL string carrying the *same* `oe` (docs/meta-api.md §2d). So
the dashboard cannot render them directly and stay working. The bytes are copied
while the link is alive -- at sync time, which for a story is the only moment it
is ever alive -- and served from our own host afterwards.

Disk only. No Meta knowledge, no database: `services/meta/cdn.py` does the
fetching and the repositories record where a file landed.
"""

import hashlib
import logging
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MB = 1024 * 1024

# A sibling of the attachment folders under `upload_dir`, not a child of one.
# `attachments.FOLDER_PATTERN` is a UUID regex, so "media" can never be mistaken
# for an upload and `attachments.resolve` cannot be pointed at a cached image.
# Same trick as `.geoip`.
CACHE_SUBDIR = "media"

# Deliberately no image/svg+xml. These are served from our own origin under a CSP
# with `script-src 'self'`, so a cached SVG would be stored XSS with our own
# domain's privileges. Raster only, and the type is confirmed from the bytes in
# `cdn.sniff_content_type` before anything reaches here.
ALLOWED_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

MAX_IMAGE_BYTES = 8 * MB

# `<2-char shard>/<64-char sha256><extension>`.
KEY_PATTERN = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{64}\.(?:jpg|png|webp)$")


class MediaCacheError(ValueError):
    """Rejected before anything is written to disk."""


@dataclass(frozen=True)
class CachedImage:
    storage_key: str
    content_type: str
    digest: str
    size_bytes: int


def digest_for(instagram_account_id: object, media_id: str) -> str:
    """The cache key for one account's media.

    The account id is *in* the hash, so two workspaces holding the same media id
    cannot collide on disk or read each other's file. Hashing means a media id
    that arrives from Meta can never influence a path component.
    """
    return hashlib.sha256(f"{instagram_account_id}:{media_id}".encode()).hexdigest()


def storage_key(digest: str, content_type: str) -> str:
    extension = ALLOWED_TYPES.get(content_type)
    if extension is None:
        raise MediaCacheError(f"{content_type} is not a cacheable image type.")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise MediaCacheError("Digest is not a sha256 hex string.")
    # Sharded so a large account does not put 10K entries in one directory.
    return f"{digest[:2]}/{digest}{extension}"


def _root(upload_dir: Path) -> Path:
    return Path(upload_dir) / CACHE_SUBDIR


def store(upload_dir: Path, digest: str, data: bytes, content_type: str) -> CachedImage:
    """Write one image and return where it landed.

    Written to a temporary name and `os.replace`d into place. Attachments never
    needed this because each upload gets its own UUID directory; here the
    15-minute cron, an ARQ catch-up storm and a request-time fill can all target
    the same path at once, and a half-written file renders broken and is then
    cached by the browser for a year.
    """
    if not data:
        raise MediaCacheError("The image is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise MediaCacheError(
            f"Images are limited to {MAX_IMAGE_BYTES // MB}MB; this one is {len(data) / MB:.1f}MB."
        )

    key = storage_key(digest, content_type)
    target = _root(upload_dir) / key
    target.parent.mkdir(parents=True, exist_ok=True)

    temporary = target.with_name(f"{target.name}.tmp-{secrets.token_hex(6)}")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise

    logger.info("media_cached", extra={"storage_key": key, "bytes": len(data)})
    return CachedImage(
        storage_key=key, content_type=content_type, digest=digest, size_bytes=len(data)
    )


def path_for(upload_dir: Path, key: str | None) -> Path | None:
    """The file behind a storage key, or None if the key is not well-formed.

    Pattern-checked and then confirmed to sit inside the cache root, the same
    discipline as `attachments.resolve`, so a crafted `..` cannot walk out.
    """
    if not key or not KEY_PATTERN.match(key):
        return None
    root = _root(upload_dir)
    candidate = (root / key).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def delete(upload_dir: Path, key: str | None) -> bool:
    """Remove a cached image. Safe to call with a stale key."""
    target = path_for(upload_dir, key)
    if target is None:
        return False
    try:
        target.unlink()
    except OSError:
        return False
    logger.info("media_cache_deleted", extra={"storage_key": key})
    return True


def directory_bytes(upload_dir: Path) -> int:
    """Total size of the cache, for the prune job's ceiling."""
    root = _root(upload_dir)
    if not root.is_dir():
        return 0
    return sum(entry.stat().st_size for entry in root.rglob("*") if entry.is_file())
