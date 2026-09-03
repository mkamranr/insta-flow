"""Attachment upload, and the public route Meta fetches from.

Two routers with deliberately different exposure. `/api/uploads` needs a signed-in
user; `/files/...` needs to be reachable by Meta's fetchers, which carry no
credentials of ours, so it is unauthenticated by necessity. The UUID directory is
what keeps a file unlisted -- treat anything uploaded here as world-readable to
anyone holding the URL.
"""

import logging
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser, SettingsDep
from app.errors import NotFoundError, ValidationError
from app.services.attachments import MEDIA_KINDS, AttachmentError, resolve, store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
public_router = APIRouter(prefix="/files", tags=["uploads"])

# Read at most one byte past the largest thing Meta will accept, so an oversized
# upload is refused without pulling it all into memory first.
_READ_CEILING = max(kind.max_bytes for kind in MEDIA_KINDS) + 1

# FastAPI wants the marker as a default; hoisted so it is not a call in a
# signature default (ruff B008).
_FILE_FIELD = File(...)


def upload_root(settings: Any) -> Path:
    root = Path(settings.upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.get("/limits")
async def limits(user: CurrentUser) -> dict[str, Any]:
    """What the picker should accept. Meta's numbers, surfaced to the UI."""
    return {
        "kinds": [
            {
                "type": kind.attachment_type,
                "extensions": sorted(kind.extensions),
                "max_bytes": kind.max_bytes,
            }
            for kind in MEDIA_KINDS
        ]
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(
    user: CurrentUser,
    settings: SettingsDep,
    file: UploadFile = _FILE_FIELD,
) -> dict[str, Any]:
    data = await file.read(_READ_CEILING)
    try:
        stored = store(upload_root(settings), file.filename or "upload", data)
    except AttachmentError as exc:
        raise ValidationError(str(exc)) from exc

    return {
        "type": stored.attachment_type,
        "storage_key": stored.storage_key,
        "filename": stored.filename,
        "size_bytes": stored.size_bytes,
        # Absolute, because Meta fetches this from its own servers.
        "url": f"{settings.public_base_url.rstrip('/')}/files/{stored.storage_key}",
    }


@public_router.get("/{folder}/{filename}")
async def download(folder: str, filename: str, settings: SettingsDep) -> FileResponse:
    """Serve an uploaded file. No auth: Meta has to be able to fetch it."""
    path = resolve(upload_root(settings), folder, filename)
    if path is None:
        raise NotFoundError("File not found.")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        # Immutable: the random suffix means a given URL never changes content.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
