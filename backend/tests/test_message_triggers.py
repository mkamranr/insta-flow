"""Story-reply and DM-keyword triggers.

Two things here are worth more than the rest: that a rule written before the
`trigger` field existed does NOT start firing on DMs, and that we never reply to
ourselves. The first would surprise every existing user at once; the second is an
infinite loop.
"""

import json
from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.config import Settings
from app.services.meta import MockInstagramClient
from app.services.security.encryption import encrypt
from app.workers.tasks import process_message_event

ACCOUNT_IG_ID = "17841400000000000"
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


def make_ctx(database, client, redis=None):
    ctx = {"db": database, "settings": settings_for(), "client": client, "job_try": 1}
    if redis is not None:
        ctx["redis"] = redis
    return ctx


@pytest.fixture
async def account(database):
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
    return account_id


async def add_rule(database, account_id, trigger, **overrides):
    doc = {
        "instagram_account_id": account_id,
        "name": f"{trigger} rule",
        "trigger": trigger,
        "scope": "any",
        "instagram_media_ids": [],
        "keywords": ["link"],
        "message": "Here it is: https://example.com/thing",
        "buttons": [],
        "opening_dm": {"enabled": False, "message": "", "button_text": ""},
        "require_follow": {"enabled": False, "prompt": ""},
        "collect_email": {"enabled": False, "prompt": ""},
        "once_per_person": False,
        "enabled": True,
        "priority": 100,
        "limits": {"per_minute": 10, "per_hour": 100, "per_day": 500},
        **overrides,
    }
    if trigger is None:
        doc.pop("trigger")
    return (await database["rules"].insert_one(doc)).inserted_id


async def deliver(
    database,
    client,
    *,
    text="link",
    mid="mid_1",
    story=None,
    echo=False,
    sender=SENDER_ID,
    redis=None,
    delayed=False,
):
    """Push one messaging event through the worker the way the endpoint would."""
    message = {"mid": mid, "text": text}
    if story is not None:
        message["reply_to"] = story
    if echo:
        message["is_echo"] = True

    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_IG_ID,
                "time": int(datetime.now(UTC).timestamp() * 1000),
                "messaging": [
                    {
                        "sender": {"id": sender},
                        "recipient": {"id": ACCOUNT_IG_ID},
                        "timestamp": int(datetime.now(UTC).timestamp() * 1000),
                        "message": message,
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
    return await process_message_event(
        make_ctx(database, client, redis), str(event_id), ACCOUNT_IG_ID, sender, text, mid, delayed
    )


STORY = {"story": {"id": "story_123", "url": "https://cdn.example/s.jpg"}}


# -- the two triggers fire -----------------------------------------------------


async def test_a_story_reply_fires_a_story_rule(database, client, account):
    await add_rule(database, account, "story_reply")

    result = await deliver(database, client, story=STORY)

    assert result["status"] == "success"
    assert result["trigger"] == "story_reply"
    assert any("example.com/thing" in m["text"] for m in client.sent_direct_messages)


async def test_a_plain_dm_fires_a_keyword_rule(database, client, account):
    await add_rule(database, account, "dm_keyword")

    result = await deliver(database, client)

    assert result["status"] == "success"
    assert result["trigger"] == "dm_keyword"


async def test_the_story_id_is_recorded_on_the_execution(database, client, account):
    """Kept so per-story reporting stays possible without re-parsing webhooks."""
    await add_rule(database, account, "story_reply")

    await deliver(database, client, story=STORY)

    execution = await database["executions"].find_one({"action": "direct_message"})
    assert execution["story_id"] == "story_123"
    assert execution["trigger"] == "story_reply"


# -- the triggers stay in their lanes ------------------------------------------


async def test_a_quoted_reply_to_a_message_is_a_dm_not_a_story_reply(database, client, account):
    """`reply_to.mid` means they quoted an earlier message. Only `reply_to.story`
    is a story reply; confusing them fires the wrong automation."""
    await add_rule(database, account, "dm_keyword")

    result = await deliver(database, client, story={"mid": "some_earlier_message"})

    assert result["trigger"] == "dm_keyword"


async def test_a_story_rule_does_not_fire_on_a_plain_dm(database, client, account):
    await add_rule(database, account, "story_reply")

    result = await deliver(database, client)

    assert result["status"] == "unmatched"
    assert client.sent_direct_messages == []


async def test_a_dm_rule_does_not_fire_on_a_story_reply(database, client, account):
    await add_rule(database, account, "dm_keyword")

    result = await deliver(database, client, story=STORY)

    assert result["status"] == "unmatched"


async def test_a_rule_written_before_triggers_existed_never_fires_on_a_dm(
    database, client, account
):
    """The regression that would hit every existing user at once.

    Legacy rules have no `trigger` key at all. They are comment rules, and must
    not start answering DMs and story replies the day this ships.
    """
    await add_rule(database, account, None)  # no trigger field, as stored today

    assert (await deliver(database, client))["status"] == "unmatched"
    assert (await deliver(database, client, mid="mid_2", story=STORY))["status"] == "unmatched"
    assert client.sent_direct_messages == []


# -- loop safety ---------------------------------------------------------------


async def test_our_own_echo_never_creates_an_execution(database, client, account):
    await add_rule(database, account, "dm_keyword")

    await deliver(database, client, sender=ACCOUNT_IG_ID, echo=True)

    assert await database["executions"].count_documents({}) == 0


async def test_a_message_from_our_own_account_is_skipped_even_without_is_echo(
    database, client, account
):
    """`is_echo` is one field Meta controls, and a mid-keyed reservation cannot
    catch a loop: every echo carries a fresh mid and would earn its own valid
    reservation. So sender identity is checked independently."""
    await add_rule(database, account, "dm_keyword")

    result = await deliver(database, client, sender=ACCOUNT_IG_ID, echo=False)

    assert result["reason"] == "self_message"
    assert await database["executions"].count_documents({}) == 0


# -- idempotency ---------------------------------------------------------------


async def test_a_redelivered_message_sends_once(database, client, account):
    await add_rule(database, account, "dm_keyword")

    first = await deliver(database, client, mid="mid_same")
    second = await deliver(database, client, mid="mid_same")

    assert first["status"] == "success"
    assert second["status"] == "duplicate"
    assert len(client.sent_direct_messages) == 1


async def test_two_different_messages_both_send(database, client, account):
    """The null-collision regression, end to end rather than at the repository."""
    await add_rule(database, account, "dm_keyword")

    await deliver(database, client, mid="mid_a")
    await deliver(database, client, mid="mid_b")

    assert len(client.sent_direct_messages) == 2


async def test_a_message_with_no_id_is_never_sent(database, client, account):
    """No mid, no idempotency key, no send."""
    await add_rule(database, account, "dm_keyword")

    result = await deliver(database, client, mid="")

    assert result["reason"] == "no_message_id"
    assert client.sent_direct_messages == []


# -- interaction with the email flow -------------------------------------------


async def test_a_parked_email_capture_wins_over_a_keyword_rule(database, client, account):
    """Ordering is the whole safety argument.

    Someone parked at `awaiting_email` who sends "link me@example.com" must
    resume the email flow and NOT also fire the keyword rule. `once_per_person`
    cannot help: it counts success/dry_run rows, and a parked row is neither.
    """
    rule_id = await add_rule(database, account, "dm_keyword")
    await database["executions"].insert_one(
        {
            "instagram_account_id": account,
            "comment_id": ObjectId(),
            "rule_id": rule_id,
            "action": "private_reply",
            "status": "awaiting_email",
            "instagram_user_id": SENDER_ID,
            "created_at": datetime.now(UTC),
        }
    )

    result = await deliver(database, client, text="link me@example.com")

    assert result.get("email_captured") is True
    assert await database["executions"].count_documents({"action": "direct_message"}) == 0


# -- feature behaviour ---------------------------------------------------------


async def test_dry_run_sends_nothing(database, account):
    client = MockInstagramClient()
    await add_rule(database, account, "dm_keyword")
    ctx = {"db": database, "settings": settings_for(dry_run=True), "client": client, "job_try": 1}

    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_IG_ID,
                "time": int(datetime.now(UTC).timestamp() * 1000),
                "messaging": [
                    {"sender": {"id": SENDER_ID}, "message": {"mid": "mid_dry", "text": "link"}}
                ],
            }
        ],
    }
    event_id = (
        await database["webhook_events"].insert_one(
            {"event_hash": str(ObjectId()), "payload": payload, "processed": False}
        )
    ).inserted_id

    result = await process_message_event(
        ctx, str(event_id), ACCOUNT_IG_ID, SENDER_ID, "link", "mid_dry"
    )

    assert result["status"] == "dry_run"
    assert client.sent_direct_messages == []


async def test_an_attachment_sends_immediately_with_no_opening_dm(database, client, account):
    """The window is already open, so a file needs no button dance here.

    On the comment path an attachment requires the opening-DM flow first.
    """
    await add_rule(
        database,
        account,
        "dm_keyword",
        attachment={
            "type": "file",
            "url": "https://example.com/files/x/guide-0123456789.pdf",
            "storage_key": "x/guide-0123456789.pdf",
            "filename": "guide-0123456789.pdf",
            "size_bytes": 1024,
        },
    )

    await deliver(database, client)

    assert client.sent_attachments, "the file goes out on the first send"


async def test_a_non_follower_is_told_why_and_gets_no_link(database, client, account):
    from dataclasses import replace

    client.profile = replace(client.profile, is_user_follow_business=False)
    await add_rule(
        database,
        account,
        "dm_keyword",
        require_follow={"enabled": True, "prompt": "Follow me first!"},
    )

    result = await deliver(database, client)

    assert result["reason"] == "not_following"
    sent = [m["text"] for m in client.sent_direct_messages]
    assert "Follow me first!" in sent
    assert not any("example.com/thing" in text for text in sent)


# -- {{username}} on a trigger whose webhook does not carry one ---------------


async def test_username_is_looked_up_because_the_webhook_has_none(database, client, account):
    """A comment webhook carries the username; a messaging one does not.

    Without the lookup this renders "Hi !" -- on a trigger whose editor offers
    the variable.
    """
    await add_rule(database, account, "dm_keyword", message="Hi {{username}}! Here you go.")

    await deliver(database, client)

    assert client.sent_direct_messages[0]["text"].startswith(f"Hi {client.profile.username}!")


async def test_a_message_without_the_variable_costs_no_profile_call(database, client, account):
    """Otherwise every DM pays a Meta round trip to fill a variable nobody used."""
    await add_rule(database, account, "dm_keyword", message="Here you go.")

    await deliver(database, client)

    assert [c for c in client.calls if c.method == "get_user_profile"] == []


async def test_a_refused_profile_lookup_still_sends_the_message(database, client, account):
    """Meta refuses this lookup for some senders. Losing a greeting is a much
    smaller harm than losing the reply."""
    from app.errors import MetaAPIError

    client.fail(
        "get_user_profile",
        MetaAPIError("User consent is required", status_code=400, retryable=False),
    )
    await add_rule(database, account, "dm_keyword", message="Hi {{username}}! Here you go.")

    result = await deliver(database, client)

    assert result["status"] == "success"
    # An awkward "Hi !" is the accepted cost. The renderer substitutes an empty
    # value rather than dropping the surrounding text, and inventing a name
    # would be worse than a stray space.
    assert client.sent_direct_messages[0]["text"].startswith("Hi ! Here you go.")


async def test_the_execution_records_the_message_that_was_actually_sent(database, client, account):
    """The disclosure is appended at send time.

    Storing the pre-disclosure render would make the activity log show the
    operator a message the recipient never received.
    """
    await add_rule(database, account, "dm_keyword", message="Here you go.")

    await deliver(database, client)

    execution = await database["executions"].find_one({"action": "direct_message"})
    assert execution["message_text"] == client.sent_direct_messages[0]["text"]
    assert "Automated reply." in execution["message_text"]
