"""Turning captured leads into a spreadsheet.

Kept out of the route so the awkward parts -- quoting, encoding, the fact that a
caption is hostile input -- are testable without HTTP, and so the per-post file
and the aggregated file cannot drift: they are the same writer over a different
slice of the same rows.
"""

import csv
import io
import re
from datetime import date, datetime
from typing import Any

Document = dict[str, Any]

HEADER = (
    "email",
    "instagram_user_id",
    "captured_at",
    "post_caption",
    "post_media_id",
    "automation",
)

# Excel and Sheets execute a cell that opens with any of these. Captions come
# from Instagram and automation names from an operator, so neither is trusted.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")

NO_POST = "none"
CAPTION_CHARS = 120


def caption_line(caption: str | None, limit: int = CAPTION_CHARS) -> str:
    """One line, trimmed. Mirrors `captionLine` in the frontend.

    Sliced by code point rather than by byte, so an emoji is never cut into half
    a surrogate pair -- captions are full of them.
    """
    collapsed = re.sub(r"\s+", " ", (caption or "")).strip()
    points = list(collapsed)
    if len(points) <= limit:
        return collapsed
    return "".join(points[:limit]).rstrip() + "…"


def _safe(value: Any) -> str:
    """A cell that a spreadsheet will display rather than execute."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_LEAD) else text


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return "" if value is None else str(value)


def rows_from_groups(groups: list[Document]) -> list[tuple[str, ...]]:
    """One row per (person, source). An address repeats across the posts that
    captured it, which is the point -- a post keeps the leads it earned."""
    rows: list[tuple[str, ...]] = []
    for group in groups:
        caption = caption_line((group.get("post") or {}).get("caption"))
        media_id = group.get("instagram_media_id") or ""
        for contact in group.get("contacts") or []:
            rows.append(
                (
                    _safe(contact.get("email")),
                    _safe(contact.get("instagram_user_id")),
                    _timestamp(contact.get("captured_at")),
                    _safe(caption),
                    _safe(media_id),
                    _safe(contact.get("rule_name")),
                )
            )
    return rows


def render(rows: list[tuple[str, ...]]) -> bytes:
    """RFC 4180 with a BOM.

    `csv.writer`'s default dialect already terminates lines with CRLF; do not
    "fix" that to `\\n`. The BOM is what stops Excel on Windows reading a
    downloaded UTF-8 file as cp1252 and mangling every emoji in a caption -- it
    ignores the HTTP charset entirely.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADER)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def filename(post: str | None, today: date) -> str:
    """ASCII only, and never built from a caption.

    A non-ASCII byte in `Content-Disposition` needs RFC 5987 encoding and breaks
    outright in some clients, so the post is identified by its media id.
    """
    if post == NO_POST:
        which = "-not-from-a-post"
    elif post:
        which = "-" + re.sub(r"[^A-Za-z0-9_-]", "", post)[:40]
    else:
        which = ""
    return f"social-flow-contacts{which}-{today.isoformat()}.csv"
