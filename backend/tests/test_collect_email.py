"""The "collect email before sending" flow."""

from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.config import Settings, get_settings
from app.services.contacts import extract_email
from app.services.meta import MockInstagramClient
from app.services.meta.webhooks import POSTBACK_PREFIX, parse_message_events
from app.services.security.encryption import encrypt
from app.workers.tasks import (
    process_comment_event,
    process_message_event,
    process_postback_event,
)

ACCOUNT_IG_ID = "17841400000000000"
SENDER = "17800000000000000"
MEDIA_ID = "17890000000000000"


# --- extraction -------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("me@example.com", "me@example.com"),
        ("sure, it's Me@Example.COM", "me@example.com"),
        (
            "here you go: first.last+tag@sub.example.co.uk thanks!",
            "first.last+tag@sub.example.co.uk",
        ),
        ("email me@example.com.", "me@example.com"),
    ],
)
def test_an_address_is_found_inside_a_sentence(text, expected):
    """People reply in prose, not with a bare address."""
    assert extract_email(text) == expected


@pytest.mark.parametrize("text", ["", "no address here", "@handle", "someone@", "a@b"])
def test_non_addresses_return_none(text):
    assert extract_email(text) is None


# --- webhook parsing --------------------------------------------------------


def test_inbound_messages_are_parsed_from_the_messaging_array():
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_IG_ID,
                "messaging": [
                    {"sender": {"id": SENDER}, "message": {"mid": "m1", "text": "hi@example.com"}}
                ],
            }
        ],
    }
    events = parse_message_events(payload)
    assert len(events) == 1
    assert events[0].sender_id == SENDER
    assert events[0].text == "hi@example.com"


def test_our_own_echoes_are_flagged():
    """Acting on an echo of our own send would loop."""
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_IG_ID,
                "messaging": [
                    {"sender": {"id": ACCOUNT_IG_ID}, "message": {"text": "hi", "is_echo": True}}
                ],
            }
        ],
    }
    assert parse_message_events(payload)[0].is_echo is True


# --- the flow ---------------------------------------------------------------


@pytest.fixture
async def parked(database):
    """An account and a rule, with an execution waiting for an email."""
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "instagram_user_id": ACCOUNT_IG_ID,
            "username": "myshop",
            "status": "connected",
            "access_token_encrypted": encrypt("tok", "k" * 64),
        }
    )
    rule_id = (
        await database["rules"].insert_one(
            {
                "instagram_account_id": account_id,
                "name": "Email gate",
                "keywords": ["link"],
                "message": "Here's the link!",
                "buttons": [{"title": "Open", "url": "https://example.com"}],
                "collect_email": {"enabled": True, "prompt": "What's your email?"},
                "enabled": True,
            }
        )
    ).inserted_id
    execution_id = (
        await database["executions"].insert_one(
            {
                "instagram_account_id": account_id,
                "comment_id": ObjectId(),
                "rule_id": rule_id,
                "action": "private_reply",
                "status": "awaiting_email",
                "instagram_user_id": SENDER,
                "created_at": datetime.now(UTC),
            }
        )
    ).inserted_id
    return {"account_id": account_id, "rule_id": rule_id, "execution_id": execution_id}


def ctx(database, client):
    return {
        "db": database,
        "settings": Settings(dry_run=False, encryption_key="k" * 64, session_secret="s" * 64),
        "client": client,
        "job_try": 1,
    }


async def test_a_valid_email_is_captured_and_the_payoff_is_sent(database, parked):
    client = MockInstagramClient()
    result = await process_message_event(
        ctx(database, client), "evt", ACCOUNT_IG_ID, SENDER, "sure — me@example.com"
    )

    assert result["status"] == "success"
    contact = await database["contacts"].find_one({})
    assert contact["email"] == "me@example.com"

    assert len(client.sent_direct_messages) == 1
    assert client.sent_direct_messages[0]["text"] == "Here's the link!"

    execution = await database["executions"].find_one({"_id": parked["execution_id"]})
    assert execution["status"] == "success"


async def test_a_reply_without_an_email_leaves_the_execution_parked(database, parked):
    """Re-prompting on every message would be spam."""
    client = MockInstagramClient()
    result = await process_message_event(
        ctx(database, client), "evt", ACCOUNT_IG_ID, SENDER, "what do you need it for?"
    )

    assert result["status"] == "awaiting_email"
    assert client.sent_direct_messages == []
    execution = await database["executions"].find_one({"_id": parked["execution_id"]})
    assert execution["status"] == "awaiting_email"


async def test_messages_from_people_with_nothing_pending_are_ignored(database, parked):
    """Social Flow is not a chatbot; it must not answer unrelated conversation."""
    client = MockInstagramClient()
    result = await process_message_event(
        ctx(database, client), "evt", ACCOUNT_IG_ID, "someone-else", "hello@example.com"
    )
    assert result["status"] == "ignored"
    assert client.sent_direct_messages == []
    assert await database["contacts"].count_documents({}) == 0


async def test_the_payoff_is_sent_only_once(database, parked):
    client = MockInstagramClient()
    await process_message_event(ctx(database, client), "e", ACCOUNT_IG_ID, SENDER, "a@b.com")
    second = await process_message_event(
        ctx(database, client), "e", ACCOUNT_IG_ID, SENDER, "a@b.com"
    )
    assert second["status"] == "ignored"
    assert len(client.sent_direct_messages) == 1


async def test_a_repeat_capture_updates_rather_than_duplicating(database, parked):
    client = MockInstagramClient()
    await process_message_event(ctx(database, client), "e", ACCOUNT_IG_ID, SENDER, "one@x.com")

    await database["executions"].update_one(
        {"_id": parked["execution_id"]}, {"$set": {"status": "awaiting_email"}}
    )
    await process_message_event(ctx(database, client), "e", ACCOUNT_IG_ID, SENDER, "two@x.com")

    assert await database["contacts"].count_documents({}) == 1
    assert (await database["contacts"].find_one({}))["email"] == "two@x.com"


# --- the seam that was missing -----------------------------------------------
#
# Everything above starts from a hand-planted `awaiting_email` execution, so the
# path that creates one -- comment, opener, tap -- was never driven, and neither
# was the API that is supposed to let anyone switch the gate on. The editor
# shipped with no control for it and nothing failed.


@pytest.fixture
async def account_with_post(database, admin):
    """An account and a synced post, owned by the signed-in admin."""
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "user_id": admin["_id"],
            "instagram_user_id": ACCOUNT_IG_ID,
            "username": "myshop",
            "status": "connected",
            "access_token_encrypted": encrypt("tok", get_settings().encryption_key),
        }
    )
    await database["media"].insert_one(
        {
            "instagram_account_id": account_id,
            "instagram_media_id": MEDIA_ID,
            "media_type": "IMAGE",
            "published_at": datetime.now(UTC),
        }
    )
    return account_id


def rule_body(**overrides):
    """A comment rule with a working opening DM, which the gate depends on."""
    body = {
        "name": "Guide",
        "trigger": "comment",
        "scope": "specific",
        "instagram_media_ids": [MEDIA_ID],
        "keywords": ["guide"],
        "message": "Here is the guide.",
        "opening_dm": {"enabled": True, "message": "Want the guide?", "button_text": "Send it"},
        "collect_email": {"enabled": True, "prompt": "Drop your email."},
    }
    body.update(overrides)
    return body


async def test_the_email_gate_round_trips_through_the_api(signed_in, account_with_post):
    """The test whose absence let a missing editor control ship unnoticed."""
    created = await signed_in.post("/api/rules", json=rule_body())

    assert created.status_code == 201, created.text
    assert created.json()["collect_email"] == {"enabled": True, "prompt": "Drop your email."}

    fetched = await signed_in.get(f"/api/rules/{created.json()['id']}")
    assert fetched.json()["collect_email"]["enabled"] is True


async def test_a_patch_that_says_nothing_about_the_gate_leaves_it_alone(
    signed_in, account_with_post
):
    rule_id = (await signed_in.post("/api/rules", json=rule_body())).json()["id"]

    patched = await signed_in.patch(f"/api/rules/{rule_id}", json={"name": "Renamed"})

    assert patched.json()["collect_email"]["prompt"] == "Drop your email."


async def test_the_gate_is_refused_without_an_opening_dm(signed_in, account_with_post):
    """It would save cleanly and then ask nobody for anything: the prompt is sent
    from the postback handler, which needs a button to be tapped."""
    response = await signed_in.post(
        "/api/rules",
        json=rule_body(opening_dm={"enabled": False, "message": "", "button_text": ""}),
    )

    assert response.status_code == 422
    assert "opening DM" in response.json()["error"]["message"]


async def test_an_opening_dm_with_no_message_does_not_count(signed_in, account_with_post):
    """`uses_opening_dm` wants text as well as the flag -- an empty opener sends
    nothing, so it carries no button."""
    response = await signed_in.post(
        "/api/rules",
        json=rule_body(opening_dm={"enabled": True, "message": "   ", "button_text": "Go"}),
    )

    assert response.status_code == 422


async def test_the_gate_is_refused_on_a_dm_keyword_rule(signed_in, account_with_post):
    """`_match_inbound_message` never reads the gate."""
    response = await signed_in.post(
        "/api/rules",
        json=rule_body(
            trigger="dm_keyword",
            scope="any",
            instagram_media_ids=[],
            opening_dm={"enabled": False, "message": "", "button_text": ""},
        ),
    )

    assert response.status_code == 422
    assert "DM keyword" in response.json()["error"]["message"]


async def test_switching_the_gate_off_is_always_allowed(signed_in, account_with_post):
    """The refusal must not trap a rule in a state it cannot leave."""
    rule_id = (await signed_in.post("/api/rules", json=rule_body())).json()["id"]

    patched = await signed_in.patch(
        f"/api/rules/{rule_id}",
        json={
            "collect_email": {"enabled": False, "prompt": ""},
            "opening_dm": {"enabled": False, "message": "", "button_text": ""},
        },
    )

    assert patched.status_code == 200
    assert patched.json()["collect_email"]["enabled"] is False


@pytest.fixture
async def armed(database):
    """An account and a two-step rule with the email gate on, ready for a comment."""
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "instagram_user_id": ACCOUNT_IG_ID,
            "username": "myshop",
            "status": "connected",
            "access_token_encrypted": encrypt("tok", "k" * 64),
        }
    )
    await database["media"].insert_one(
        {"instagram_account_id": account_id, "instagram_media_id": MEDIA_ID, "caption": "guide"}
    )
    rule_id = (
        await database["rules"].insert_one(
            {
                "instagram_account_id": account_id,
                "name": "Guide",
                "trigger": "comment",
                "scope": "specific",
                "instagram_media_ids": [MEDIA_ID],
                "keywords": ["guide"],
                "match_mode": "contains",
                "message": "Here is the guide.",
                "buttons": [],
                "opening_dm": {
                    "enabled": True,
                    "message": "Want the guide?",
                    "button_text": "Send it",
                },
                "require_follow": {"enabled": False, "prompt": ""},
                "collect_email": {"enabled": True, "prompt": "Drop your email."},
                "once_per_person": False,
                "enabled": True,
                "priority": 100,
                "limits": {"per_minute": 10, "per_hour": 100, "per_day": 500},
            }
        )
    ).inserted_id
    return {"account_id": account_id, "rule_id": rule_id}


async def test_a_comment_then_a_tap_asks_for_the_email_and_parks(database, armed):
    """The whole path the other tests start downstream of: comment, opener, tap,
    prompt. Nothing here is hand-planted, so a break anywhere along it shows up."""
    client = MockInstagramClient()
    comment_id = "17900000000000000"
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
                            "from": {"id": SENDER, "username": "emma"},
                            "comment_id": comment_id,
                            "text": "send me the guide",
                            "media": {"id": MEDIA_ID, "media_product_type": "FEED"},
                        },
                    }
                ],
            }
        ],
    }
    event_id = str(
        (
            await database["webhook_events"].insert_one(
                {"event_hash": str(ObjectId()), "payload": payload, "status": "received"}
            )
        ).inserted_id
    )

    opener = await process_comment_event(ctx(database, client), event_id, ACCOUNT_IG_ID, comment_id)
    assert opener["action"] == "opening_dm"
    assert "Want the guide?" in client.sent_private_replies[0]["text"]

    tapped = await process_postback_event(
        ctx(database, client),
        event_id,
        ACCOUNT_IG_ID,
        SENDER,
        f"{POSTBACK_PREFIX}:{armed['rule_id']}:{comment_id}",
    )

    assert tapped["status"] == "awaiting_email"
    assert [m["text"] for m in client.sent_direct_messages] == ["Drop your email."]
    row = await database["executions"].find_one({"_id": ObjectId(tapped["execution_id"])})
    assert row["status"] == "awaiting_email"
    assert "Here is the guide." not in [m["text"] for m in client.sent_direct_messages]


# --- which template an automation came from ----------------------------------


async def test_the_template_it_came_from_round_trips(signed_in, account_with_post):
    """Stored as the gallery's stable id, never its title: a title is display
    text that can be reworded, and the frontend maps the id back for the label."""
    created = await signed_in.post("/api/rules", json=rule_body(template="collect-emails"))

    assert created.status_code == 201, created.text
    assert created.json()["template"] == "collect-emails"


async def test_a_rule_built_by_hand_records_no_template(signed_in, account_with_post):
    assert (await signed_in.post("/api/rules", json=rule_body())).json()["template"] is None


async def test_editing_an_automation_does_not_blank_its_template(signed_in, account_with_post):
    """The editor PATCHes the whole draft, but a PATCH that omits the field must
    leave it alone -- otherwise every save would erase where the rule came from."""
    rule_id = (
        await signed_in.post("/api/rules", json=rule_body(template="collect-emails"))
    ).json()["id"]

    patched = await signed_in.patch(f"/api/rules/{rule_id}", json={"name": "Renamed"})

    assert patched.json()["template"] == "collect-emails"
