"""Auto DM pause: nothing automated leaves while a pause is running, and what it
held can be delivered afterwards on the operator's say-so."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.config import Settings
from app.constants import PRIVATE_REPLY_CALLS_PER_HOUR
from app.db.repositories.executions import AUTOMATION_PAUSED as REPO_AUTOMATION_PAUSED
from app.db.repositories.executions import ExecutionsRepository
from app.services.campaigns.policy import AUTOMATION_PAUSED
from app.services.meta import MockInstagramClient
from app.services.meta.webhooks import POSTBACK_PREFIX
from app.services.security.encryption import encrypt
from app.workers.tasks import (
    MAX_PAUSED_REPLAY,
    PAUSED_REPLAY_SPACING_SECONDS,
    deliver_paused_backlog,
    process_comment_event,
    process_message_event,
    process_postback_event,
    recheck_follow,
)

ACCOUNT_IG_ID = "17841400000000000"
SENDER_ID = "27962242703427275"
MEDIA_ID = "17890000000000000"
COMMENT_ID = "17900000000000000"
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


class FakeRedis:
    """Captures enqueues instead of scheduling them."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.jobs.append(
            {
                "name": name,
                "args": args,
                "defer": kwargs.get("_defer_by"),
                "job_id": kwargs.get("_job_id"),
            }
        )
        return None


@pytest.fixture
def client():
    return MockInstagramClient()


@pytest.fixture
def redis():
    return FakeRedis()


def make_ctx(database, client, redis=None, *, dry_run=False):
    return {
        "db": database,
        "settings": settings_for(dry_run),
        "client": client,
        "redis": redis,
        "job_try": 1,
        "job_id": "test-job",
    }


@pytest.fixture
async def seeded(database):
    """An account with a post, a comment rule on it, and a DM-keyword rule."""
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
    await database["media"].insert_one(
        {
            "instagram_account_id": account_id,
            "instagram_media_id": MEDIA_ID,
            "caption": "5 AI tools every developer should know",
        }
    )
    base = {
        "instagram_account_id": account_id,
        "keywords": ["link"],
        "match_mode": "contains",
        "message": "Here it is: https://example.com/tools",
        "buttons": [],
        "opening_dm": {"enabled": False, "message": "", "button_text": ""},
        "require_follow": {"enabled": False, "prompt": ""},
        "collect_email": {"enabled": False, "prompt": ""},
        "once_per_person": False,
        "enabled": True,
        "priority": 100,
        "limits": {"per_minute": 10, "per_hour": 100, "per_day": 500},
    }
    rule_id = (
        await database["rules"].insert_one(
            {**base, "name": "Tools link", "scope": "specific", "instagram_media_ids": [MEDIA_ID]}
        )
    ).inserted_id
    dm_rule_id = (
        await database["rules"].insert_one(
            {
                **base,
                "name": "DM keyword",
                "trigger": "dm_keyword",
                "scope": "any",
                "instagram_media_ids": [],
            }
        )
    ).inserted_id
    return {"account_id": account_id, "rule_id": rule_id, "dm_rule_id": dm_rule_id}


async def pause_for(database, seeded, *, minutes=30, backlog_since=None):
    """Hold this account the way the API route does."""
    now = datetime.now(UTC)
    await database["instagram_accounts"].update_one(
        {"_id": seeded["account_id"]},
        {
            "$set": {
                "automation_paused_until": now + timedelta(minutes=minutes),
                "automation_paused_at": now,
                "automation_backlog_since": backlog_since,
            }
        },
    )


async def use_opening_dm(database, seeded):
    """Switch the rule to the two-step flow, which is the only one with a button.

    It matters for the reservation as well as the message: the comment then holds
    `opening_dm` and the tap holds `private_reply`, so the two do not collide.
    """
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]},
        {
            "$set": {
                "opening_dm": {
                    "enabled": True,
                    "message": "Want the link?",
                    "button_text": "Send it",
                }
            }
        },
    )


async def resume(database, seeded):
    await database["instagram_accounts"].update_one(
        {"_id": seeded["account_id"]},
        {"$set": {"automation_paused_until": datetime.now(UTC)}},
    )


# --- driving each entry point -----------------------------------------------


def comment_payload(*, text="send me the link", comment_id=COMMENT_ID, age=None):
    when = datetime.now(UTC) - (age or timedelta(minutes=1))
    return {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_IG_ID,
                "time": int(when.timestamp()),
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "from": {"id": SENDER_ID, "username": "john123"},
                            "comment_id": comment_id,
                            "text": text,
                            "media": {"id": MEDIA_ID, "media_product_type": "REELS"},
                        },
                    }
                ],
            }
        ],
    }


async def store_event(database, payload):
    return str(
        (
            await database["webhook_events"].insert_one(
                {
                    "event_hash": str(ObjectId()),
                    "payload": payload,
                    "raw_body": json.dumps(payload),
                    "status": "received",
                    "received_at": datetime.now(UTC),
                }
            )
        ).inserted_id
    )


async def a_comment(database, client, redis=None, *, comment_id=COMMENT_ID, age=None, **kwargs):
    payload = comment_payload(comment_id=comment_id, age=age)
    event_id = await store_event(database, payload)
    return await process_comment_event(
        make_ctx(database, client, redis, **kwargs), event_id, ACCOUNT_IG_ID, comment_id
    )


async def a_tap(database, client, seeded, **kwargs):
    return await process_postback_event(
        make_ctx(database, client, **kwargs),
        await store_event(database, {}),
        ACCOUNT_IG_ID,
        SENDER_ID,
        f"{POSTBACK_PREFIX}:{seeded['rule_id']}:{COMMENT_ID}",
    )


async def a_message(database, client, *, text="link", mid="mid_1", **kwargs):
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_IG_ID,
                "time": int(datetime.now(UTC).timestamp() * 1000),
                "messaging": [
                    {
                        "sender": {"id": SENDER_ID},
                        "recipient": {"id": ACCOUNT_IG_ID},
                        "timestamp": int(datetime.now(UTC).timestamp() * 1000),
                        "message": {"mid": mid, "text": text},
                    }
                ],
            }
        ],
    }
    return await process_message_event(
        make_ctx(database, client, **kwargs),
        await store_event(database, payload),
        ACCOUNT_IG_ID,
        SENDER_ID,
        text,
        mid,
    )


# --- the guard --------------------------------------------------------------


ENTRY_POINTS = ("comment", "postback", "dm_keyword", "email_reply", "follow_recheck")


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
async def test_no_worker_entry_point_sends_while_paused(database, client, seeded, entry_point):
    """The guard against a future send site quietly missing the pause.

    Every way a message can leave this app is driven here, and none of them may
    call Meta while a pause is running.
    """
    if entry_point == "comment":
        await pause_for(database, seeded)
        await a_comment(database, client)

    elif entry_point == "postback":
        await use_opening_dm(database, seeded)
        await a_comment(database, client)  # the opener goes out before the pause
        await pause_for(database, seeded)
        client.calls.clear()  # `mutation_calls` is derived from this
        await a_tap(database, client, seeded)

    elif entry_point == "dm_keyword":
        await pause_for(database, seeded)
        await a_message(database, client)

    elif entry_point == "email_reply":
        await database["executions"].insert_one(
            {
                "instagram_account_id": seeded["account_id"],
                "rule_id": seeded["rule_id"],
                "instagram_user_id": SENDER_ID,
                "status": "awaiting_email",
                "action": "private_reply",
                "created_at": datetime.now(UTC),
            }
        )
        await pause_for(database, seeded)
        await a_message(database, client, text="me@example.com", mid="mid_email")

    elif entry_point == "follow_recheck":
        execution_id = (
            await database["executions"].insert_one(
                {
                    "instagram_account_id": seeded["account_id"],
                    "rule_id": seeded["rule_id"],
                    "instagram_user_id": SENDER_ID,
                    "comment_id": (
                        await database["comments"].insert_one(
                            {
                                "instagram_account_id": seeded["account_id"],
                                "instagram_comment_id": COMMENT_ID,
                                "instagram_user_id": SENDER_ID,
                                "text": "link",
                            }
                        )
                    ).inserted_id,
                    "status": "awaiting_follow",
                    "action": "private_reply",
                    "follow_deadline": datetime.now(UTC) + timedelta(hours=20),
                    "created_at": datetime.now(UTC),
                }
            )
        ).inserted_id
        await pause_for(database, seeded)
        await recheck_follow(make_ctx(database, client, FakeRedis()), str(execution_id), 0)

    assert client.mutation_calls == [], f"{entry_point} sent something while paused"


async def test_a_held_comment_is_recorded_with_a_machine_readable_reason(database, client, seeded):
    """Held, not lost: the Activity log has to explain it months later."""
    await pause_for(database, seeded)

    result = await a_comment(database, client)

    assert result == {"status": "skipped", "reason": AUTOMATION_PAUSED}
    row = await database["executions"].find_one({"rule_id": seeded["rule_id"]})
    assert row["status"] == "skipped"
    assert row["policy_decision"] == AUTOMATION_PAUSED
    assert row["comment_id"] is not None


async def test_a_held_tap_records_nothing_so_the_button_still_works(database, client, seeded):
    """A terminal row here would make the button permanently dead.

    Nothing is recorded for a tap made during a pause, so the tap they make
    afterwards is an ordinary first tap and delivers the payoff.
    """
    await use_opening_dm(database, seeded)
    await a_comment(database, client)
    await pause_for(database, seeded)

    assert (await a_tap(database, client, seeded))["reason"] == AUTOMATION_PAUSED
    assert client.sent_direct_messages == []
    assert await database["executions"].count_documents({"action": "private_reply"}) == 0

    await resume(database, seeded)
    assert (await a_tap(database, client, seeded))["status"] == "success"
    assert len(client.sent_direct_messages) == 1


async def test_a_volunteered_email_is_kept_even_though_the_payoff_is_held(database, client, seeded):
    """An address nobody will offer twice is worth more than a prompt reply."""
    await database["executions"].insert_one(
        {
            "instagram_account_id": seeded["account_id"],
            "rule_id": seeded["rule_id"],
            "instagram_user_id": SENDER_ID,
            "status": "awaiting_email",
            "action": "private_reply",
            "created_at": datetime.now(UTC),
        }
    )
    await pause_for(database, seeded)

    result = await a_message(database, client, text="me@example.com", mid="mid_email")

    assert result["email_captured"] is True
    contact = await database["contacts"].find_one({"instagram_user_id": SENDER_ID})
    assert contact["email"] == "me@example.com"
    row = await database["executions"].find_one({"instagram_user_id": SENDER_ID})
    assert row["status"] == "awaiting_email", "must stay parked so a later message resumes it"


async def test_a_held_follow_recheck_reschedules_instead_of_sending(database, client, seeded):
    comment_id = (
        await database["comments"].insert_one(
            {
                "instagram_account_id": seeded["account_id"],
                "instagram_comment_id": COMMENT_ID,
                "instagram_user_id": SENDER_ID,
                "text": "link",
            }
        )
    ).inserted_id
    execution_id = (
        await database["executions"].insert_one(
            {
                "instagram_account_id": seeded["account_id"],
                "rule_id": seeded["rule_id"],
                "comment_id": comment_id,
                "instagram_user_id": SENDER_ID,
                "status": "awaiting_follow",
                "action": "private_reply",
                "follow_deadline": datetime.now(UTC) + timedelta(hours=20),
                "created_at": datetime.now(UTC),
            }
        )
    ).inserted_id
    await pause_for(database, seeded)
    redis = FakeRedis()

    result = await recheck_follow(make_ctx(database, client, redis), str(execution_id), 0)

    assert result["reason"] == AUTOMATION_PAUSED
    assert [job["name"] for job in redis.jobs] == ["recheck_follow"]
    row = await database["executions"].find_one({"_id": execution_id})
    assert row["status"] == "awaiting_follow"


async def test_a_held_send_does_not_make_someone_look_already_messaged(database, client, seeded):
    """`already_sent_to` counts success and dry_run; a held row is neither."""
    await pause_for(database, seeded)
    await a_comment(database, client)

    executions = ExecutionsRepository(database)
    assert await executions.already_sent_to(seeded["rule_id"], SENDER_ID) is False


# --- delivering the backlog -------------------------------------------------


async def drain(database, seeded, redis, client=None):
    """Fan out, then run every replay job the fan-out queued."""
    result = await deliver_paused_backlog(
        make_ctx(database, client or MockInstagramClient(), redis), str(seeded["account_id"])
    )
    for job in list(redis.jobs):
        if job["name"] == "process_comment_event":
            await process_comment_event(
                make_ctx(database, client or MockInstagramClient(), redis), *job["args"]
            )
    return result


async def test_the_backlog_replay_delivers_a_held_comment(database, client, seeded, redis):
    await pause_for(database, seeded)
    await a_comment(database, client)
    await resume(database, seeded)

    await deliver_paused_backlog(make_ctx(database, client, redis), str(seeded["account_id"]))
    job = redis.jobs[0]
    await process_comment_event(make_ctx(database, client, redis), *job["args"])

    assert len(client.sent_private_replies) == 1
    row = await database["executions"].find_one({"rule_id": seeded["rule_id"]})
    assert row["status"] == "success"


async def test_replaying_the_same_row_twice_sends_once(database, client, seeded, redis):
    """The compare-and-swap claim. Two jobs reach the row; one wins."""
    await pause_for(database, seeded)
    await a_comment(database, client)
    await resume(database, seeded)

    await deliver_paused_backlog(make_ctx(database, client, redis), str(seeded["account_id"]))
    args = redis.jobs[0]["args"]
    await process_comment_event(make_ctx(database, client, redis), *args)
    second = await process_comment_event(make_ctx(database, client, redis), *args)

    assert second["status"] == "duplicate"
    assert len(client.sent_private_replies) == 1


async def test_a_replay_re_applies_every_other_gate(database, client, seeded, redis):
    """A comment that aged past Meta's 7-day window during the pause is not sent."""
    await pause_for(database, seeded)
    await a_comment(database, client, age=timedelta(days=8))
    await resume(database, seeded)

    await drain(database, seeded, redis, client)

    assert client.mutation_calls == []
    row = await database["executions"].find_one({"rule_id": seeded["rule_id"]})
    assert row["policy_decision"] == "outside_reply_window"


async def test_a_replay_whose_rule_was_deleted_sends_nothing(database, client, seeded, redis):
    await pause_for(database, seeded)
    await a_comment(database, client)
    await resume(database, seeded)
    await database["rules"].delete_one({"_id": seeded["rule_id"]})

    await drain(database, seeded, redis, client)

    assert client.mutation_calls == []


async def test_a_replay_whose_media_was_deleted_still_renders(database, client, seeded, redis):
    """`{{post_caption}}` goes empty rather than raising."""
    await pause_for(database, seeded)
    await a_comment(database, client)
    await resume(database, seeded)
    await database["media"].delete_many({})

    await drain(database, seeded, redis, client)

    assert len(client.sent_private_replies) == 1


async def test_a_replay_in_dry_run_records_dry_run_and_calls_nothing(database, seeded, redis):
    client = MockInstagramClient()
    await pause_for(database, seeded)
    await a_comment(database, client, dry_run=True)
    await resume(database, seeded)

    await deliver_paused_backlog(
        make_ctx(database, client, redis, dry_run=True), str(seeded["account_id"])
    )
    await process_comment_event(
        make_ctx(database, client, redis, dry_run=True), *redis.jobs[0]["args"]
    )

    assert client.mutation_calls == [], "DRY_RUN must never call a Meta mutation"
    row = await database["executions"].find_one({"rule_id": seeded["rule_id"]})
    assert row["status"] == "dry_run"


async def test_only_comment_triggered_rows_are_replayed(database, client, seeded, redis):
    """A held DM-keyword row is counted, never queued: its 24-hour window is gone."""
    await pause_for(database, seeded)
    await a_comment(database, client)
    await a_message(database, client, mid="mid_dm")
    await resume(database, seeded)

    counts = await ExecutionsRepository(database).pause_backlog_counts(seeded["account_id"])
    result = await deliver_paused_backlog(
        make_ctx(database, client, redis), str(seeded["account_id"])
    )

    assert counts["held"] == 2
    assert result["queued"] == 1
    assert len(redis.jobs) == 1


async def test_each_replay_is_enqueued_under_its_own_job_id(database, client, seeded, redis):
    """Without this a double click would fan out twice, and the two jobs would
    both find the row mid-flight and both send."""
    await pause_for(database, seeded)
    for n in range(3):
        await a_comment(database, client, comment_id=f"{COMMENT_ID}{n}")
    await resume(database, seeded)

    await deliver_paused_backlog(make_ctx(database, client, redis), str(seeded["account_id"]))

    job_ids = [job["job_id"] for job in redis.jobs]
    assert len(set(job_ids)) == 3
    assert all(job_id.startswith("paused-replay:") for job_id in job_ids)


async def test_replays_are_spaced_under_metas_hourly_ceiling(database, client, seeded, redis):
    await pause_for(database, seeded)
    for n in range(3):
        await a_comment(database, client, comment_id=f"{COMMENT_ID}{n}")
    await resume(database, seeded)

    await deliver_paused_backlog(make_ctx(database, client, redis), str(seeded["account_id"]))

    defers = [job["defer"].total_seconds() for job in redis.jobs]
    assert defers == sorted(defers)
    gaps = [b - a for a, b in zip(defers, defers[1:], strict=False)]
    assert all(gap >= 3600 / PRIVATE_REPLY_CALLS_PER_HOUR for gap in gaps)


async def test_the_backlog_window_retires_what_was_answered(database, client, seeded, redis):
    """Delivering settles the offer, so the page stops asking."""
    await pause_for(database, seeded)
    await a_comment(database, client)
    await resume(database, seeded)

    await deliver_paused_backlog(make_ctx(database, client, redis), str(seeded["account_id"]))

    account = await database["instagram_accounts"].find_one({"_id": seeded["account_id"]})
    counts = await ExecutionsRepository(database).pause_backlog_counts(
        seeded["account_id"], since=account["automation_backlog_since"]
    )
    assert counts["held"] == 0


async def test_a_row_that_can_never_be_replayed_stops_being_offered(
    database, client, seeded, redis
):
    """Its webhook event is gone, so it can never be re-parsed. The window is
    what retires it -- otherwise the operator is asked about it forever."""
    await pause_for(database, seeded)
    await a_comment(database, client)
    await resume(database, seeded)
    await database["webhook_events"].delete_many({})

    result = await deliver_paused_backlog(
        make_ctx(database, client, redis), str(seeded["account_id"])
    )

    assert result == {"queued": 0, "held": 1}
    account = await database["instagram_accounts"].find_one({"_id": seeded["account_id"]})
    counts = await ExecutionsRepository(database).pause_backlog_counts(
        seeded["account_id"], since=account["automation_backlog_since"]
    )
    assert counts["held"] == 0


async def test_the_repository_and_policy_agree_on_the_reason_code():
    """The repository keeps its own copy to avoid importing services. Pin them."""
    assert REPO_AUTOMATION_PAUSED == AUTOMATION_PAUSED


def test_one_pass_cannot_outspend_metas_hourly_budget():
    """The cap and the spacing have to agree: a pass may queue no more sends than
    Meta allows in an hour, and its deferred tail must land inside that hour."""
    assert PAUSED_REPLAY_SPACING_SECONDS >= 3600 / PRIVATE_REPLY_CALLS_PER_HOUR
    assert MAX_PAUSED_REPLAY <= PRIVATE_REPLY_CALLS_PER_HOUR
    assert MAX_PAUSED_REPLAY * PAUSED_REPLAY_SPACING_SECONDS <= 3600
