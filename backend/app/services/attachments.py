"""Uploaded media that Meta fetches by URL.

Meta does not accept file bytes on the messaging endpoint. It accepts a URL and
fetches it itself (docs/meta-api.md §4c), so anything we send has to be served
publicly. Files therefore live under a per-upload UUID directory and are served
unauthenticated: the UUID is the only thing standing between the file and the
world, which is the same trade every hosted automation tool makes.

Limits and formats below are Meta's, not ours. Uploading something it will
refuse to fetch produces a message that silently never arrives, so the check
happens here rather than at send time.
"""

import logging
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

MB = 1024 * 1024


@dataclass(frozen=True)
class MediaKind:
    """One of Meta's four attachment types."""

    attachment_type: str
    extensions: frozenset[str]
    max_bytes: int


MEDIA_KINDS: tuple[MediaKind, ...] = (
    MediaKind("image", frozenset({".png", ".jpg", ".jpeg"}), 8 * MB),
    MediaKind("video", frozenset({".mp4", ".ogg", ".avi", ".mov", ".webm"}), 25 * MB),
    MediaKind("audio", frozenset({".aac", ".m4a", ".wav"}), 25 * MB),
    MediaKind("file", frozenset({".pdf"}), 25 * MB),
)

# .mp4 is legal as both video and audio. Video wins: it is what a sender almost
# always means, and Meta renders an audio-only mp4 in a video bubble anyway.
_BY_EXTENSION: dict[str, MediaKind] = {}
for kind in MEDIA_KINDS:
    for extension in kind.extensions:
        _BY_EXTENSION.setdefault(extension, kind)

SAFE_NAME = re.compile(r"[^a-z0-9]+")
FOLDER_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
FILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}-[A-Za-z0-9]{10}\.[a-z0-9]{2,5}$")


class AttachmentError(ValueError):
    """Rejected before anything is written to disk."""


@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    filename: str
    attachment_type: str
    size_bytes: int


def kind_for(filename: str) -> MediaKind:
    extension = Path(filename).suffix.lower()
    kind = _BY_EXTENSION.get(extension)
    if kind is None:
        allowed = ", ".join(sorted(_BY_EXTENSION))
        raise AttachmentError(
            f"{extension or 'that file type'} is not supported. Allowed: {allowed}"
        )
    return kind


def _slug(filename: str) -> str:
    stem = SAFE_NAME.sub("-", Path(filename).stem.lower()).strip("-")
    return (stem or "file")[:60]


def store(root: Path, filename: str, data: bytes) -> StoredFile:
    """Write the upload and return where it landed.

    Validation happens first so a rejected file leaves no directory behind.
    """
    kind = kind_for(filename)
    if not data:
        raise AttachmentError("The file is empty.")
    if len(data) > kind.max_bytes:
        raise AttachmentError(
            f"{kind.attachment_type} attachments are limited to "
            f"{kind.max_bytes // MB}MB; this one is {len(data) / MB:.1f}MB."
        )

    folder = str(uuid4())
    # Hex, not token_urlsafe: the base64url alphabet includes "-" and "_", which
    # FILE_PATTERN rejects -- so roughly a third of uploads would store fine and
    # then 404 on the public route Meta fetches from.
    safe = f"{_slug(filename)}-{secrets.token_hex(5)}{Path(filename).suffix.lower()}"

    target = root / folder
    target.mkdir(parents=True, exist_ok=True)
    (target / safe).write_bytes(data)

    logger.info(
        "attachment_stored",
        extra={"storage_key": f"{folder}/{safe}", "bytes": len(data), "type": kind.attachment_type},
    )
    return StoredFile(
        storage_key=f"{folder}/{safe}",
        filename=safe,
        attachment_type=kind.attachment_type,
        size_bytes=len(data),
    )


def resolve(root: Path, folder: str, filename: str) -> Path | None:
    """The path for a public download, or None if the request is not well-formed.

    Both segments are pattern-checked and the result is confirmed to sit inside
    the upload root, so a crafted `..` cannot walk out of it.
    """
    if not FOLDER_PATTERN.match(folder) or not FILE_PATTERN.match(filename):
        return None
    candidate = (root / folder / filename).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def delete(root: Path, storage_key: str | None) -> bool:
    """Remove a stored file and its folder. Safe to call with a stale key.

    Called when a rule is deleted and when its attachment is replaced -- an
    orphaned 25MB PDF nobody can reach is pure waste.
    """
    if not storage_key or "/" not in storage_key:
        return False
    folder, _, filename = storage_key.partition("/")
    if not FOLDER_PATTERN.match(folder) or not FILE_PATTERN.match(filename):
        return False
    target = (root / folder).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return False
    if not target.is_dir():
        return False
    shutil.rmtree(target, ignore_errors=True)
    logger.info("attachment_deleted", extra={"storage_key": storage_key})
    return True
