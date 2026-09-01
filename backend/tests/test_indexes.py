"""The index definitions are load-bearing: they are the duplicate-protection
authority (§25, §63), not a performance detail. These assertions pin the three
unique indexes the worker depends on."""

from app.db.indexes import COLLECTIONS, INDEXES


def index_named(collection: str, name: str) -> dict:
    for model in INDEXES[collection]:
        if model.document["name"] == name:
            return model.document
    raise AssertionError(f"{collection} has no index named {name}")


def test_every_specified_collection_is_present():
    assert len(COLLECTIONS) == 11
    assert set(INDEXES) <= set(COLLECTIONS)


def test_webhook_idempotency_index_is_unique():
    doc = index_named("webhook_events", "uq_event_hash")
    assert doc["unique"] is True
    assert list(doc["key"]) == ["event_hash"]


def test_comment_idempotency_index_is_unique_and_compound():
    doc = index_named("comments", "uq_account_comment")
    assert doc["unique"] is True
    assert list(doc["key"]) == ["instagram_account_id", "instagram_comment_id"]


def test_execution_reservation_index_is_unique_and_compound():
    """This is what stops two concurrent workers both sending a private reply."""
    doc = index_named("executions", "uq_comment_rule_action")
    assert doc["unique"] is True
    assert list(doc["key"]) == ["comment_id", "rule_id", "action"]


def test_user_email_is_unique():
    assert index_named("users", "uq_email")["unique"] is True


def test_instagram_account_id_is_unique():
    assert index_named("instagram_accounts", "uq_instagram_user_id")["unique"] is True


def test_media_account_media_pair_is_unique():
    assert index_named("media", "uq_account_media")["unique"] is True


# --- obsolete index cleanup -------------------------------------------------


async def test_a_renamed_index_is_dropped_on_startup(database):
    """Mongo keeps an index whose definition has vanished from the code.

    The campaigns->rules rename left uq_comment_campaign_action in place. With
    campaign_id now absent from every document it enforced uniqueness on
    (comment_id, action) alone, which is not what anything asked for.
    """
    from pymongo import ASCENDING

    from app.db.indexes import create_indexes, drop_obsolete_indexes

    await database["executions"].create_index(
        [("comment_id", ASCENDING), ("campaign_id", ASCENDING), ("action", ASCENDING)],
        unique=True,
        name="uq_comment_campaign_action",
    )
    assert "uq_comment_campaign_action" in await database["executions"].index_information()

    dropped = await drop_obsolete_indexes(database)
    assert "executions.uq_comment_campaign_action" in dropped
    assert "uq_comment_campaign_action" not in await database["executions"].index_information()

    # The one we actually want survives.
    await create_indexes(database)
    assert "uq_comment_rule_action" in await database["executions"].index_information()


async def test_the_cleanup_is_idempotent(database):
    from app.db.indexes import drop_obsolete_indexes

    assert await drop_obsolete_indexes(database) == []
