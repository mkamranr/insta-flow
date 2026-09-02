"""Meta webhook signature validation and payload parsing.

Every rule here is recorded in docs/meta-api.md §5, verified against Meta's
documentation. The parser sketch in project-plan.md §24 is wrong in ways that
would silently match nothing; §5.6 lists the four corrections, all implemented
below.
"""

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Hub-Signature-256"
SIGNATURE_PREFIX = "sha256="

COMMENTS_FIELD = "comments"
INSTAGRAM_OBJECT = "instagram"


def verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Validate `X-Hub-Signature-256` over the raw request body.

    Must run on the exact bytes received: re-serialising the parsed JSON produces
    a different digest even for an identical document.
    """
    if not signature_header or not app_secret:
        return False
    if not signature_header.startswith(SIGNATURE_PREFIX):
        return False

    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(SIGNATURE_PREFIX + expected, signature_header)


def sign(raw_body: bytes, app_secret: str) -> str:
    """Produce the header Meta would send. Used by tests and the webhook self-test."""
    digest = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return SIGNATURE_PREFIX + digest


def verify_subscription(
    mode: str | None, token: str | None, challenge: str | None, expected_token: str
) -> str | None:
    """Handle Meta's GET verification handshake (§5.1).

    Returns the challenge to echo, or None to reject.
    """
    if mode != "subscribe":
        return None
    if not expected_token or not token:
        return None
    if not hmac.compare_digest(token, expected_token):
        return None
    return challenge or ""


@dataclass(frozen=True)
class ParsedComment:
    """One comment event, normalised out of a webhook payload."""

    instagram_account_id: str
    instagram_comment_id: str
    instagram_user_id: str = ""
    username: str = ""
    text: str = ""
    instagram_media_id: str = ""
    media_product_type: str = ""
    parent_id: str = ""
    commented_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_self_comment(self) -> bool:
        """Did the connected account write this comment?

        The account's own comments and replies fire the same webhook, so without
        this Social Flow would trigger on its own replies (§5.6).
        """
        return bool(self.instagram_user_id) and (
            self.instagram_user_id == self.instagram_account_id
        )

    def is_from(self, account: dict[str, Any]) -> bool:
        """Fuller self-check against a connected account record.

        `from.id` is an Instagram-scoped id and does not always equal the account
        id, so username is checked too.
        """
        account_ig_id = str(account.get("instagram_user_id", ""))
        account_username = str(account.get("username", "")).lower()
        return bool(
            (self.instagram_user_id and self.instagram_user_id == account_ig_id)
            or (self.username and self.username.lower() == account_username)
        )


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def parse_comment_events(payload: dict[str, Any]) -> list[ParsedComment]:
    """Extract every comment event from a webhook payload.

    Tolerant by design: Meta omits optional keys, and a KeyError here would mean
    returning 500 and being redelivered forever.
    """
    if not isinstance(payload, dict):
        return []
    if payload.get("object") not in (INSTAGRAM_OBJECT, None):
        return []

    events: list[ParsedComment] = []

    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue

        account_id = _as_str(entry.get("id"))
        commented_at = _timestamp(entry.get("time"))

        # The field name lives at entry.changes[].field. project-plan.md §24
        # reads entry["field"], which never matches.
        for change in entry.get("changes") or []:
            if not isinstance(change, dict) or change.get("field") != COMMENTS_FIELD:
                continue

            value = change.get("value")
            if not isinstance(value, dict):
                continue

            # Meta's own docs disagree on the key: the reference page documents
            # `id`, the examples page `comment_id`. Accept either (§5.6).
            comment_id = _as_str(value.get("comment_id") or value.get("id"))
            if not comment_id:
                logger.warning("webhook_comment_without_id", extra={"account_id": account_id})
                continue

            raw_sender = value.get("from")
            raw_media = value.get("media")
            sender: dict[str, Any] = raw_sender if isinstance(raw_sender, dict) else {}
            media: dict[str, Any] = raw_media if isinstance(raw_media, dict) else {}

            events.append(
                ParsedComment(
                    instagram_account_id=account_id,
                    instagram_comment_id=comment_id,
                    instagram_user_id=_as_str(sender.get("id")),
                    username=_as_str(sender.get("username")),
                    text=_as_str(value.get("text")),
                    instagram_media_id=_as_str(media.get("id")),
                    media_product_type=_as_str(media.get("media_product_type")),
                    parent_id=_as_str(value.get("parent_id")),
                    commented_at=commented_at,
                    raw=value,
                )
            )

    return events


# Anything past this is milliseconds, not seconds. 1e11 seconds is the year
# 5138, so no real second-timestamp reaches it and no real millisecond-timestamp
# falls below it.
_MILLISECOND_THRESHOLD = 1e11


def _timestamp(value: Any) -> datetime | None:
    """Parse `entry.time` / `timestamp`.

    Meta documents comment events in seconds, but messaging events arrive in
    milliseconds — observed in live traffic. Guessing from magnitude is safe
    here because the two ranges cannot overlap for any plausible date.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if abs(number) >= _MILLISECOND_THRESHOLD:
        number /= 1000.0

    try:
        return datetime.fromtimestamp(number, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


# Payload carried on the opening DM's postback button. Namespaced so a payload
# from some other integration on the same account is ignored rather than
# misinterpreted.
POSTBACK_PREFIX = "instaflow"


def build_postback_payload(rule_id: str, comment_id: str) -> str:
    return f"{POSTBACK_PREFIX}:{rule_id}:{comment_id}"


@dataclass(frozen=True)
class ParsedPostback:
    """A button tap on a message we sent.

    The tap is user-initiated, which opens the 24-hour messaging window — that
    is what makes the follow-up DM permitted (docs/meta-api.md §4b).
    """

    instagram_account_id: str
    sender_id: str
    payload: str
    title: str = ""
    mid: str = ""
    sent_at: datetime | None = None

    @property
    def is_ours(self) -> bool:
        return self.payload.startswith(f"{POSTBACK_PREFIX}:")

    @property
    def rule_id(self) -> str:
        parts = self.payload.split(":")
        return parts[1] if len(parts) > 2 else ""

    @property
    def comment_id(self) -> str:
        parts = self.payload.split(":")
        return parts[2] if len(parts) > 2 else ""


def parse_postback_events(payload: dict[str, Any]) -> list[ParsedPostback]:
    """Extract button taps from a webhook payload.

    Messaging events arrive under `entry[].messaging[]`, not `entry[].changes[]`
    like comments do — a parser written for one shape sees nothing in the other.
    """
    if not isinstance(payload, dict):
        return []

    events: list[ParsedPostback] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        account_id = _as_str(entry.get("id"))

        for event in entry.get("messaging") or []:
            if not isinstance(event, dict):
                continue
            postback = event.get("postback")
            if not isinstance(postback, dict):
                continue
            raw_sender = event.get("sender")
            sender: dict[str, Any] = raw_sender if isinstance(raw_sender, dict) else {}

            events.append(
                ParsedPostback(
                    instagram_account_id=account_id,
                    sender_id=_as_str(sender.get("id")),
                    payload=_as_str(postback.get("payload")),
                    title=_as_str(postback.get("title")),
                    mid=_as_str(postback.get("mid")),
                    sent_at=_timestamp(event.get("timestamp", entry.get("time"))),
                )
            )
    return events


@dataclass(frozen=True)
class ParsedMessage:
    """An inbound DM from an Instagram user."""

    instagram_account_id: str
    sender_id: str
    text: str = ""
    mid: str = ""
    is_echo: bool = False
    sent_at: datetime | None = None


def parse_message_events(payload: dict[str, Any]) -> list[ParsedMessage]:
    """Extract inbound DMs. Echoes of our own sends are flagged, not dropped,
    so a caller can tell "no messages" from "only our own"."""
    if not isinstance(payload, dict):
        return []

    events: list[ParsedMessage] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        account_id = _as_str(entry.get("id"))

        for event in entry.get("messaging") or []:
            if not isinstance(event, dict):
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            raw_sender = event.get("sender")
            sender: dict[str, Any] = raw_sender if isinstance(raw_sender, dict) else {}

            events.append(
                ParsedMessage(
                    instagram_account_id=account_id,
                    sender_id=_as_str(sender.get("id")),
                    text=_as_str(message.get("text")),
                    mid=_as_str(message.get("mid")),
                    is_echo=bool(message.get("is_echo", False)),
                    sent_at=_timestamp(event.get("timestamp", entry.get("time"))),
                )
            )
    return events
