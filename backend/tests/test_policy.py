"""Policy engine (§30): the last gate before a Meta mutation."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.campaigns.policy import (
    ACCOUNT_NOT_CONNECTED,
    ACTION_PRIVATE_REPLY,
    ALLOWED,
    ALREADY_MESSAGED,
    DRY_RUN,
    NO_MESSAGE,
    OUTSIDE_REPLY_WINDOW,
    RATE_LIMITED_DAY,
    RATE_LIMITED_HOUR,
    RATE_LIMITED_MINUTE,
    RULE_DISABLED,
    PolicyContext,
    evaluate,
    within_reply_window,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def context(**overrides):
    base = dict(
        rule={
            "enabled": True,
            "message": "Here's the link!",
            "limits": {"per_minute": 10, "per_hour": 100, "per_day": 500},
        },
        account={"status": "connected"},
        action=ACTION_PRIVATE_REPLY,
        commented_at=NOW - timedelta(hours=1),
        dry_run=False,
        now=NOW,
    )
    rule_overrides = overrides.pop("rule", None)
    if rule_overrides:
        base["rule"] = {**base["rule"], **rule_overrides}
    base.update(overrides)
    return PolicyContext(**base)


# --- reply window -----------------------------------------------------------


def test_a_fresh_comment_is_inside_the_window():
    assert within_reply_window(NOW - timedelta(days=6, hours=23), NOW) is True


def test_a_comment_older_than_seven_days_is_outside():
    assert within_reply_window(NOW - timedelta(days=7, seconds=1), NOW) is False


def test_an_unknown_timestamp_is_treated_as_eligible():
    """Meta stays the final authority; the reservation still prevents duplicates."""
    assert within_reply_window(None, NOW) is True


def test_a_naive_timestamp_is_assumed_utc():
    """Mongo can hand back a naive datetime; comparing it would raise."""
    naive = (NOW - timedelta(days=1)).replace(tzinfo=None)
    assert within_reply_window(naive, NOW) is True


# --- gates ------------------------------------------------------------------


def test_an_allowed_action_passes():
    decision = evaluate(context())
    assert decision.allowed is True and decision.reason == ALLOWED


def test_a_disabled_rule_is_denied():
    assert evaluate(context(rule={"enabled": False})).reason == RULE_DISABLED


def test_a_disconnected_account_is_denied():
    assert evaluate(context(account={"status": "token_error"})).reason == ACCOUNT_NOT_CONNECTED


def test_a_rule_with_an_empty_message_is_denied():
    """Better to deny here than to fail mid-send with nothing to say."""
    assert evaluate(context(rule={"message": "   "})).reason == NO_MESSAGE


def test_a_comment_outside_the_window_is_denied():
    decision = evaluate(context(commented_at=NOW - timedelta(days=8)))
    assert decision.allowed is False and decision.reason == OUTSIDE_REPLY_WINDOW


def test_already_messaged_denies():
    """"Only send this DM once per person"."""
    assert evaluate(context(already_messaged=True)).reason == ALREADY_MESSAGED


@pytest.mark.parametrize(
    "field,reason",
    [
        ("sent_last_minute", RATE_LIMITED_MINUTE),
        ("sent_last_hour", RATE_LIMITED_HOUR),
        ("sent_last_day", RATE_LIMITED_DAY),
    ],
)
def test_each_rate_limit_denies(field, reason):
    caps = {"sent_last_minute": 10, "sent_last_hour": 100, "sent_last_day": 500}
    assert evaluate(context(**{field: caps[field]})).reason == reason


def test_just_under_a_limit_is_allowed():
    assert evaluate(context(sent_last_minute=9)).allowed is True


def test_dry_run_is_allowed_but_marked():
    """Dry run runs the whole pipeline and records an execution, but never sends."""
    decision = evaluate(context(dry_run=True))
    assert decision.allowed is True
    assert decision.is_dry_run is True
    assert decision.reason == DRY_RUN


def test_a_denial_beats_dry_run():
    """A disabled rule must read as disabled, not as a dry run."""
    decision = evaluate(context(rule={"enabled": False}, dry_run=True))
    assert decision.allowed is False and decision.reason == RULE_DISABLED


def test_the_decision_reason_is_recorded_for_debugging():
    decision = evaluate(context(sent_last_day=500))
    assert decision.detail == "500/500"
