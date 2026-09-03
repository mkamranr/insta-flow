"""Integration tests against a real MongoDB.

Skipped automatically when Mongo is unreachable, so `pytest` still works outside
Docker. Inside `make test` these run and are what actually prove the unique
indexes enforce idempotency — the pure tests only prove the definitions exist.
"""

import os

import pytest
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.db.indexes import COLLECTIONS, create_indexes
from app.db.repositories.comments import CommentsRepository
from app.db.repositories.executions import ExecutionsRepository
from app.db.repositories.webhook_events import WebhookEventsRepository

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongodb:27017")
DB_NAME = "instaflow_test"


@pytest.fixture
async def database():
    client = AsyncMongoClient(MONGO_URL, tz_aware=True, serverSelectionTimeoutMS=1500)
    try:
        await client.admin.command("ping")
    except Exception:
        pytest.skip(f"MongoDB not reachable at {MONGO_URL}")
    await client.drop_database(DB_NAME)
    db = client[DB_NAME]
    await create_indexes(db)
    try:
        yield db
    finally:
        await client.drop_database(DB_NAME)
        await client.close()


async def test_all_indexes_are_created(database):
    for collection in COLLECTIONS:
        info = await database[collection].index_information()
        assert "_id_" in info


async def test_duplicate_webhook_delivery_is_recorded_once(database):
    repo = WebhookEventsRepository(database)
    body = b'{"object":"instagram","entry":[{"id":"1"}]}'

    first, is_new = await repo.record(
        raw_body=body, payload={"object": "instagram"}, event_type="comments"
    )
    assert is_new is True

    second, is_new_again = await repo.record(
        raw_body=body, payload={"object": "instagram"}, event_type="comments"
    )
    assert is_new_again is False
    assert second["_id"] == first["_id"]
    assert await repo.count() == 1


async def test_duplicate_comment_insert_returns_the_existing_row(database):
    repo = CommentsRepository(database)
    account_id = ObjectId()
    doc = {
        "instagram_account_id": account_id,
        "instagram_comment_id": "17900000000000000",
        "text": "send me the tools",
    }

    first, created = await repo.insert_unique(dict(doc))
    assert created is True

    second, created_again = await repo.insert_unique(dict(doc))
    assert created_again is False
    assert second["_id"] == first["_id"]
    assert await repo.count() == 1


async def test_execution_reservation_is_granted_exactly_once(database):
    """§63: the second worker to arrive must get None and send nothing."""
    repo = ExecutionsRepository(database)
    ids = {
        "instagram_account_id": ObjectId(),
        "comment_id": ObjectId(),
        "rule_id": ObjectId(),
        "action": "private_reply",
    }

    first = await repo.reserve(**ids, dry_run=True)
    assert first is not None

    second = await repo.reserve(**ids, dry_run=True)
    assert second is None, "a duplicate job must not obtain a second reservation"
    assert await repo.count() == 1


async def test_a_different_action_gets_its_own_reservation(database):
    """Private and public replies are independent actions (§86)."""
    repo = ExecutionsRepository(database)
    ids = {
        "instagram_account_id": ObjectId(),
        "comment_id": ObjectId(),
        "rule_id": ObjectId(),
    }

    assert await repo.reserve(**ids, action="private_reply", dry_run=True) is not None
    assert await repo.reserve(**ids, action="public_reply", dry_run=True) is not None
    assert await repo.count() == 2


async def test_completing_an_execution_records_the_meta_message_id(database):
    repo = ExecutionsRepository(database)
    reservation = await repo.reserve(
        instagram_account_id=ObjectId(),
        comment_id=ObjectId(),
        rule_id=ObjectId(),
        action="private_reply",
        dry_run=False,
    )
    updated = await repo.complete(
        reservation["_id"],
        status="success",
        message_text="Hey john123!",
        meta_message_id="aWdfZ...",
        meta_recipient_id="526...",
    )
    assert updated["status"] == "success"
    assert updated["meta_message_id"] == "aWdfZ..."
    assert updated["completed_at"] is not None


async def test_unknown_execution_status_is_rejected(database):
    repo = ExecutionsRepository(database)
    reservation = await repo.reserve(
        instagram_account_id=ObjectId(),
        comment_id=ObjectId(),
        rule_id=ObjectId(),
        action="private_reply",
        dry_run=True,
    )
    with pytest.raises(ValueError):
        await repo.complete(reservation["_id"], status="sent_probably")


# -- the reservation key, after the partial-index change ---------------------


async def test_existing_comment_executions_survive_the_index_change(database):
    """No migration was performed, so old rows must still be protected.

    Every row written before this change has a real ObjectId comment_id, which
    is exactly what the partial filter selects. If that assumption were wrong,
    a Meta redelivery of an old comment would send a second DM.
    """
    from bson import ObjectId

    from app.db.indexes import create_indexes
    from app.db.repositories import ExecutionsRepository

    account_id, rule_id, comment_id = ObjectId(), ObjectId(), ObjectId()
    await database["executions"].insert_one(
        {
            "instagram_account_id": account_id,
            "comment_id": comment_id,
            "rule_id": rule_id,
            "action": "private_reply",
            "status": "success",
        }
    )

    await create_indexes(database)

    names = set(await database["executions"].index_information())
    assert "uq_comment_rule_action" not in names, "the non-partial predecessor is gone"
    assert {"uq_comment_rule_action_v2", "uq_message_rule_action"} <= names

    repo = ExecutionsRepository(database)
    again = await repo.reserve(
        instagram_account_id=account_id,
        comment_id=comment_id,
        rule_id=rule_id,
        action="private_reply",
        dry_run=False,
    )
    assert again is None, "a redelivered old comment must not earn a second reservation"


async def test_two_different_messages_on_one_rule_both_reserve(database):
    """The regression the naive design would have shipped.

    Keyed on a null comment_id these would collide, the second person would
    silently never be DMed, and it would look like duplicate suppression.
    """
    from bson import ObjectId

    from app.db.repositories import ExecutionsRepository

    repo = ExecutionsRepository(database)
    account_id, rule_id = ObjectId(), ObjectId()

    first = await repo.reserve(
        instagram_account_id=account_id,
        trigger_message_id="mid_aaa",
        rule_id=rule_id,
        action="direct_message",
        dry_run=False,
    )
    second = await repo.reserve(
        instagram_account_id=account_id,
        trigger_message_id="mid_bbb",
        rule_id=rule_id,
        action="direct_message",
        dry_run=False,
    )

    assert first is not None
    assert second is not None


async def test_the_same_message_reserves_only_once(database):
    from bson import ObjectId

    from app.db.repositories import ExecutionsRepository

    repo = ExecutionsRepository(database)
    account_id, rule_id = ObjectId(), ObjectId()
    kwargs = dict(
        instagram_account_id=account_id,
        trigger_message_id="mid_same",
        rule_id=rule_id,
        action="direct_message",
        dry_run=False,
    )

    assert await repo.reserve(**kwargs) is not None
    assert await repo.reserve(**kwargs) is None


async def test_a_comment_and_a_message_reservation_can_coexist(database):
    """Different domains, different indexes; neither may block the other."""
    from bson import ObjectId

    from app.db.repositories import ExecutionsRepository

    repo = ExecutionsRepository(database)
    account_id, rule_id = ObjectId(), ObjectId()

    by_comment = await repo.reserve(
        instagram_account_id=account_id,
        comment_id=ObjectId(),
        rule_id=rule_id,
        action="direct_message",
        dry_run=False,
    )
    by_message = await repo.reserve(
        instagram_account_id=account_id,
        trigger_message_id="mid_ccc",
        rule_id=rule_id,
        action="direct_message",
        dry_run=False,
    )

    assert by_comment is not None
    assert by_message is not None


async def test_the_absent_key_is_omitted_rather_than_written_as_null(database):
    """A null would land the row in the other partial index's null bucket."""
    from bson import ObjectId

    from app.db.repositories import ExecutionsRepository

    repo = ExecutionsRepository(database)
    reserved = await repo.reserve(
        instagram_account_id=ObjectId(),
        trigger_message_id="mid_ddd",
        rule_id=ObjectId(),
        action="direct_message",
        dry_run=False,
    )

    row = await database["executions"].find_one({"_id": reserved["_id"]})
    assert "comment_id" not in row


async def test_reserving_with_neither_or_both_keys_is_refused(database):
    """A row in neither unique index has no duplicate protection at all, and
    nothing would report that. Refuse loudly instead."""
    import pytest
    from bson import ObjectId

    from app.db.repositories import ExecutionsRepository

    repo = ExecutionsRepository(database)
    base = dict(
        instagram_account_id=ObjectId(), rule_id=ObjectId(), action="direct_message", dry_run=False
    )

    with pytest.raises(ValueError):
        await repo.reserve(**base)
    with pytest.raises(ValueError):
        await repo.reserve(comment_id=ObjectId(), trigger_message_id="mid_eee", **base)
