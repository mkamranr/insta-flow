"""Response models for connected Instagram accounts.

`InstagramAccountPublic` is the only shape the frontend ever sees. It has no
token field by construction, so a token cannot leak by forgetting to strip one.
"""

from datetime import UTC, datetime
from typing import Final, Literal

from pydantic import BaseModel, field_validator

# The longest Auto DM pause anyone can set. Deliberately longer than Meta's
# 7-day reply window -- a three-week break is a real thing to want -- so part of
# a long pause's backlog will be undeliverable, and the API says so rather than
# refusing the pause.
MAX_PAUSE_DAYS: Final = 30


class InstagramAccountPublic(BaseModel):
    id: str
    instagram_user_id: str
    username: str
    display_name: str | None = None
    account_type: str | None = None
    profile_picture_url: str | None = None
    permissions: list[str] = []
    status: str
    status_error: str | None = None
    webhook_subscribed: bool = False
    is_default: bool = False
    subscribed_fields: list[str] = []
    token_expires_at: datetime | None = None
    token_expired: bool = False
    automation_paused: bool = False
    automation_paused_until: datetime | None = None
    automation_paused_at: datetime | None = None
    last_webhook_at: datetime | None = None
    # Meta's own count, from `GET /me?fields=...media_count`. The authoritative
    # total, so the resync card can say "30 of 30" rather than only how many it
    # happened to fetch.
    media_count: int | None = None
    media_resync_state: str | None = None
    media_resync_started_at: datetime | None = None
    media_resync_finished_at: datetime | None = None
    media_resync_count: int | None = None
    media_resync_pages: int | None = None
    media_resync_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_document(
        cls, document: dict, *, token_expired: bool = False, automation_paused: bool = False
    ) -> "InstagramAccountPublic":
        return cls(
            id=str(document["_id"]),
            instagram_user_id=str(document.get("instagram_user_id", "")),
            username=str(document.get("username", "")),
            display_name=document.get("display_name"),
            account_type=document.get("account_type"),
            profile_picture_url=document.get("profile_picture_url"),
            permissions=list(document.get("permissions") or []),
            status=str(document.get("status", "disconnected")),
            status_error=document.get("status_error"),
            webhook_subscribed=bool(document.get("webhook_subscribed", False)),
            is_default=bool(document.get("is_default", False)),
            subscribed_fields=list(document.get("subscribed_fields") or []),
            token_expires_at=document.get("token_expires_at"),
            token_expired=token_expired,
            # Passed in rather than derived here, exactly like `token_expired`:
            # the model stays dumb and the route owns the clock.
            automation_paused=automation_paused,
            automation_paused_until=document.get("automation_paused_until"),
            automation_paused_at=document.get("automation_paused_at"),
            last_webhook_at=document.get("last_webhook_at"),
            media_count=document.get("media_count"),
            media_resync_state=document.get("media_resync_state"),
            media_resync_started_at=document.get("media_resync_started_at"),
            media_resync_finished_at=document.get("media_resync_finished_at"),
            media_resync_count=document.get("media_resync_count"),
            media_resync_pages=document.get("media_resync_pages"),
            media_resync_error=document.get("media_resync_error"),
            created_at=document.get("created_at"),
            updated_at=document.get("updated_at"),
        )


class PauseRequest(BaseModel):
    """When Auto DM should start sending again."""

    until: datetime

    @field_validator("until")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        """Coercion only. Whether the instant is sensible is the route's call, so
        the refusal comes back in the app's error shape rather than pydantic's."""
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class PauseBacklogAction(BaseModel):
    """What to do with the comments a pause held."""

    action: Literal["deliver", "ignore"]


class AccountPauseStatus(BaseModel):
    paused: bool
    paused_until: datetime | None = None
    paused_at: datetime | None = None
    held: int = 0
    deliverable: int = 0
