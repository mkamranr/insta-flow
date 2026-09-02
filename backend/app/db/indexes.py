"""All MongoDB indexes, exactly as specified in project-plan.md §18.

These are not an optimisation. The three unique indexes marked below are the
system's duplicate-protection authority (§25, §63): the worker relies on a
duplicate-key error, not on an application-level check, to avoid double sends.
"""

import logging
from typing import Any

from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)

COLLECTIONS = [
    "users",
    "instagram_accounts",
    "media",
    "rules",
    "link_clicks",
    "contacts",
    "comments",
    "executions",
    "webhook_events",
    "ai_classifications",
    "audit_logs",
    "account_snapshots",
]

INDEXES: dict[str, list[IndexModel]] = {
    "users": [
        IndexModel([("email", ASCENDING)], unique=True, name="uq_email"),
    ],
    "account_snapshots": [
        # One row per account per calendar day. The uniqueness is what makes the
        # daily job idempotent under retry or a double run (§5).
        IndexModel(
            [("instagram_account_id", ASCENDING), ("day", ASCENDING)],
            unique=True,
            name="uq_account_day",
        ),
    ],
    "instagram_accounts": [
        IndexModel(
            [("instagram_user_id", ASCENDING)], unique=True, name="uq_instagram_user_id"
        ),
        IndexModel([("user_id", ASCENDING)], name="ix_user_id"),
    ],
    "media": [
        IndexModel(
            [("instagram_account_id", ASCENDING), ("instagram_media_id", ASCENDING)],
            unique=True,
            name="uq_account_media",
        ),
        IndexModel(
            [("instagram_account_id", ASCENDING), ("published_at", DESCENDING)],
            name="ix_account_published",
        ),
    ],
    "rules": [
        # The lookup the worker does on every comment.
        IndexModel(
            [
                ("instagram_account_id", ASCENDING),
                ("instagram_media_id", ASCENDING),
                ("enabled", ASCENDING),
            ],
            name="ix_account_media_enabled",
        ),
        IndexModel(
            [("instagram_account_id", ASCENDING), ("priority", DESCENDING)],
            name="ix_account_priority",
        ),
    ],
    "comments": [
        # Comment-level idempotency (§25).
        IndexModel(
            [("instagram_account_id", ASCENDING), ("instagram_comment_id", ASCENDING)],
            unique=True,
            name="uq_account_comment",
        ),
        IndexModel(
            [("instagram_account_id", ASCENDING), ("commented_at", DESCENDING)],
            name="ix_account_commented",
        ),
        IndexModel(
            [("instagram_media_id", ASCENDING), ("commented_at", DESCENDING)],
            name="ix_media_commented",
        ),
    ],
    "executions": [
        # Execution-level idempotency (§25, §63): the reservation that prevents
        # two concurrent workers both sending a private reply.
        IndexModel(
            [("comment_id", ASCENDING), ("rule_id", ASCENDING), ("action", ASCENDING)],
            unique=True,
            name="uq_comment_rule_action",
        ),
        IndexModel(
            [("instagram_account_id", ASCENDING), ("created_at", DESCENDING)],
            name="ix_account_created",
        ),
        IndexModel([("status", ASCENDING), ("created_at", DESCENDING)], name="ix_status_created"),
    ],
    "webhook_events": [
        # Webhook-level idempotency (§25).
        IndexModel([("event_hash", ASCENDING)], unique=True, name="uq_event_hash"),
        IndexModel([("received_at", DESCENDING)], name="ix_received"),
    ],
    "link_clicks": [
        IndexModel([("token", ASCENDING)], unique=True, name="uq_token"),
        IndexModel(
            [("instagram_account_id", ASCENDING), ("created_at", DESCENDING)],
            name="ix_account_created",
        ),
        IndexModel([("rule_id", ASCENDING)], name="ix_rule"),
    ],
    "contacts": [
        IndexModel(
            [("instagram_account_id", ASCENDING), ("instagram_user_id", ASCENDING)],
            unique=True,
            name="uq_account_user",
        ),
        IndexModel(
            [("instagram_account_id", ASCENDING), ("created_at", DESCENDING)],
            name="ix_account_created",
        ),
    ],
    "ai_classifications": [
        IndexModel([("comment_id", ASCENDING)], name="ix_comment"),
    ],
    "audit_logs": [
        IndexModel([("created_at", DESCENDING)], name="ix_created"),
        IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="ix_user_created"),
    ],
}


# Indexes from earlier schema versions. Mongo does not remove an index when its
# definition disappears from the code, so a renamed index lingers and keeps
# enforcing its old constraint against fields that no longer exist.
OBSOLETE_INDEXES: dict[str, list[str]] = {
    # Renamed when campaigns became rules. With campaign_id now always absent,
    # this silently enforced uniqueness on (comment_id, action) alone.
    "executions": ["uq_comment_campaign_action"],
}


async def drop_obsolete_indexes(database: AsyncDatabase[dict[str, Any]]) -> list[str]:
    """Remove indexes left behind by earlier schema versions. Idempotent."""
    dropped: list[str] = []
    for collection_name, names in OBSOLETE_INDEXES.items():
        try:
            existing = await database[collection_name].index_information()
        except Exception:  # pragma: no cover - collection may not exist yet
            logger.debug("obsolete_index_check_skipped", extra={"collection": collection_name})
            continue
        for name in names:
            if name in existing:
                await database[collection_name].drop_index(name)
                dropped.append(f"{collection_name}.{name}")
                logger.warning("obsolete_index_dropped", extra={"index": name})
    return dropped


async def create_indexes(database: AsyncDatabase[dict[str, Any]]) -> dict[str, list[str]]:
    """Create every index. Idempotent — safe to run on every startup."""
    await drop_obsolete_indexes(database)

    created: dict[str, list[str]] = {}
    for collection_name, models in INDEXES.items():
        names = await database[collection_name].create_indexes(models)
        created[collection_name] = names
    logger.info(
        "indexes_created",
        extra={"collections": len(created), "indexes": sum(len(v) for v in created.values())},
    )
    return created
