"""The Instagram client contract.

Everything outside `services/meta/` depends on `InstagramClient`, never on a
concrete implementation. That is what lets `MockInstagramClient` stand in for the
whole Meta surface in tests, and it is why no other package may build a Meta URL
or read an access token.

Endpoint and permission details live in docs/meta-api.md.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.errors import MetaAPIError, MetaRateLimitError, MetaTokenError

# Fields Social Flow subscribes to. `messaging_postbacks` carries the button tap
# that opens the 24-hour window for the follow-up DM (docs/meta-api.md §4b).
DEFAULT_WEBHOOK_FIELDS = ("comments", "messaging_postbacks", "messages")


@dataclass(frozen=True)
class MetaAccount:
    instagram_user_id: str
    username: str
    account_type: str = "BUSINESS"
    name: str | None = None
    profile_picture_url: str | None = None
    # `followers_count` is plural on this edge. The Messaging User Profile edge
    # spells the same idea `follower_count`, and the singular form 400s here --
    # see docs/meta-api.md §2b. Meta returns a point in time, never a history.
    followers_count: int | None = None
    follows_count: int | None = None
    media_count: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetaMedia:
    instagram_media_id: str
    media_type: str | None = None
    media_product_type: str | None = None
    caption: str | None = None
    permalink: str | None = None
    # Meta returns `thumbnail_url` for VIDEO only; IMAGE and CAROUSEL_ALBUM
    # carry `media_url` instead. Verified live 2026-09-04 -- a card that reads
    # only `thumbnail_url` shows nothing for half the grid.
    thumbnail_url: str | None = None
    media_url: str | None = None
    published_at: datetime | None = None
    like_count: int | None = None
    comments_count: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetaUserProfile:
    """From the Messaging User Profile API. Backs "require follow before DM"."""

    instagram_user_id: str
    username: str = ""
    name: str | None = None
    profile_pic: str | None = None
    follower_count: int | None = None
    is_user_follow_business: bool | None = None
    is_business_follow_user: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetaSendResult:
    """Normalised success shape (§35). Meta's raw body is kept for the audit trail."""

    success: bool
    message_id: str | None = None
    recipient_id: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


def error_for_status(status: int, message: str = "", **kwargs: Any) -> MetaAPIError:
    """Map an HTTP status onto the right error type and retry semantics (§33, §64).

    Retryable: 429, 5xx, timeouts, network failures.
    Not retryable: every other 4xx. A 401 additionally pauses the account, because
    retrying with a dead token can only fail.
    """
    if status == 429:
        return MetaRateLimitError(
            message or "Meta rate limit exceeded.", status_code=status, **kwargs
        )
    if status == 401:
        return MetaTokenError(
            message or "Meta access token is invalid or expired.",
            status_code=status,
            retryable=False,
            **kwargs,
        )
    return MetaAPIError(
        message or f"Meta returned HTTP {status}.",
        status_code=status,
        retryable=status >= 500,
        **kwargs,
    )


@runtime_checkable
class InstagramClient(Protocol):
    """The complete set of Meta operations Social Flow performs."""

    async def get_account(self, access_token: str) -> MetaAccount:
        """Look up the Instagram professional account the token belongs to."""
        ...

    async def get_media(
        self, access_token: str, instagram_user_id: str, limit: int = 25
    ) -> list[MetaMedia]:
        """List recent media for campaign media-targeting."""
        ...

    async def get_stories(self, access_token: str, instagram_user_id: str) -> list[MetaMedia]:
        """Currently active stories. Meta returns only live ones, which is what
        makes expiry observable rather than something to compute."""
        ...

    async def subscribe_webhooks(
        self,
        access_token: str,
        instagram_user_id: str,
        fields: tuple[str, ...] = DEFAULT_WEBHOOK_FIELDS,
    ) -> bool: ...

    async def get_subscribed_fields(
        self, access_token: str, instagram_user_id: str
    ) -> list[str]: ...

    async def send_private_reply(
        self,
        access_token: str,
        instagram_user_id: str,
        comment_id: str,
        text: str,
        buttons: list[dict[str, str]] | None = None,
    ) -> MetaSendResult:
        """DM the author of a comment. One message only, within 7 days."""
        ...

    async def send_direct_message(
        self,
        access_token: str,
        instagram_user_id: str,
        recipient_id: str,
        text: str,
        buttons: list[dict[str, str]] | None = None,
    ) -> MetaSendResult:
        """Message an IGSID inside an open 24-hour window."""
        ...

    async def send_attachment(
        self,
        access_token: str,
        instagram_user_id: str,
        recipient_id: str,
        attachment_type: str,
        url: str,
    ) -> MetaSendResult:
        """Send a file by URL. Meta fetches it; we never upload bytes.

        Only valid to an IGSID inside an open window -- a private reply is
        text-only (docs/meta-api.md §4c).
        """
        ...

    async def send_public_reply(
        self, access_token: str, comment_id: str, text: str
    ) -> MetaSendResult:
        """Reply publicly under a comment (POST /{comment_id}/replies)."""
        ...

    async def get_user_profile(self, access_token: str, instagram_user_id: str) -> MetaUserProfile:
        """Profile for an IGSID, including whether they follow the business."""
        ...
