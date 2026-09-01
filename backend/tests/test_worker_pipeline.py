"""Phase 7. Covers the six cases §80 requires, and the M3 exit criterion: a
comment posted to the webhook produces exactly one execution, end to end."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from arq.worker import Retry
from bson import ObjectId

from app.config import Settings
from app.services.meta import MockInstagramClient, SimulatedTimeout
from app.services.meta.webhooks import sign
from app.services.security.encryption import encrypt
from app.workers.tasks import RETRY_DELAYS, process_comment_event, retry_delay

ACCOUNT_IG_ID = "17841400000000000"
COMMENTER_IG_ID = "17800000000000000"
COMMENT_ID = "17900000000000000"
MEDIA_ID = "17890000000000000"

SECRET = "webhook-test-secret"


def payload(*, text="Can you send me the tools?", comment_id=COMMENT_ID, from_id=COMMENTER_IG_ID):
    return {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_IG_ID,
                "time": int(datetime.now(UTC).timestamp()),
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "from": {"id": from_id, "username": "john123"},
                            "comment_id": comment_id,
                            "text": text,
                            "media": {"id": MEDIA_ID, "media_product_type": "REELS"},
                        },
                    }
                ],
            }
        ],
    }


def settings_for(dry_run: bool) -> Settings:
    return Settings(
        dry_run=dry_run,
        meta_app_secret=SECRET,
        meta_webhook_verify_token="verify",
        session_secret="s" * 64,
        encryption_key="k" * 64,
        meta_app_id="1",
    )


@pytest.fixture
async def seeded(database):
    """An account, a post, and a rule armed on that post."""
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "instagram_user_id": ACCOUNT_IG_ID,
            "username": "myshop",
            "status": "connected",
            # Encrypted with the same key settings_for() uses, so the worker can
            # decrypt it the way it does in production.
            "access_token_encrypted": encrypt("test-access-token", "k" * 64),
        }
    )
    await database["media"].insert_one(
        {
            "instagram_account_id": account_id,
            "instagram_media_id": MEDIA_ID,
            "caption": "5 AI tools every developer should know",
        }
    )
    rule_id = (
        await database["rules"].insert_one(
            {
                "instagram_account_id": account_id,
                "name": "Tools link",
                "scope": "specific",
                "instagram_media_ids": [MEDIA_ID],
                "keywords": ["tools"],
                "any_word": False,
                "match_mode": "contains",
                "case_sensitive": False,
                "message": "Hey {{username}}! Here's the list: {{link}}",
                "buttons": [{"title": "Open", "url": "https://example.com/ai-tools"}],
                "once_per_person": False,
                "enabled": True,
                "priority": 100,
                "limits": {"per_minute": 10, "per_hour": 100, "per_day": 500},
            }
        )
    ).inserted_id
    return {"account_id": account_id, "rule_id": rule_id}


@pytest.fixture
def client():
    return MockInstagramClient()


def make_ctx(database, client, *, dry_run=False, job_try=1):
    return {
        "db": database,
        "settings": settings_for(dry_run),
        "client": client,
        "job_try": job_try,
        "job_id": "test-job",
    }


async def store_event(database, body_payload):
    """Persist a webhook event the way the endpoint would."""
    raw = json.dumps(body_payload).encode()
    result = await database["webhook_events"].insert_one(
        {
            "event_hash": sign(raw, SECRET),
            "event_type": "comments",
            "payload": body_payload,
            "status": "received",
            "received_at": datetime.now(UTC),
        }
    )
    return str(result.inserted_id)


# --- retry schedule ---------------------------------------------------------


def test_retry_delays_follow_the_documented_schedule():
    for attempt, base in enumerate(RETRY_DELAYS, start=1):
        delay = retry_delay(attempt)
        assert base <= delay <= base + max(base * 0.25, 0.5)


def test_retry_delay_is_jittered():
    """Identical delays would resynchronise a burst of failures."""
    assert len({retry_delay(5) for _ in range(20)}) > 1


def test_retry_delay_is_clamped_beyond_the_last_attempt():
    assert retry_delay(99) >= RETRY_DELAYS[-1]


# --- REQUIRED: successful job ----------------------------------------------


async def test_a_matching_comment_sends_and_records_success(database, seeded, client):
    event_id = await store_event(database, payload())
    result = await process_comment_event(
        make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )

    assert result["status"] == "success"
    assert len(client.sent_private_replies) == 1
    assert client.sent_private_replies[0]["text"] == (
        "Hey john123! Here's the list: https://example.com/ai-tools"
    )

    execution = await database["executions"].find_one({})
    assert execution["status"] == "success"
    assert execution["meta_message_id"]
    assert execution["attempts"] == 1

    comment = await database["comments"].find_one({})
    assert comment["normalized_text"] == "can you send me the tools?"


# --- dry run ----------------------------------------------------------------


async def test_dry_run_records_an_execution_but_sends_nothing(database, seeded, client):
    event_id = await store_event(database, payload())
    result = await process_comment_event(
        make_ctx(database, client, dry_run=True), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )

    assert result["status"] == "dry_run"
    assert client.mutation_calls == [], "DRY_RUN must never call a Meta mutation"

    execution = await database["executions"].find_one({})
    assert execution["status"] == "dry_run"
    assert execution["dry_run"] is True
    # The message is still rendered and stored, so it can be reviewed.
    assert execution["message_text"].startswith("Hey john123!")


# --- REQUIRED: duplicate job ------------------------------------------------


async def test_running_the_same_job_twice_sends_once(database, seeded, client):
    event_id = await store_event(database, payload())
    ctx = make_ctx(database, client)

    first = await process_comment_event(ctx, event_id, ACCOUNT_IG_ID, COMMENT_ID)
    second = await process_comment_event(ctx, event_id, ACCOUNT_IG_ID, COMMENT_ID)

    assert first["status"] == "success"
    assert second["status"] == "duplicate"
    assert len(client.sent_private_replies) == 1
    assert await database["executions"].count_documents({}) == 1


# --- REQUIRED: retry --------------------------------------------------------


async def test_a_retryable_error_raises_retry_and_does_not_finalise(database, seeded, client):
    client.fail("send_private_reply", 429)
    event_id = await store_event(database, payload())

    with pytest.raises(Retry):
        await process_comment_event(
            make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID
        )

    execution = await database["executions"].find_one({})
    assert execution["status"] == "processing", "a retryable failure must stay open"
    assert execution["attempts"] == 1


async def test_a_retry_continues_the_same_execution_and_can_succeed(database, seeded, client):
    """Second attempt must reuse the reservation, not create a second one."""
    client.fail("send_private_reply", 500)
    event_id = await store_event(database, payload())

    with pytest.raises(Retry):
        await process_comment_event(
            make_ctx(database, client, job_try=1), event_id, ACCOUNT_IG_ID, COMMENT_ID
        )
    result = await process_comment_event(
        make_ctx(database, client, job_try=2), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )

    assert result["status"] == "success"
    assert await database["executions"].count_documents({}) == 1
    execution = await database["executions"].find_one({})
    assert execution["status"] == "success"
    assert execution["attempts"] == 2


async def test_timeouts_are_retried(database, seeded, client):
    client.fail("send_private_reply", SimulatedTimeout())
    event_id = await store_event(database, payload())
    with pytest.raises(Retry):
        await process_comment_event(
            make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID
        )


async def test_the_final_attempt_fails_instead_of_retrying_forever(database, seeded, client):
    client.fail("send_private_reply", 500, times=10)
    event_id = await store_event(database, payload())

    result = await process_comment_event(
        make_ctx(database, client, job_try=len(RETRY_DELAYS)), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )
    assert result["status"] == "failed"
    assert (await database["executions"].find_one({}))["status"] == "failed"


# --- REQUIRED: permanent error ----------------------------------------------


async def test_a_400_fails_immediately_without_retrying(database, seeded, client):
    client.fail("send_private_reply", 400)
    event_id = await store_event(database, payload())

    result = await process_comment_event(
        make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )
    assert result["status"] == "failed"
    execution = await database["executions"].find_one({})
    assert execution["status"] == "failed"
    assert execution["error"]


async def test_a_401_pauses_the_account(database, seeded, client):
    """Retrying with a dead token can only fail; the dashboard must prompt a reconnect."""
    client.fail("send_private_reply", 401)
    event_id = await store_event(database, payload())

    result = await process_comment_event(
        make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )
    assert result["status"] == "failed"
    account = await database["instagram_accounts"].find_one({"_id": seeded["account_id"]})
    assert account["status"] == "token_error"


# --- REQUIRED: failed job (non-Meta failures) -------------------------------


async def test_an_unmatched_comment_records_no_execution(database, seeded, client):
    event_id = await store_event(database, payload(text="love this post"))
    result = await process_comment_event(
        make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )
    assert result["status"] == "unmatched"
    assert await database["executions"].count_documents({}) == 0
    assert await database["comments"].count_documents({}) == 1


async def test_a_disabled_rule_matches_nothing(database, seeded, client):
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]}, {"$set": {"enabled": False}}
    )
    event_id = await store_event(database, payload())
    result = await process_comment_event(
        make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )
    # A disabled rule is filtered out before selection, so nothing is chosen.
    assert result["status"] == "unmatched"
    assert client.mutation_calls == []


async def test_an_out_of_window_comment_is_skipped_before_calling_meta(database, seeded, client):
    old = payload()
    old["entry"][0]["time"] = int((datetime.now(UTC) - timedelta(days=9)).timestamp())
    event_id = await store_event(database, old)

    result = await process_comment_event(
        make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "outside_reply_window"
    assert client.mutation_calls == [], "a call we know will fail must not be made"

    execution = await database["executions"].find_one({})
    assert execution["status"] == "skipped"
    assert execution["policy_decision"] == "outside_reply_window"


async def test_a_bad_message_fails_without_retrying(database, seeded, client):
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]}, {"$set": {"message": "Hi {{unknown_var}}"}}
    )
    event_id = await store_event(database, payload())

    result = await process_comment_event(
        make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )
    assert result["status"] == "failed"
    assert result["reason"] == "template_error"
    assert client.mutation_calls == []


async def test_a_self_comment_is_skipped_by_username(database, seeded, client):
    """from.id is Instagram-scoped, so the username check is what catches this."""
    own = payload(from_id="some-scoped-id")
    own["entry"][0]["changes"][0]["value"]["from"]["username"] = "myshop"
    event_id = await store_event(database, own)

    result = await process_comment_event(
        make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "self_comment"
    assert client.mutation_calls == []


async def test_an_unknown_account_is_skipped(database, client):
    event_id = await store_event(database, payload())
    result = await process_comment_event(
        make_ctx(database, client), event_id, "99999999999999999", COMMENT_ID
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "unknown_account"


# --- REQUIRED: worker restart behaviour -------------------------------------


async def test_a_job_replayed_after_a_restart_does_not_resend(database, seeded, client):
    """A crash after sending leaves a terminal execution; the replayed job must
    see it and stop, rather than sending a second DM."""
    event_id = await store_event(database, payload())
    await process_comment_event(make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID)

    fresh_client = MockInstagramClient()  # a restarted worker has fresh state
    result = await process_comment_event(
        make_ctx(database, fresh_client), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )

    assert result["status"] == "duplicate"
    assert fresh_client.mutation_calls == []


async def test_a_crash_mid_send_leaves_a_resumable_reservation(database, seeded, client):
    """Interrupted before completion: the execution stays `processing`, and the
    next attempt continues it rather than starting over."""
    client.fail("send_private_reply", SimulatedTimeout())
    event_id = await store_event(database, payload())
    with pytest.raises(Retry):
        await process_comment_event(
            make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID
        )

    result = await process_comment_event(
        make_ctx(database, MockInstagramClient(), job_try=2), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )
    assert result["status"] == "success"
    assert await database["executions"].count_documents({}) == 1


# --- rate limits and cooldown ----------------------------------------------


async def test_a_rule_rate_limit_denies_before_sending(database, seeded, client):
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]}, {"$set": {"limits.per_day": 1}}
    )
    await database["executions"].insert_one(
        {
            "rule_id": seeded["rule_id"],
            "comment_id": ObjectId(),
            "action": "private_reply",
            "status": "success",
            "created_at": datetime.now(UTC),
        }
    )
    event_id = await store_event(database, payload())
    result = await process_comment_event(
        make_ctx(database, client), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )
    assert result["reason"] == "rate_limited_day"
    assert client.mutation_calls == []


async def test_once_per_person_blocks_a_repeat_commenter(database, seeded, client):
    """"Only send this DM once per person", however many times they comment."""
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]}, {"$set": {"once_per_person": True}}
    )
    await database["executions"].insert_one(
        {
            "rule_id": seeded["rule_id"],
            "comment_id": ObjectId(),
            "action": "private_reply",
            "status": "success",
            "instagram_user_id": COMMENTER_IG_ID,
            "created_at": datetime.now(UTC),
        }
    )
    event_id = await store_event(database, payload(comment_id="different-comment"))
    result = await process_comment_event(
        make_ctx(database, client), event_id, ACCOUNT_IG_ID, "different-comment"
    )
    assert result["reason"] == "already_messaged"
    assert client.mutation_calls == []


async def test_an_unknown_account_still_closes_the_webhook_event(database, client):
    """Meta's dashboard test payload uses entry.id "0". An event nothing can act
    on must not sit at `received` forever, looking like an unprocessed backlog."""
    event_id = await store_event(database, payload())
    result = await process_comment_event(
        make_ctx(database, client), event_id, "99999999999999999", COMMENT_ID
    )

    assert result["reason"] == "unknown_account"
    event = await database["webhook_events"].find_one({})
    assert event["status"] == "failed"
    assert event["processed_at"] is not None
    assert "No connected account" in event["error"]
