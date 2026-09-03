"""Response models for connected Instagram accounts.

`InstagramAccountPublic` is the only shape the frontend ever sees. It has no
token field by construction, so a token cannot leak by forgetting to strip one.
"""

from datetime import datetime

from pydantic import BaseModel


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
    subscribed_fields: list[str] = []
    token_expires_at: datetime | None = None
    token_expired: bool = False
    last_webhook_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_document(
        cls, document: dict, *, token_expired: bool = False
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
            subscribed_fields=list(document.get("subscribed_fields") or []),
            token_expires_at=document.get("token_expires_at"),
            token_expired=token_expired,
            last_webhook_at=document.get("last_webhook_at"),
            created_at=document.get("created_at"),
            updated_at=document.get("updated_at"),
        )
