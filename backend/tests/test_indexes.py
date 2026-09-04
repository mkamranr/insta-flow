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
    assert len(COLLECTIONS) == 16
    assert set(INDEXES) <= set(COLLECTIONS)
    assert "account_snapshots" in COLLECTIONS
    assert "stories" in COLLECTIONS
    assert "upcoming_posts" in COLLECTIONS


def test_only_one_upcoming_post_can_be_pending():
    """Partial on `pending`, not a plain unique index: `pending` is unset once
    applied, so the row stops being covered and the next template can be saved.
    A plain unique index would block every one after the first."""
    doc = index_named("upcoming_posts", "uq_account_pending_upcoming")
    assert doc["unique"] is True
    assert doc["partialFilterExpression"] == {"pending": True}


def test_a_story_is_unique_per_account():
    """The sync upserts on this key. Without uniqueness an hourly job would
    append a duplicate row every hour a story stayed live."""
    doc = index_named("stories", "uq_account_story")
    assert doc["unique"] is True
    assert list(doc["key"]) == ["instagram_account_id", "instagram_story_id"]


def test_account_snapshot_index_is_unique_per_day():
    """One row per account per day is what makes the daily job idempotent (§5).

    Without uniqueness a retry would append a second row for the same day and
    quietly turn a growth delta into zero.
    """
    doc = index_named("account_snapshots", "uq_account_day")
    assert doc["unique"] is True
    assert list(doc["key"]) == ["instagram_account_id", "day"]


def test_webhook_idempotency_index_is_unique():
    doc = index_named("webhook_events", "uq_event_hash")
    assert doc["unique"] is True
    assert list(doc["key"]) == ["event_hash"]


def test_comment_idempotency_index_is_unique_and_compound():
    doc = index_named("comments", "uq_account_comment")
    assert doc["unique"] is True
    assert list(doc["key"]) == ["instagram_account_id", "instagram_comment_id"]


def test_execution_reservation_index_is_unique_and_compound():
    """This is what stops two concurrent workers both sending a private reply.

    Renamed to _v2 when it gained a partial filter, so that message-triggered
    executions -- which have no comment -- could be keyed separately instead of
    all colliding on a null comment_id.
    """
    doc = index_named("executions", "uq_comment_rule_action_v2")
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

    from app.db.indexes import drop_obsolete_indexes

    await database["executions"].create_index(
        [("comment_id", ASCENDING), ("campaign_id", ASCENDING), ("action", ASCENDING)],
        unique=True,
        name="uq_comment_campaign_action",
    )
    assert "uq_comment_campaign_action" in await database["executions"].index_information()

    dropped = await drop_obsolete_indexes(database)
    assert "executions.uq_comment_campaign_action" in dropped
    assert "uq_comment_campaign_action" not in await database["executions"].index_information()


async def test_startup_creates_the_replacement_before_dropping_its_predecessor(database):
    """Order matters, and the wrong order is invisible.

    create_indexes() used to drop obsolete indexes FIRST, which on every startup
    left a window with no unique index on executions at all -- and a create that
    then failed would leave the system running with no duplicate protection and
    nothing saying so. Dropping last fails safe.
    """
    from pymongo import ASCENDING

    from app.db.indexes import create_indexes

    await database["executions"].create_index(
        [("comment_id", ASCENDING), ("rule_id", ASCENDING), ("action", ASCENDING)],
        unique=True,
        name="uq_comment_rule_action",
    )

    await create_indexes(database)

    names = set(await database["executions"].index_information())
    assert "uq_comment_rule_action" not in names
    assert {"uq_comment_rule_action_v2", "uq_message_rule_action"} <= names

    # Idempotent: a second startup leaves the same end state.
    await create_indexes(database)
    names = set(await database["executions"].index_information())
    assert "uq_comment_rule_action" not in names
    assert {"uq_comment_rule_action_v2", "uq_message_rule_action"} <= names


async def test_the_cleanup_is_idempotent(database):
    from app.db.indexes import drop_obsolete_indexes

    assert await drop_obsolete_indexes(database) == []


def test_both_execution_reservation_indexes_are_unique_and_partial():
    """Partial is the load-bearing word.

    A non-partial unique index treats a missing field as null. If the message
    index were non-partial, every comment-triggered row would index as
    (null, rule, action) and the second one would collide -- reserve() returns
    None, nobody is messaged, and it looks like correct duplicate suppression.
    """
    comment = index_named("executions", "uq_comment_rule_action_v2")
    message = index_named("executions", "uq_message_rule_action")

    assert comment["unique"] is True
    assert message["unique"] is True
    assert list(comment["key"]) == ["comment_id", "rule_id", "action"]
    assert list(message["key"]) == ["trigger_message_id", "rule_id", "action"]


def test_the_partial_filters_use_type_not_exists():
    """`$exists: true` also matches an explicit null.

    One stray `trigger_message_id: None` would then put two rows at
    (null, rule, action) and break reservations for that rule. `$type` cannot.
    """
    comment = index_named("executions", "uq_comment_rule_action_v2")
    message = index_named("executions", "uq_message_rule_action")

    assert comment["partialFilterExpression"] == {"comment_id": {"$type": "objectId"}}
    assert message["partialFilterExpression"] == {"trigger_message_id": {"$type": "string"}}


def test_the_non_partial_predecessor_is_retired():
    from app.db.indexes import OBSOLETE_INDEXES

    assert "uq_comment_rule_action" in OBSOLETE_INDEXES["executions"]


def test_rules_can_be_looked_up_by_trigger():
    doc = index_named("rules", "ix_account_trigger_enabled")
    assert list(doc["key"]) == ["instagram_account_id", "trigger", "enabled", "priority"]
