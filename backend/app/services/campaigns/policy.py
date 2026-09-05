"""Policy engine (§30).

The last gate before any Meta mutation. Pure: the caller gathers the facts, this
decides. Every decision carries a machine-readable reason, stored on the
execution so a skipped comment can be explained months later without guessing.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.constants import MESSAGE_REPLY_WINDOW_HOURS, PRIVATE_REPLY_WINDOW_DAYS

ACTION_PRIVATE_REPLY = "private_reply"
ACTION_PUBLIC_REPLY = "public_reply"
# Story replies and inbound DMs both send an ordinary DM, so they share one
# action. `action` names the Meta operation performed, not the reason for it --
# the reason lives in the `trigger` field on the execution.
ACTION_DIRECT_MESSAGE = "direct_message"

# Decision reasons. Stored verbatim on the execution record.
ALLOWED = "allowed"
DRY_RUN = "dry_run"
RULE_DISABLED = "rule_disabled"
ACCOUNT_NOT_CONNECTED = "account_not_connected"
AUTOMATION_PAUSED = "automation_paused"
NO_MESSAGE = "no_message"
OUTSIDE_REPLY_WINDOW = "outside_reply_window"
OUTSIDE_MESSAGE_WINDOW = "outside_message_window"
ALREADY_MESSAGED = "already_messaged"
NOT_FOLLOWING = "not_following"
RATE_LIMITED_MINUTE = "rate_limited_minute"
RATE_LIMITED_HOUR = "rate_limited_hour"
RATE_LIMITED_DAY = "rate_limited_day"


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    detail: str = ""

    @property
    def is_dry_run(self) -> bool:
        return self.reason == DRY_RUN


@dataclass(frozen=True)
class PolicyContext:
    """Facts the caller must gather before a decision can be made."""

    rule: dict[str, Any]
    account: dict[str, Any]
    action: str = ACTION_PRIVATE_REPLY
    commented_at: datetime | None = None
    # When the person messaged us. Separate from `commented_at` on purpose:
    # reusing that for a message timestamp reads as a bug to the next person.
    message_sent_at: datetime | None = None
    dry_run: bool = True
    sent_last_minute: int = 0
    sent_last_hour: int = 0
    sent_last_day: int = 0
    already_messaged: bool = False
    is_following: bool | None = None
    now: datetime | None = None


def within_message_window(sent_at: datetime | None, now: datetime | None = None) -> bool:
    """Is the person's own message still inside Meta's 24-hour reply window?

    Checked before the call rather than by catching Meta's rejection. The case
    this really catches is the worker being down and then draining a stale
    queue. Absent timestamp fails open, matching `within_reply_window`.
    """
    if sent_at is None:
        return True
    reference = now or datetime.now(UTC)
    return reference - sent_at <= timedelta(hours=MESSAGE_REPLY_WINDOW_HOURS)


def within_reply_window(commented_at: datetime | None, now: datetime | None = None) -> bool:
    """Is the comment still inside Meta's 7-day private-reply window?

    Checked before the call, not by catching Meta's rejection: a request we know
    will fail wastes one of the 750/hour budget and muddies the logs. An unknown
    timestamp is treated as eligible — the reservation still prevents duplicates,
    and Meta remains the final authority.
    """
    if commented_at is None:
        return True
    now = now or datetime.now(UTC)
    if commented_at.tzinfo is None:
        commented_at = commented_at.replace(tzinfo=UTC)
    return now - commented_at <= timedelta(days=PRIVATE_REPLY_WINDOW_DAYS)


def paused_until(account: dict[str, Any]) -> datetime | None:
    """When this account's Auto DM pause ends, or None if it was never paused.

    Mongo can hand back a naive datetime, and comparing one would raise, so an
    absent tzinfo is read as UTC -- the same coercion `within_reply_window` does.
    """
    until = account.get("automation_paused_until")
    if not isinstance(until, datetime):
        return None
    return until.replace(tzinfo=UTC) if until.tzinfo is None else until


def pause_active(account: dict[str, Any], now: datetime | None = None) -> bool:
    """Is every automated send for this account being held right now?

    Strictly `now < until`, so cancelling -- which writes `until = now` rather
    than unsetting it -- is already over by the time anyone reads it. That is
    what makes a cancelled pause and one that lapsed on its own the same state,
    with no job to run at expiry.
    """
    until = paused_until(account)
    return until is not None and (now or datetime.now(UTC)) < until


def evaluate(context: PolicyContext) -> PolicyDecision:
    """Decide whether `action` may be performed. Cheapest, most definitive checks
    first, so a decision is never made on stale reasoning."""
    rule = context.rule
    now = context.now or datetime.now(UTC)

    if not rule.get("enabled", False):
        return PolicyDecision(False, RULE_DISABLED)

    if context.account.get("status") != "connected":
        return PolicyDecision(False, ACCOUNT_NOT_CONNECTED, str(context.account.get("status")))

    # Auto DM pause. Read off the account rather than carried as a context field
    # on purpose: a field is something a call site can forget to pass, and the
    # gate would then silently not fire. `status` above is read the same way, so
    # neither caller has to change.
    #
    # Placement is load-bearing. After the two checks above, so a disabled rule
    # or a dead token never builds a backlog that looks deliverable; before
    # `already_messaged` and the rate limits, which are re-decided from scratch
    # when the held comment is replayed.
    if pause_active(context.account, now):
        held_until = paused_until(context.account)
        return PolicyDecision(
            False, AUTOMATION_PAUSED, held_until.isoformat() if held_until else ""
        )

    if not str(rule.get("message", "")).strip():
        return PolicyDecision(False, NO_MESSAGE)

    if context.action == ACTION_PRIVATE_REPLY and not within_reply_window(
        context.commented_at, now
    ):
        return PolicyDecision(
            False,
            OUTSIDE_REPLY_WINDOW,
            f"comment is older than {PRIVATE_REPLY_WINDOW_DAYS} days",
        )

    if context.action == ACTION_DIRECT_MESSAGE and not within_message_window(
        context.message_sent_at, now
    ):
        return PolicyDecision(
            False,
            OUTSIDE_MESSAGE_WINDOW,
            f"their message is older than {MESSAGE_REPLY_WINDOW_HOURS} hours",
        )

    if context.already_messaged:
        # "Only send this DM once per person" (§32 cooldown, simplified).
        return PolicyDecision(False, ALREADY_MESSAGED)

    # Follow gate. `None` means unknown — we do not block on a check we could
    # not perform, since that would silently drop every DM if the profile
    # lookup fails.
    if (rule.get("require_follow") or {}).get("enabled") and context.is_following is False:
        return PolicyDecision(False, NOT_FOLLOWING)

    limits = rule.get("limits") or {}
    for reason, sent, cap in (
        (RATE_LIMITED_MINUTE, context.sent_last_minute, limits.get("per_minute")),
        (RATE_LIMITED_HOUR, context.sent_last_hour, limits.get("per_hour")),
        (RATE_LIMITED_DAY, context.sent_last_day, limits.get("per_day")),
    ):
        if cap is not None and sent >= int(cap):
            return PolicyDecision(False, reason, f"{sent}/{cap}")

    # Dry run is allowed-but-do-not-send: the pipeline runs to completion and
    # records an execution, but the Meta client is never called (§36).
    if context.dry_run:
        return PolicyDecision(True, DRY_RUN)

    return PolicyDecision(True, ALLOWED)
