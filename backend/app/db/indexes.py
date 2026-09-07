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
    "roles",
    "instagram_accounts",
    "media",
    "stories",
    "upcoming_posts",
    "rules",
    "link_clicks",
    "contacts",
    "comments",
    "executions",
    "webhook_events",
    "ai_classifications",
    "audit_logs",
    "account_snapshots",
    "app_settings",
    "click_events",
    "meta_apps",
    "email_log",
    "invites",
]

INDEXES: dict[str, list[IndexModel]] = {
    "invites": [
        # The redemption lookup: one indexed equality match on the token's
        # SHA-256. Unique but NOT partial, and the absence is worth stating,
        # because the loudest trap in this file is a *missing* partial filter
        # (see `uq_user_slug`) and the next reader will assume one belongs.
        # `token_hash` is written on every insert and is never absent or null,
        # so there is no population of nulls for a plain unique index to
        # collide on -- the exact opposite of the slug situation.
        IndexModel([("token_hash", ASCENDING)], unique=True, name="uq_invite_token"),
        # At most one LIVE invite per address, and this index IS the check:
        # a second invite to a pending address raises DuplicateKeyError, which
        # the route reports as a conflict. Never a read-then-write (§25, §63).
        #
        # Partial on the VALUE `pending`, like `uq_bootstrap_admin`. An accepted
        # or revoked invite drops out of the index, so the same person can be
        # invited again later; a plain unique index would block that forever.
        # `$type` is not wanted here -- the filter is on a value, not presence.
        IndexModel(
            [("email", ASCENDING)],
            unique=True,
            name="uq_invite_pending_email",
            partialFilterExpression={"status": "pending"},
        ),
        IndexModel(
            [("status", ASCENDING), ("created_at", DESCENDING)],
            name="ix_status_created",
        ),
        IndexModel(
            [("email", ASCENDING), ("created_at", DESCENDING)],
            name="ix_email_created",
        ),
        # Deliberately NO TTL index. Deleting an expired invite would turn every
        # late click into a 404 indistinguishable from a wrong token, which
        # destroys both the audit trail and the only error message the recipient
        # can act on. Expiry is evaluated at read time against `expires_at`.
        # `test_indexes.py` pins the absence, because a TTL is the first thing a
        # reviewer suggests here.
    ],
    "email_log": [
        IndexModel([("created_at", DESCENDING)], name="ix_created"),
        IndexModel(
            [("to_address", ASCENDING), ("created_at", DESCENDING)],
            name="ix_recipient_created",
        ),
        # The idempotency handle. The route mints an RFC 5322 Message-ID, opens
        # the row, and only then enqueues, so a redelivered job finds the row
        # already terminal and returns without sending a second copy.
        #
        # Unique but NOT partial, and the absence is deliberate enough to write
        # down: the loudest trap in this file is a MISSING partial filter
        # (`uq_user_slug`, which must skip slug-less rows until the backfill has
        # run). Nothing analogous applies here -- `message_id` is written by the
        # enqueuer on every row from the very first one, so there is no
        # population of nulls for a plain unique index to collide on.
        IndexModel([("message_id", ASCENDING)], unique=True, name="uq_message_id"),
    ],
    "roles": [
        IndexModel([("slug", ASCENDING)], unique=True, name="uq_role_slug"),
    ],
    "users": [
        IndexModel([("email", ASCENDING)], unique=True, name="uq_email"),
        # The workspace slug: the `/u/<slug>/` segment a Meta app is configured to
        # call. Partial, and on `$type` rather than `$exists`, for the same reason
        # the executions indexes are: this index is created before
        # `backfill_missing_slugs` runs, so on an upgrade every existing user is
        # still slug-less. A non-partial unique index reads all of them as null
        # and collides, aborting startup on the very deployments that need the
        # backfill. `$exists: true` would also match an explicit null and let the
        # same collision back in.
        IndexModel(
            [("slug", ASCENDING)],
            unique=True,
            name="uq_user_slug",
            partialFilterExpression={"slug": {"$type": "string"}},
        ),
        # At most one user may ever carry `is_bootstrap`. Two requests that both
        # pass a "no users yet" check would otherwise both create an admin, with
        # different emails, and the unique email index would not stop them.
        # Partial on the value, so ordinary users -- which omit the field -- are
        # not covered and can be created freely.
        IndexModel(
            [("is_bootstrap", ASCENDING)],
            unique=True,
            name="uq_bootstrap_admin",
            partialFilterExpression={"is_bootstrap": True},
        ),
    ],
    "click_events": [
        # Every query is "this account, since this moment", either grouped into
        # time buckets or by place.
        IndexModel(
            [("instagram_account_id", ASCENDING), ("clicked_at", DESCENDING)],
            name="ix_account_clicked",
        ),
        IndexModel([("rule_id", ASCENDING), ("clicked_at", DESCENDING)], name="ix_rule_clicked"),
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
    "meta_apps": [
        # One Meta app per workspace. Unique rather than merely indexed: two rows
        # for one user would make "which app secret verifies this webhook" a
        # coin toss, and the wrong answer is a silently rejected signature.
        IndexModel([("user_id", ASCENDING)], unique=True, name="uq_meta_app_user"),
    ],
    "instagram_accounts": [
        IndexModel([("instagram_user_id", ASCENDING)], unique=True, name="uq_instagram_user_id"),
        IndexModel([("user_id", ASCENDING)], name="ix_user_id"),
        # At most one default account per workspace. Unique rather than merely
        # indexed: two defaults would make "which account does an unqualified
        # request act on" a coin toss, and the wrong answer writes a rule against
        # the wrong audience.
        #
        # Partial on the value, like `uq_bootstrap_admin` above: an account that
        # is not the default omits the field entirely, so it is not covered and
        # any number of them coexist. That is also what makes this index safe to
        # create BEFORE the backfill that populates it -- no existing row carries
        # `is_default` at all, so nothing can collide. Contrast `uq_user_slug`,
        # which had to spell out `$type` precisely because its field was already
        # present and null on every row that needed backfilling.
        IndexModel(
            [("user_id", ASCENDING)],
            unique=True,
            name="uq_default_account_per_user",
            partialFilterExpression={"is_default": True},
        ),
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
    "stories": [
        IndexModel(
            [("instagram_account_id", ASCENDING), ("instagram_story_id", ASCENDING)],
            unique=True,
            name="uq_account_story",
        ),
        # The picker's query: active stories for one account, newest first.
        IndexModel(
            [
                ("instagram_account_id", ASCENDING),
                ("expired_at", ASCENDING),
                ("first_seen_at", DESCENDING),
            ],
            name="ix_account_active_story",
        ),
    ],
    "upcoming_posts": [
        # One pending template per account. `pending` is unset once applied, so
        # the partial index stops covering the row and the next one can be
        # created -- a plain unique index would block forever after the first.
        IndexModel(
            [("instagram_account_id", ASCENDING)],
            unique=True,
            name="uq_account_pending_upcoming",
            partialFilterExpression={"pending": True},
        ),
    ],
    "rules": [
        # The lookup for story-reply and DM-keyword rules, which have no media.
        IndexModel(
            [
                ("instagram_account_id", ASCENDING),
                ("trigger", ASCENDING),
                ("enabled", ASCENDING),
                ("priority", DESCENDING),
            ],
            name="ix_account_trigger_enabled",
        ),
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
        # two concurrent workers both sending the same message.
        #
        # Two indexes, not one, because an execution is keyed by whichever thing
        # triggered it: a comment row for comment rules, an Instagram message id
        # for story-reply and DM-keyword rules. Exactly one is ever present.
        #
        # They MUST be partial. A non-partial unique index treats a missing
        # field as null, so the second message-triggered execution for a given
        # (rule, action) would collide with the first on (null, rule, action) --
        # reserve() returns None, the person is silently never messaged, and it
        # looks exactly like correct duplicate suppression. Silent data loss.
        #
        # `$type`, not `$exists`: `$exists: true` also matches an explicit null,
        # which would reintroduce the same collision through the back door.
        IndexModel(
            [("comment_id", ASCENDING), ("rule_id", ASCENDING), ("action", ASCENDING)],
            unique=True,
            name="uq_comment_rule_action_v2",
            partialFilterExpression={"comment_id": {"$type": "objectId"}},
        ),
        IndexModel(
            [("trigger_message_id", ASCENDING), ("rule_id", ASCENDING), ("action", ASCENDING)],
            unique=True,
            name="uq_message_rule_action",
            partialFilterExpression={"trigger_message_id": {"$type": "string"}},
        ),
        IndexModel(
            [("instagram_account_id", ASCENDING), ("created_at", DESCENDING)],
            name="ix_account_created",
        ),
        IndexModel([("status", ASCENDING), ("created_at", DESCENDING)], name="ix_status_created"),
        # `find_awaiting_email` runs on every inbound DM and matches exactly this
        # key; it had no index at all and scanned the largest collection in the
        # system. The contacts backfill reconstructs a lead's source posts with
        # the same query, so one index serves both.
        IndexModel(
            [
                ("instagram_account_id", ASCENDING),
                ("instagram_user_id", ASCENDING),
                ("created_at", DESCENDING),
            ],
            name="ix_account_user_created",
        ),
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
        # Which post a lead came from. Multikey on `sources`, and it MUST stay
        # non-unique -- which is the opposite of the rule the executions block
        # follows, so it is worth being explicit about why.
        #
        # That rule is about a *unique* index created before its backfill.
        # `create_indexes()` runs before `backfill_sources`, so at this moment
        # every legacy contact has no `sources` field at all and indexes as null.
        # Under a non-unique index that is a harmless no-op. Under a unique one
        # they would all collide on null and startup would abort on exactly the
        # installs that still need the backfill.
        IndexModel(
            [("instagram_account_id", ASCENDING), ("sources.instagram_media_id", ASCENDING)],
            name="ix_account_source_media",
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
    # Superseded by uq_comment_rule_action_v2, which is the same key made
    # partial so message-triggered executions can coexist. Renamed rather than
    # altered because an index's options cannot be changed in place.
    "executions": ["uq_comment_campaign_action", "uq_comment_rule_action"],
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
    # COLLECTIONS is the declared set, and a collection with nothing but its
    # `_id` still has to exist -- `app_settings` needs no secondary index but
    # must be there. Mongo creates lazily on first write, which would leave a
    # declared collection missing until something happened to use it.
    existing = set(await database.list_collection_names())
    for name in COLLECTIONS:
        if name not in INDEXES and name not in existing:
            await database.create_collection(name)

    created: dict[str, list[str]] = {}
    for collection_name, models in INDEXES.items():
        names = await database[collection_name].create_indexes(models)
        created[collection_name] = names

    # Dropped LAST, deliberately. Dropping first would leave a window on every
    # startup with no unique index on executions at all, and a create that then
    # failed would leave the system running with no duplicate protection and no
    # error saying so. This order fails safe: a bad create aborts startup with
    # the old index still in place.
    await drop_obsolete_indexes(database)

    logger.info(
        "indexes_created",
        extra={"collections": len(created), "indexes": sum(len(v) for v in created.values())},
    )
    return created
