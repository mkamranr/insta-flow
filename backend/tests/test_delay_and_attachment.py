"""Reply delay, and sending a file with the payoff."""

import json
from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.config import Settings
from app.services.meta import MockInstagramClient
from app.services.meta.webhooks import POSTBACK_PREFIX
from app.services.security.encryption import encrypt
from app.workers.tasks import process_comment_event, process_postback_event

ACCOUNT_IG_ID = "17841400000000000"
MEDIA_ID = "17900000000000000"
COMMENT_ID = "17981234567890123"
SENDER_ID = "27962242703427275"
KEY = "k" * 64


class FakeRedis:
    """Captures deferred enqueues instead of scheduling them."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.jobs.append({"name": name, "args": args, "defer": kwargs.get("_defer_by")})
        return None


def settings_for() -> Settings:
    return Settings(
        dry_run=False,
        meta_app_secret="s",
        meta_webhook_verify_token="verify",
        session_secret="s" * 64,
        encryption_key=KEY,
        meta_app_id="1",
    )


@pytest.fixture
def client():
    return MockInstagramClient()


@pytest.fixture
def redis():
    return FakeRedis()


def make_ctx(database, client, redis=None):
    ctx = {"db": database, "settings": settings_for(), "client": client, "job_try": 1}
    if redis is not None:
        ctx["redis"] = redis
    return ctx


@pytest.fixture
async def seeded(database):
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
                "name": "Send the guide",
                "scope": "specific",
                "instagram_media_ids": [MEDIA_ID],
                "keywords": ["guide"],
                "message": 'Here it is!',
                "buttons": [],
                "opening_dm": {"enabled": False, "message": "", "button_text": ""},
                "require_follow": {"enabled": False, "prompt": ""},
                "collect_email": {"enabled": False, "prompt": ""},
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
                "text": "guide",
            }
        )
    ).inserted_id
    return {"account_id": account_id, "rule_id": rule_id, "comment_id": comment_id}


async def comment(database, client, redis=None, *, delayed=False):
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
                            "id": COMMENT_ID,
                            "text": "guide",
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
        make_ctx(database, client, redis), str(event_id), ACCOUNT_IG_ID, COMMENT_ID, delayed
    )


# -- reply delay -------------------------------------------------------------


async def test_no_delay_sends_straight_away(database, client, redis, seeded):
    result = await comment(database, client, redis)

    assert result["status"] == "success"
    assert client.sent_private_replies
    assert redis.jobs == []


async def test_a_delay_defers_the_job_and_sends_nothing_yet(database, client, redis, seeded):
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]}, {"$set": {"reply_delay_seconds": 30}}
    )

    result = await comment(database, client, redis)

    assert result["status"] == "scheduled"
    assert result["delay_seconds"] == 30
    assert client.sent_private_replies == [], "nothing may go out on the first pass"
    assert len(redis.jobs) == 1
    assert redis.jobs[0]["name"] == "process_comment_event"
    assert redis.jobs[0]["defer"].total_seconds() == 30


async def test_the_deferred_pass_actually_sends(database, client, redis, seeded):
    """Second time round the flag is set, so it must not re-defer forever."""
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]}, {"$set": {"reply_delay_seconds": 30}}
    )

    result = await comment(database, client, redis, delayed=True)

    assert result["status"] == "success"
    assert client.sent_private_replies
    assert redis.jobs == []


async def test_a_delay_is_ignored_when_there_is_no_queue(database, client, seeded):
    """Driving the task directly must still deliver rather than silently drop."""
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]}, {"$set": {"reply_delay_seconds": 30}}
    )

    result = await comment(database, client, None)

    assert result["status"] == "success"


# -- attachments -------------------------------------------------------------


ATTACHMENT = {
    "type": "file",
    "url": "https://example.com/files/abc/guide-ABCDEFGHIJ.pdf",
    "storage_key": "abc/guide-ABCDEFGHIJ.pdf",
    "filename": "guide-ABCDEFGHIJ.pdf",
    "size_bytes": 1024,
}


async def tap(database, client, seeded):
    return await process_postback_event(
        make_ctx(database, client),
        str(ObjectId()),
        ACCOUNT_IG_ID,
        SENDER_ID,
        f"{POSTBACK_PREFIX}:{seeded['rule_id']}:{COMMENT_ID}",
    )


async def test_the_payoff_carries_the_attachment(database, client, seeded):
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]}, {"$set": {"attachment": ATTACHMENT}}
    )

    result = await tap(database, client, seeded)

    assert result["status"] == "success"
    assert client.sent_attachments == [
        {"recipient_id": SENDER_ID, "type": "file", "url": ATTACHMENT["url"]}
    ]


async def test_the_text_still_goes_out_alongside_it(database, client, seeded):
    """Meta takes the file as its own message, so both are sent."""
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]}, {"$set": {"attachment": ATTACHMENT}}
    )

    await tap(database, client, seeded)

    assert any("Here it is!" in m["text"] for m in client.sent_direct_messages)


async def test_a_rule_without_an_attachment_sends_none(database, client, seeded):
    await tap(database, client, seeded)
    assert client.sent_attachments == []


async def test_a_failed_attachment_does_not_fail_a_delivered_payoff(database, client, seeded):
    """The text and its buttons already arrived.

    Marking the execution failed would report a delivered message as undelivered
    and, with once_per_person off, invite a duplicate on the next comment.
    """
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]}, {"$set": {"attachment": ATTACHMENT}}
    )
    client.fail("send_attachment", 500)

    result = await tap(database, client, seeded)

    assert result["status"] == "success"
    execution = await database["executions"].find_one({"rule_id": seeded["rule_id"]})
    assert execution["status"] == "success"
