"""In-memory Instagram client for development and tests.

Implements the full `InstagramClient` protocol without any network access, so the
whole pipeline can be exercised without Meta credentials and CI never touches a
live account (§55, §77).

It deliberately enforces Meta's real restrictions rather than always succeeding:
a second private reply to the same comment raises, exactly as Meta would. A mock
that happily accepts duplicates would let a double-send bug pass every test.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.constants import PRIVATE_REPLY_WINDOW_DAYS
from app.errors import MetaAPIError
from app.services.meta.cdn import MediaBytes
from app.services.meta.client import (
    DEFAULT_WEBHOOK_FIELDS,
    MetaAccount,
    MetaMedia,
    MetaSendResult,
    MetaUserProfile,
    error_for_status,
)

# The smallest thing `cdn.sniff_content_type` accepts as a JPEG: the SOI marker
# plus enough of a header to be non-empty. Tests assert on the bytes, so it only
# has to be stable and recognisable.
DEFAULT_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 8

# Every failure mode §77 requires the mock to simulate.
SIMULATED_STATUSES = (400, 401, 403, 404, 429, 500)


class SimulatedTimeout(MetaAPIError):
    """Meta did not respond in time. Retryable."""

    code = "meta_timeout"

    def __init__(self, message: str = "Meta request timed out.") -> None:
        super().__init__(message, status_code=None, retryable=True)


class SimulatedNetworkError(MetaAPIError):
    """The request never reached Meta. Retryable."""

    code = "meta_network_error"

    def __init__(self, message: str = "Could not reach Meta.") -> None:
        super().__init__(message, status_code=None, retryable=True)


@dataclass
class MockCall:
    method: str
    kwargs: dict[str, Any] = field(default_factory=dict)


class MockInstagramClient:
    """A scriptable stand-in for the real client.

    Failures are queued per method and consumed one per call, so a test can say
    "fail twice with 429, then succeed" and assert the retry path.
    """

    MUTATIONS = (
        "send_private_reply",
        "send_direct_message",
        "send_attachment",
        "send_public_reply",
        "subscribe_webhooks",
    )

    def __init__(
        self,
        *,
        account: MetaAccount | None = None,
        media: list[MetaMedia] | None = None,
    ) -> None:
        self.account = account or MetaAccount(
            instagram_user_id="17841400000000000",
            username="example",
            account_type="BUSINESS",
            name="Example Account",
        )
        self.media = media if media is not None else [_default_media()]
        # Empty by default: most tests have no stories, and "no active stories"
        # is the state a story rule must survive.
        self.stories: list[MetaMedia] = []
        # Scripted image bodies, keyed by URL; anything else gets DEFAULT_JPEG.
        self.media_bytes: dict[str, bytes] = {}
        self.calls: list[MockCall] = []
        self.sent_private_replies: list[dict[str, Any]] = []
        self.sent_direct_messages: list[dict[str, Any]] = []
        self.sent_public_replies: list[dict[str, Any]] = []
        self.sent_attachments: list[dict[str, Any]] = []
        self.profile = MetaUserProfile(
            instagram_user_id="17800000000000000",
            username="john123",
            is_user_follow_business=True,
        )
        self.subscribed_fields: list[str] = []
        self._failures: dict[str, list[Exception]] = defaultdict(list)
        self._replied_comment_ids: set[str] = set()
        self._comment_ages: dict[str, datetime] = {}

    # -- scripting ---------------------------------------------------------

    def fail(self, method: str, error: Exception | int, times: int = 1) -> "MockInstagramClient":
        """Queue `times` failures for `method`. An int is treated as an HTTP status."""
        for _ in range(times):
            self._failures[method].append(
                error_for_status(error) if isinstance(error, int) else error
            )
        return self

    def set_comment_age(self, comment_id: str, days_old: float) -> None:
        """Age a comment so the 7-day private-reply window can be tested."""
        self._comment_ages[comment_id] = datetime.now(UTC) - timedelta(days=days_old)

    # -- assertions --------------------------------------------------------

    @property
    def mutation_calls(self) -> list[MockCall]:
        """Every call that would change something at Meta.

        Dry-run tests assert this stays empty (§36, Phase 12).
        """
        return [call for call in self.calls if call.method in self.MUTATIONS]

    def calls_to(self, method: str) -> list[MockCall]:
        return [call for call in self.calls if call.method == method]

    # -- internals ---------------------------------------------------------

    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append(MockCall(method=method, kwargs=kwargs))
        queued = self._failures.get(method)
        if queued:
            raise queued.pop(0)

    # -- InstagramClient ---------------------------------------------------

    async def get_account(self, access_token: str) -> MetaAccount:
        self._record("get_account", access_token=access_token)
        return self.account

    async def get_media(
        self, access_token: str, instagram_user_id: str, limit: int = 25, max_pages: int = 1
    ) -> list[MetaMedia]:
        """Walks `self.media` in `limit`-sized slices, one `_record` per page.

        Recording per page rather than per call is the point: a test asserts
        `len(client.calls_to("get_media"))` to prove how many requests a sync
        made, which is the only way to catch a change that silently turns every
        Refresh into a full walk.
        """
        collected: list[MetaMedia] = []
        for page in range(max(1, max_pages)):
            offset = page * limit
            self._record(
                "get_media",
                instagram_user_id=instagram_user_id,
                limit=limit,
                after=str(offset) if offset else None,
            )
            batch = self.media[offset : offset + limit]
            collected.extend(batch)
            # Short page means Meta returned no `next` -- the real client stops
            # on the same condition.
            if len(batch) < limit:
                break
        return collected

    async def get_stories(self, access_token: str, instagram_user_id: str) -> list[MetaMedia]:
        self._record("get_stories", instagram_user_id=instagram_user_id)
        return self.stories

    async def fetch_media_bytes(self, url: str) -> MediaBytes:
        """Serve scripted bytes for a URL, falling back to a one-pixel JPEG.

        Not in `MUTATIONS`: downloading an image changes nothing at Meta, so
        DRY_RUN must not suppress it. That matters more than it looks -- a story
        URL is only alive for a few hours, and a dry run that skipped the fetch
        would lose the only chance to keep the picture.
        """
        self._record("fetch_media_bytes", url=url)
        return MediaBytes(data=self.media_bytes.get(url, DEFAULT_JPEG), content_type="image/jpeg")

    async def subscribe_webhooks(
        self,
        access_token: str,
        instagram_user_id: str,
        fields: tuple[str, ...] = DEFAULT_WEBHOOK_FIELDS,
    ) -> bool:
        self._record("subscribe_webhooks", instagram_user_id=instagram_user_id, fields=fields)
        self.subscribed_fields = list(fields)
        return True

    async def get_subscribed_fields(self, access_token: str, instagram_user_id: str) -> list[str]:
        self._record("get_subscribed_fields", instagram_user_id=instagram_user_id)
        return list(self.subscribed_fields)

    async def send_private_reply(
        self,
        access_token: str,
        instagram_user_id: str,
        comment_id: str,
        text: str,
        buttons: list[dict[str, str]] | None = None,
    ) -> MetaSendResult:
        self._record(
            "send_private_reply",
            instagram_user_id=instagram_user_id,
            comment_id=comment_id,
            text=text,
            buttons=buttons or [],
        )

        # Meta's real restrictions, enforced so bugs surface in tests.
        if comment_id in self._replied_comment_ids:
            raise MetaAPIError(
                "A private reply has already been sent for this comment.",
                status_code=400,
                error_code=10,
                retryable=False,
            )
        commented_at = self._comment_ages.get(comment_id)
        if commented_at and datetime.now(UTC) - commented_at > timedelta(
            days=PRIVATE_REPLY_WINDOW_DAYS
        ):
            raise MetaAPIError(
                f"Comment is older than the {PRIVATE_REPLY_WINDOW_DAYS}-day private "
                "reply window.",
                status_code=400,
                error_code=10,
                retryable=False,
            )

        self._replied_comment_ids.add(comment_id)
        index = len(self.sent_private_replies) + 1
        result = MetaSendResult(
            success=True,
            message_id=f"aWdfZAG1lc3NhZ2U6mock{index}",
            recipient_id=f"5261{index:012d}",
            raw_response={"recipient_id": f"5261{index:012d}", "message_id": f"mock{index}"},
        )
        self.sent_private_replies.append(
            {"comment_id": comment_id, "text": text, "buttons": buttons or [], "result": result}
        )
        return result

    async def send_direct_message(
        self,
        access_token: str,
        instagram_user_id: str,
        recipient_id: str,
        text: str,
        buttons: list[dict[str, str]] | None = None,
    ) -> MetaSendResult:
        self._record(
            "send_direct_message",
            recipient_id=recipient_id,
            text=text,
            buttons=buttons or [],
        )
        index = len(self.sent_direct_messages) + 1
        result = MetaSendResult(
            success=True,
            message_id=f"aWdfZAG1lc3NhZ2U6ZG0{index}",
            recipient_id=recipient_id,
            raw_response={"recipient_id": recipient_id, "message_id": f"dm{index}"},
        )
        self.sent_direct_messages.append(
            {"recipient_id": recipient_id, "text": text, "buttons": buttons or [], "result": result}
        )
        return result

    async def get_user_profile(self, access_token: str, instagram_user_id: str) -> MetaUserProfile:
        self._record("get_user_profile", instagram_user_id=instagram_user_id)
        return self.profile

    async def send_attachment(
        self,
        access_token: str,
        instagram_user_id: str,
        recipient_id: str,
        attachment_type: str,
        url: str,
    ) -> MetaSendResult:
        self._record(
            "send_attachment",
            recipient_id=recipient_id,
            attachment_type=attachment_type,
            url=url,
        )
        result = MetaSendResult(success=True, message_id=f"attach_{len(self.sent_attachments)}")
        self.sent_attachments.append(
            {"recipient_id": recipient_id, "type": attachment_type, "url": url}
        )
        return result

    async def send_public_reply(
        self, access_token: str, comment_id: str, text: str
    ) -> MetaSendResult:
        self._record("send_public_reply", comment_id=comment_id, text=text)
        index = len(self.sent_public_replies) + 1
        result = MetaSendResult(
            success=True,
            message_id=f"17900000000{index:06d}",
            raw_response={"id": f"17900000000{index:06d}"},
        )
        self.sent_public_replies.append({"comment_id": comment_id, "text": text, "result": result})
        return result


def _default_media() -> MetaMedia:
    return MetaMedia(
        instagram_media_id="17890000000000000",
        media_type="VIDEO",
        media_product_type="REELS",
        caption="5 AI tools every developer should know",
        permalink="https://www.instagram.com/reel/mock/",
        published_at=datetime.now(UTC) - timedelta(days=1),
    )
