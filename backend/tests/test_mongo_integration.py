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
