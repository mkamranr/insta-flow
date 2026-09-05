"""The "require them to follow you first" gate.

This was previously dead code: `PolicyContext.is_following` existed and
`policy.evaluate` read it, but nothing ever populated it, so the gate never
blocked anyone and non-followers received the link. These tests pin the wiring
at both ends.

The gate is enforced twice on purpose. At the comment, to avoid opening a
conversation with someone who will not be allowed the payoff; and again at the
postback, because **that is the message carrying the link**, and by then a DM
thread exists -- which is when Meta is most willing to answer the profile
lookup at all.
"""

from dataclasses import replace

import pytest
from bson import ObjectId

from app.config import Settings
from app.models.rules import (
    FOLLOW_PROMPT_NO_WINDOW,
    FOLLOW_PROMPT_WITH_WINDOW,
    FollowGate,
)
from app.services.meta import MockInstagramClient
from app.services.meta.webhooks import POSTBACK_PREFIX
from app.services.security.encryption import encrypt
from app.workers.tasks import (
    AWAITING_FOLLOW,
    lookup_is_following,
    process_postback_event,
    recheck_follow,
)

ACCOUNT_IG_ID = "17841400000000000"
MEDIA_ID = "17900000000000000"
COMMENT_ID = "17981234567890123"
SENDER_ID = "27962242703427275"
KEY = "k" * 64


def settings_for(dry_run: bool = False) -> Settings:
    return Settings(
        dry_run=dry_run,
        meta_app_secret="s",
        meta_webhook_verify_token="verify",
        session_secret="s" * 64,
        encryption_key=KEY,
        meta_app_id="1",
    )


@pytest.fixture
def client():
    return MockInstagramClient()


def following(client, value):
    """Script what Meta will say about the commenter."""
    client.profile = replace(client.profile, is_user_follow_business=value)
    return client


@pytest.fixture
async def seeded(database):
    """An account, a comment, and a rule that gates on following."""
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "instagram_user_id": ACCOUNT_IG_ID,
            "username": "myshop",
            "status": "connected",
            "access_token_encrypted": encrypt("test-access-token", KEY),
        }
    )
    rule_id = (
        await database["rules"].insert_one(
            {
                "instagram_account_id": account_id,
                "name": "Tools link",
                "scope": "specific",
                "instagram_media_ids": [MEDIA_ID],
                "keywords": ["link"],
                "message": "Here it is: https://example.com/tools",
                "buttons": [],
                "require_follow": {"enabled": True, "prompt": "Follow me first and comment again!"},
                "collect_email": {"enabled": False, "prompt": ""},
                "opening_dm": {"enabled": False, "message": "", "button_text": ""},
                "once_per_person": False,
                "enabled": True,
                "priority": 100,
                "limits": {"per_minute": 10, "per_hour": 100, "per_day": 500},
            }
        )
    ).inserted_id
    comment_id = (
        await database["comments"].insert_one(
            {
                "instagram_account_id": account_id,
                "instagram_media_id": MEDIA_ID,
                "instagram_comment_id": COMMENT_ID,
                "instagram_user_id": SENDER_ID,
                "username": "someone",
                "text": "link",
            }
        )
    ).inserted_id
    return {"account_id": account_id, "rule_id": rule_id, "comment_id": comment_id}


def make_ctx(database, client, *, dry_run=False):
    return {
        "db": database,
        "settings": settings_for(dry_run),
        "client": client,
        "job_try": 1,
        "job_id": "test-job",
    }


async def tap(database, client, seeded, *, dry_run=False):
    payload = f"{POSTBACK_PREFIX}:{seeded['rule_id']}:{COMMENT_ID}"
    return await process_postback_event(
        make_ctx(database, client, dry_run=dry_run),
        str(ObjectId()),
        ACCOUNT_IG_ID,
        SENDER_ID,
        payload,
    )


# -- the lookup helper ------------------------------------------------------


async def test_lookup_returns_what_meta_says(database, client):
    account = {"access_token_encrypted": encrypt("t", KEY)}
    assert await lookup_is_following(following(client, True), settings_for(), account, SENDER_ID)
    assert (
        await lookup_is_following(following(client, False), settings_for(), account, SENDER_ID)
        is False
    )


async def test_lookup_returns_none_when_meta_refuses(database, client):
    """Meta answers "User consent is required to access user profile" for some
    users. That is unknown, not "not following" -- and unknown fails open."""
    client.fail("get_user_profile", 500)
    account = {"access_token_encrypted": encrypt("t", KEY)}

    assert await lookup_is_following(client, settings_for(), account, SENDER_ID) is None


# -- the postback gate, where the link is handed over -----------------------


async def test_a_non_follower_does_not_get_the_link(database, client, seeded):
    result = await tap(database, following(client, False), seeded)

    # Parked, not finished: the button tap opened a 24-hour window, so the link
    # can still be delivered inside it once they follow.
    assert result["status"] == AWAITING_FOLLOW
    sent = [m["text"] for m in client.sent_direct_messages]
    assert not any("example.com/tools" in text for text in sent)


async def test_a_non_follower_is_told_why(database, client, seeded):
    await tap(database, following(client, False), seeded)
    assert client.sent_direct_messages[-1]["text"] == "Follow me first and comment again!"


async def test_a_follower_gets_the_link(database, client, seeded):
    result = await tap(database, following(client, True), seeded)

    assert result["status"] == "success"
    assert any("example.com/tools" in m["text"] for m in client.sent_direct_messages)


async def test_unknown_follow_status_still_gets_the_link(database, client, seeded):
    """Fail open, by explicit choice: no genuine follower is ever turned away."""
    client.fail("get_user_profile", 500)

    result = await tap(database, client, seeded)

    assert result["status"] == "success"
    assert any("example.com/tools" in m["text"] for m in client.sent_direct_messages)


async def test_the_block_is_recorded_on_the_execution(database, client, seeded):
    await tap(database, following(client, False), seeded)

    execution = await database["executions"].find_one({"rule_id": seeded["rule_id"]})
    assert execution["status"] == AWAITING_FOLLOW
    assert execution["policy_decision"] == "not_following"
    assert execution["follow_deadline"] is not None


async def test_a_rule_without_the_gate_never_calls_meta_for_a_profile(database, client, seeded):
    """The lookup costs a Meta round trip, so it must not run unasked."""
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]}, {"$set": {"require_follow": {"enabled": False, "prompt": ""}}}
    )

    await tap(database, following(client, False), seeded)

    assert not [c for c in client.calls if c.method == "get_user_profile"]


async def test_dry_run_sends_nothing_and_does_not_look_anyone_up(database, client, seeded):
    result = await tap(database, following(client, False), seeded, dry_run=True)

    assert result["status"] == "dry_run"
    assert client.sent_direct_messages == []
    assert not [c for c in client.calls if c.method == "get_user_profile"]


# -- the comment step: a blocked non-follower must be told why ---------------


async def comment_from(database, client, seeded, *, dry_run=False):
    """Drive process_comment_event for a comment on the gated post."""
    import json
    from datetime import UTC, datetime

    from app.workers.tasks import process_comment_event

    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_IG_ID,
                "time": int(datetime.now(UTC).timestamp()),
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": "18100000000000001",
                            "text": "link",
                            "from": {"id": SENDER_ID, "username": "someone"},
                            "media": {"id": MEDIA_ID, "media_product_type": "REELS"},
                        },
                    }
                ],
            }
        ],
    }
    event_id = (
        await database["webhook_events"].insert_one(
            {
                "object": "instagram",
                "event_hash": str(ObjectId()),
                "raw_body": json.dumps(payload),
                "payload": payload,
                "processed": False,
            }
        )
    ).inserted_id
    return await process_comment_event(
        make_ctx(database, client, dry_run=dry_run),
        str(event_id),
        ACCOUNT_IG_ID,
        "18100000000000001",
    )


async def test_a_blocked_non_follower_receives_the_prompt(database, client, seeded):
    """Silence would leave them with no idea why nothing arrived.

    The rule's prompt literally says "comment again", so it has to be delivered
    for that instruction to mean anything.
    """
    result = await comment_from(database, following(client, False), seeded)

    assert result["status"] == "skipped"
    assert result["reason"] == "not_following"
    assert client.sent_private_replies[-1]["text"] == "Follow me first and comment again!"


async def test_the_blocked_prompt_carries_no_link(database, client, seeded):
    sent = await comment_from(database, following(client, False), seeded)
    assert sent["reason"] == "not_following"
    assert not any("example.com" in m["text"] for m in client.sent_private_replies)


async def test_a_follower_is_not_sent_the_prompt(database, client, seeded):
    await comment_from(database, following(client, True), seeded)
    assert not any("Follow me first" in m["text"] for m in client.sent_private_replies)


async def test_a_blocked_attempt_does_not_use_up_once_per_person(database, client, seeded):
    """`already_sent_to` counts success/dry_run only.

    If a skipped attempt counted, "follow me and comment again" would be a lie:
    the second comment would be refused as a duplicate.
    """
    from app.db.repositories import ExecutionsRepository

    await comment_from(database, following(client, False), seeded)

    assert not await ExecutionsRepository(database).already_sent_to(seeded["rule_id"], SENDER_ID)


# -- delivering the link once they follow ------------------------------------


async def parked_execution(database):
    return await database["executions"].find_one({"status": AWAITING_FOLLOW})


async def test_tapping_again_after_following_delivers_the_link(database, client, seeded):
    """The user's own suggestion: re-tap, re-check, deliver.

    It works because `awaiting_follow` is not a terminal status, so the second
    postback picks the parked execution back up instead of being dropped as a
    duplicate.
    """
    await tap(database, following(client, False), seeded)
    result = await tap(database, following(client, True), seeded)

    assert result["status"] == "success"
    assert any("example.com/tools" in m["text"] for m in client.sent_direct_messages)


async def test_the_scheduled_recheck_delivers_without_any_second_tap(database, client, seeded):
    await tap(database, following(client, False), seeded)
    execution = await parked_execution(database)

    result = await recheck_follow(
        make_ctx(database, following(client, True)), str(execution["_id"]), 1
    )

    assert result["status"] == "success"
    assert any("example.com/tools" in m["text"] for m in client.sent_direct_messages)


async def test_the_recheck_keeps_waiting_while_they_still_do_not_follow(database, client, seeded):
    await tap(database, following(client, False), seeded)
    execution = await parked_execution(database)

    result = await recheck_follow(
        make_ctx(database, following(client, False)), str(execution["_id"]), 1
    )

    assert result["status"] == AWAITING_FOLLOW
    assert not any("example.com/tools" in m["text"] for m in client.sent_direct_messages)


async def test_an_unknown_answer_does_not_release_the_link(database, client, seeded):
    """Unknown fails OPEN at the gate but must fail CLOSED here.

    By this point we hold positive evidence the person was not following, so a
    refused lookup is not permission -- it would hand over the very link the
    gate just withheld.
    """
    await tap(database, following(client, False), seeded)
    execution = await parked_execution(database)

    client.fail("get_user_profile", 500)
    result = await recheck_follow(make_ctx(database, client), str(execution["_id"]), 1)

    assert result["status"] == AWAITING_FOLLOW
    assert not any("example.com/tools" in m["text"] for m in client.sent_direct_messages)


async def test_the_watch_gives_up_at_the_deadline(database, client, seeded):
    from datetime import UTC, datetime, timedelta

    await tap(database, following(client, False), seeded)
    execution = await parked_execution(database)
    await database["executions"].update_one(
        {"_id": execution["_id"]},
        {"$set": {"follow_deadline": datetime.now(UTC) - timedelta(seconds=1)}},
    )

    result = await recheck_follow(
        make_ctx(database, following(client, False)), str(execution["_id"]), 9
    )

    assert result["reason"] == "follow_watch_expired"
    row = await database["executions"].find_one({"_id": execution["_id"]})
    assert row["status"] == "skipped"


async def test_only_one_of_a_racing_tap_and_recheck_can_send(database, client, seeded):
    """The claim is atomic, so the link is never DMed twice."""
    from app.db.repositories import ExecutionsRepository

    await tap(database, following(client, False), seeded)
    execution = await parked_execution(database)
    executions = ExecutionsRepository(database)

    first = await executions.claim_awaiting_follow(execution["_id"])
    second = await executions.claim_awaiting_follow(execution["_id"])

    assert first is not None
    assert second is None


async def test_a_finished_execution_is_not_resent_by_a_late_timer(database, client, seeded):
    await tap(database, following(client, False), seeded)
    execution = await parked_execution(database)
    await tap(database, following(client, True), seeded)  # delivered via re-tap
    before = len(client.sent_direct_messages)

    result = await recheck_follow(
        make_ctx(database, following(client, True)), str(execution["_id"]), 3
    )

    assert result["reason"] == "not_awaiting"
    assert len(client.sent_direct_messages) == before


# -- once_per_person must not lock out someone who did as they were asked ----


async def test_an_outstanding_follow_block_does_not_count_as_messaged(database, client, seeded):
    """The bug this fixes, in one test.

    A real run went: opener sent (success) -> button tapped -> gate blocked. The
    successful opener made `already_sent_to` true, so commenting again after
    following was refused as `already_messaged`. We had told them to follow and
    then punished them for it.
    """
    from app.db.repositories import ExecutionsRepository

    await database["executions"].insert_one(
        {
            "instagram_account_id": seeded["account_id"],
            "comment_id": seeded["comment_id"],
            "rule_id": seeded["rule_id"],
            "action": "opening_dm",
            "status": "success",
            "instagram_user_id": SENDER_ID,
        }
    )
    await tap(database, following(client, False), seeded)

    assert not await ExecutionsRepository(database).already_sent_to(seeded["rule_id"], SENDER_ID)


async def test_once_the_link_is_delivered_they_do_count_as_messaged(database, client, seeded):
    from app.db.repositories import ExecutionsRepository

    await tap(database, following(client, False), seeded)
    await tap(database, following(client, True), seeded)

    assert await ExecutionsRepository(database).already_sent_to(seeded["rule_id"], SENDER_ID)


def test_the_repository_and_policy_agree_on_the_reason_code():
    """The repository keeps its own copy to avoid importing services. Pin them."""
    from app.db.repositories.executions import NOT_FOLLOWING as REPO_REASON
    from app.services.campaigns.policy import NOT_FOLLOWING as POLICY_REASON

    assert REPO_REASON == POLICY_REASON


def test_the_recheck_schedule_stays_inside_the_messaging_window():
    """Polling must not outlive the 24-hour window the tap opened."""
    from app.workers.tasks import FOLLOW_WATCH_WINDOW

    assert FOLLOW_WATCH_WINDOW.total_seconds() < 24 * 3600


# -- the opener must not be blocked at the comment step ----------------------


@pytest.fixture
async def seeded_with_opener(database, seeded):
    """The real-world shape: a rule that opens with a button DM."""
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]},
        {
            "$set": {
                "opening_dm": {
                    "enabled": True,
                    "message": "Tap below and I'll send it over",
                    "button_text": "Send me the link",
                }
            }
        },
    )
    return seeded


async def test_a_non_follower_still_gets_the_opener(database, client, seeded_with_opener):
    """The bug: blocking here strands them.

    A comment allows exactly one private reply and opens no messaging window.
    Spending that reply on "follow me and the link lands automatically" leaves
    no way to ever deliver -- no button to tap, no window to send through. The
    opener carries no link, so it must go out; the postback gate enforces.
    """
    result = await comment_from(database, following(client, False), seeded_with_opener)

    assert result["status"] != "skipped"
    assert client.sent_private_replies, "the opener should have been sent"


async def test_the_opener_path_does_not_burn_a_meta_lookup(database, client, seeded_with_opener):
    """No decision rides on it here, so the round trip is pure waste."""
    await comment_from(database, following(client, False), seeded_with_opener)

    assert not [c for c in client.calls if c.method == "get_user_profile"]


async def test_a_rule_without_an_opener_still_blocks_at_the_comment(database, client, seeded):
    """Here the link IS this message, so it has to be withheld now or never."""
    result = await comment_from(database, following(client, False), seeded)

    assert result["reason"] == "not_following"
    assert not any("example.com" in m["text"] for m in client.sent_private_replies)


async def test_tapping_again_after_a_blocked_execution_delivers(database, client, seeded):
    """A blocked run is withheld, not finished.

    Tapping again is how someone says "I have followed now", and the fresh tap
    opens a new 24-hour window, so the link can still be handed over.
    """
    await database["executions"].insert_one(
        {
            "instagram_account_id": seeded["account_id"],
            "comment_id": seeded["comment_id"],
            "rule_id": seeded["rule_id"],
            "action": "private_reply",
            "status": "skipped",
            "policy_decision": "not_following",
            "instagram_user_id": SENDER_ID,
        }
    )

    result = await tap(database, following(client, True), seeded)

    assert result["status"] == "success"
    assert any("example.com/tools" in m["text"] for m in client.sent_direct_messages)


async def test_a_genuinely_delivered_execution_is_still_a_duplicate(database, client, seeded):
    """Re-entry is for blocked runs only, never for ones that succeeded."""
    await tap(database, following(client, True), seeded)
    before = len(client.sent_direct_messages)

    result = await tap(database, following(client, True), seeded)

    assert result["status"] == "duplicate"
    assert len(client.sent_direct_messages) == before


# -- the prompt says something the flow can actually deliver -----------------
#
# The shipped default told people to "comment again" on a rule whose whole point
# is that they do not have to: the postback opened a window and `recheck_follow`
# delivers on its own. Three copies of this string existed, and the only correct
# one was the postback fallback, reachable only when a rule carried no prompt --
# which the editor made impossible by pre-filling the wrong one. These pin the
# wording to the behaviour rather than to a reviewer noticing.


def test_the_default_prompt_does_not_ask_for_another_comment() -> None:
    """The default applies to opener rules, where re-commenting achieves nothing.

    Asserted on the whole phrase, not the substring: "no need to comment again"
    contains "comment again", so a naive check passes the very wording it is
    meant to forbid. It did, on the first run of this test.
    """
    assert FollowGate().prompt == FOLLOW_PROMPT_WITH_WINDOW
    assert "no need to comment again" in FollowGate().prompt.lower()


def test_the_two_prompts_disagree_about_commenting_again() -> None:
    """They describe genuinely different recoveries; collapsing them loses one.

    Without an opening DM there is no messaging window and the comment's single
    private reply has been spent on the prompt itself, so commenting again is the
    only way back. Saying otherwise would strand people.
    """
    assert "no need to comment again" in FOLLOW_PROMPT_WITH_WINDOW.lower()

    no_window = FOLLOW_PROMPT_NO_WINDOW.lower()
    assert "comment again" in no_window
    assert "no need" not in no_window


async def test_a_promptless_rule_falls_back_to_the_windowed_wording(database, client, seeded):
    """The fallback is what a rule saved before this feature will use."""
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]},
        {"$set": {"require_follow": {"enabled": True, "prompt": ""}}},
    )

    await tap(database, following(client, False), seeded)

    assert client.sent_direct_messages[-1]["text"] == FOLLOW_PROMPT_WITH_WINDOW
