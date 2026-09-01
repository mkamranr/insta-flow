"""The "collect email before sending" flow."""

from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.config import Settings
from app.services.contacts import extract_email
from app.services.meta import MockInstagramClient
from app.services.meta.webhooks import parse_message_events
from app.services.security.encryption import encrypt
from app.workers.tasks import process_message_event

ACCOUNT_IG_ID = "17841400000000000"
SENDER = "17800000000000000"


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
    """InstaFlow is not a chatbot; it must not answer unrelated conversation."""
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
