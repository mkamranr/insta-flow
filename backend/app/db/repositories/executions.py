from typing import Any

from pymongo.errors import DuplicateKeyError

from .base import BaseRepository, Document, to_object_id, utcnow

# Mirrors app.services.campaigns.policy.NOT_FOLLOWING. Duplicated rather than
# imported so the repository layer stays free of service imports; a test asserts
# the two never drift apart.
NOT_FOLLOWING = "not_following"
AWAITING_FOLLOW = "awaiting_follow"
# Mirrors app.services.campaigns.policy.AUTOMATION_PAUSED, for the same reason
# and pinned by the same kind of test.
AUTOMATION_PAUSED = "automation_paused"

# §62 state machine. An execution never leaves this set.
STATUSES = (
    "queued",
    "processing",
    "awaiting_email",
    "success",
    "failed",
    "dry_run",
    "skipped",
)


class ExecutionsRepository(BaseRepository[Any]):
    collection_name = "executions"

    async def reserve(
        self,
        *,
        instagram_account_id: Any,
        rule_id: Any,
        action: str,
        dry_run: bool,
        comment_id: Any = None,
        trigger_message_id: str = "",
        instagram_user_id: str = "",
    ) -> Document | None:
        """Atomically claim the right to perform this action.

        Returns the reservation, or None if another worker already holds it. This
        MUST be called before any Meta mutation: the unique indexes are the only
        thing standing between a duplicated job and a duplicated DM (§63).

        Exactly one key is required. A comment-triggered execution is keyed on
        its comment row; a story-reply or DM-keyword execution on the Instagram
        message id, because it has no comment. Supplying neither -- or both --
        raises: such a row would sit in neither partial unique index and would
        have no duplicate protection at all, silently.
        """
        if bool(comment_id) == bool(trigger_message_id):
            raise ValueError(
                "reserve() needs exactly one of comment_id or trigger_message_id; "
                "a row in neither unique index has no duplicate protection."
            )

        now = utcnow()
        # The unused key is OMITTED, never written as None. An explicit null
        # would land the row in the other index's null bucket, where the second
        # such row for a rule would collide and be silently dropped.
        key: Document = (
            {"comment_id": to_object_id(comment_id)}
            if comment_id
            else {"trigger_message_id": str(trigger_message_id)}
        )
        document = {
            "instagram_account_id": to_object_id(instagram_account_id),
            **key,
            "rule_id": to_object_id(rule_id),
            "action": action,
            # Denormalised so cooldown is a single query rather than a join.
            "instagram_user_id": instagram_user_id,
            "status": "processing",
            "message_text": None,
            "attempts": 0,
            "meta_message_id": None,
            "meta_recipient_id": None,
            "error": None,
            "policy_decision": None,
            "dry_run": dry_run,
            "created_at": now,
            "updated_at": now,
            "started_at": now,
            "completed_at": None,
        }
        try:
            result = await self.collection.insert_one(document)
            document["_id"] = result.inserted_id
            return document
        except DuplicateKeyError:
            return None

    async def find_reservation(
        self,
        comment_id: Any = None,
        rule_id: Any = None,
        action: str = "",
        *,
        trigger_message_id: str = "",
    ) -> Document | None:
        """Fetch the reservation a `reserve()` collision refers to.

        A retry of the same job must continue the existing execution, not create
        a second one -- and must not resend if it already reached a terminal state.

        The `$type` predicate is repeated in the query so Mongo can prove the
        query is a subset of the partial index filter and actually use it;
        without it the planner may fall back to a collection scan.
        """
        if bool(comment_id) == bool(trigger_message_id):
            raise ValueError("find_reservation() needs exactly one key, matching reserve().")

        key: Document = (
            {"comment_id": {"$eq": to_object_id(comment_id), "$type": "objectId"}}
            if comment_id
            else {"trigger_message_id": {"$eq": str(trigger_message_id), "$type": "string"}}
        )
        return await self.collection.find_one(
            {**key, "rule_id": to_object_id(rule_id), "action": action}
        )

    async def count_since(self, rule_id: Any, since: Any, action: str) -> int:
        """Successful real sends since `since`. Dry runs consume no Meta quota."""
        return await self.collection.count_documents(
            {
                "rule_id": to_object_id(rule_id),
                "action": action,
                "status": "success",
                "created_at": {"$gte": since},
            }
        )

    async def find_awaiting_email(
        self, instagram_account_id: Any, instagram_user_id: str
    ) -> Document | None:
        """An execution paused waiting for this person to send their email."""
        return await self.collection.find_one(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "instagram_user_id": str(instagram_user_id),
                "status": "awaiting_email",
            },
            sort=[("created_at", -1)],
        )

    async def claim_awaiting_follow(self, execution_id: Any) -> Document | None:
        """Take exclusive ownership of an execution parked at `awaiting_follow`.

        Two things can reach the same parked row at once: the scheduled re-check,
        and the person tapping the button a second time after following. Whoever
        flips it out of `awaiting_follow` first owns the send; the loser gets
        `None` and stops. Check-then-write here would DM the link twice (§5).
        """
        return await self.collection.find_one_and_update(
            {"_id": to_object_id(execution_id), "status": AWAITING_FOLLOW},
            {"$set": {"status": "processing", "updated_at": utcnow()}},
            return_document=True,
        )

    async def repark_awaiting_follow(self, execution_id: Any) -> None:
        """Hand a claimed execution back, so a later attempt can pick it up."""
        await self.collection.update_one(
            {"_id": to_object_id(execution_id)},
            {"$set": {"status": AWAITING_FOLLOW, "updated_at": utcnow()}},
        )

    async def claim_paused_skip(self, execution_id: Any) -> Document | None:
        """Take a row an Auto DM pause held back into flight.

        Compare-and-swap, like `claim_awaiting_follow`: the backlog is drained by
        fanning out one job per held row, and two jobs reaching the same row must
        not both send. The loser gets None and stops.

        The filter names the reason as well as the status, so this can only ever
        revive a row a pause held -- never one skipped for being outside the
        reply window, rate limited, or already messaged.
        """
        return await self.collection.find_one_and_update(
            {
                "_id": to_object_id(execution_id),
                "status": "skipped",
                "policy_decision": AUTOMATION_PAUSED,
            },
            {
                "$set": {
                    "status": "processing",
                    "policy_decision": None,
                    "completed_at": None,
                    "updated_at": utcnow(),
                }
            },
            return_document=True,
        )

    async def list_paused_skips(
        self, instagram_account_id: Any, *, since: Any = None, limit: int = 500
    ) -> list[Document]:
        """Comment-triggered rows an Auto DM pause held, oldest first.

        Oldest first because whoever waited longest is closest to Meta's 7-day
        reply window closing on them.

        Comment-keyed only. A story-reply or DM-keyword row cannot be replayed:
        it lives under the 24-hour messaging window, not the 7-day comment one.
        `$type` rather than `$exists`, matching `find_reservation` -- an explicit
        null would otherwise slip through.
        """
        query: Document = {
            "instagram_account_id": to_object_id(instagram_account_id),
            "status": "skipped",
            "policy_decision": AUTOMATION_PAUSED,
            "comment_id": {"$type": "objectId"},
        }
        if since is not None:
            query["created_at"] = {"$gt": since}
        cursor = self.collection.find(query).sort("created_at", 1).limit(limit)
        return [doc async for doc in cursor]

    async def pause_backlog_counts(
        self, instagram_account_id: Any, *, since: Any = None, replayable_since: Any = None
    ) -> dict[str, int]:
        """How much a pause held, and how much of it can still be delivered.

        Two numbers, not one. A pause may run longer than Meta's 7-day reply
        window, so part of a long backlog is undeliverable whatever the operator
        chooses -- and the dialog must not promise what it cannot send.

        `deliverable` is an estimate, measured on the execution's own
        `created_at` rather than the comment's `commented_at`, so it costs no
        join. The two are seconds apart in practice. The authority is still the
        policy engine: a replay that turns out to be too late is denied with
        `outside_reply_window` and sends nothing.
        """
        held_query: Document = {
            "instagram_account_id": to_object_id(instagram_account_id),
            "status": "skipped",
            "policy_decision": AUTOMATION_PAUSED,
        }
        if since is not None:
            held_query["created_at"] = {"$gt": since}

        deliverable_query: Document = {**held_query, "comment_id": {"$type": "objectId"}}
        if replayable_since is not None:
            floor = replayable_since if since is None else max(since, replayable_since)
            deliverable_query["created_at"] = {"$gt": floor}

        return {
            "held": await self.collection.count_documents(held_query),
            "deliverable": await self.collection.count_documents(deliverable_query),
        }

    async def already_sent_to(self, rule_id: Any, instagram_user_id: str) -> bool:
        """Has this rule already DMed this person?

        Backs "only send this DM once per person": however many times someone
        comments, this rule messages them once.

        With one exception. If their run ended at the follow gate, the opener
        went out but the payoff never did -- and we told them to follow. Counting
        that as "already messaged" would lock out the very person who did what we
        asked, so an unresolved follow block re-opens the door.
        """
        if not instagram_user_id:
            return False
        scope: Document = {
            "rule_id": to_object_id(rule_id),
            "instagram_user_id": instagram_user_id,
        }
        sent = await self.collection.count_documents(
            {**scope, "status": {"$in": ["success", "dry_run"]}}, limit=1
        )
        if not sent:
            return False
        outstanding_block = await self.collection.count_documents(
            {
                **scope,
                "policy_decision": NOT_FOLLOWING,
                "status": {"$in": ["skipped", "awaiting_follow"]},
            },
            limit=1,
        )
        return not outstanding_block

    async def complete(
        self,
        execution_id: Any,
        *,
        status: str,
        message_text: str | None = None,
        meta_message_id: str | None = None,
        meta_recipient_id: str | None = None,
        error: str | None = None,
        policy_decision: str | None = None,
    ) -> Document | None:
        if status not in STATUSES:
            raise ValueError(f"unknown execution status: {status}")
        return await self.update(
            execution_id,
            {
                "status": status,
                "message_text": message_text,
                "meta_message_id": meta_message_id,
                "meta_recipient_id": meta_recipient_id,
                "error": error,
                "policy_decision": policy_decision,
                "completed_at": utcnow(),
            },
        )

    async def increment_attempts(self, execution_id: Any) -> None:
        await self.collection.update_one(
            {"_id": to_object_id(execution_id)},
            {"$inc": {"attempts": 1}, "$set": {"updated_at": utcnow()}},
        )

    async def triggered_comment_ids(self, instagram_account_id: Any) -> list[Any]:
        """Comments that actually caused a rule to fire, for trigger-rate reporting.

        Distinct on `comment_id`, so the two-step flow (opener then payoff) counts
        the comment once rather than twice.
        """
        return await self.collection.distinct(
            "comment_id", {"instagram_account_id": to_object_id(instagram_account_id)}
        )

    async def series(
        self,
        instagram_account_id: Any,
        since: Any,
        actions: tuple[str, ...],
        *,
        by_hour: bool = True,
    ) -> list[Document]:
        """Successful sends per hour or per day, oldest first."""
        fmt = "%Y-%m-%dT%H:00" if by_hour else "%Y-%m-%d"
        pipeline: list[Document] = [
            {
                "$match": {
                    "instagram_account_id": to_object_id(instagram_account_id),
                    "status": "success",
                    "action": {"$in": list(actions)},
                    "created_at": {"$gte": since},
                }
            },
            {
                "$group": {
                    "_id": {"$dateToString": {"format": fmt, "date": "$created_at"}},
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        cursor = await self.collection.aggregate(pipeline)
        return [{"bucket": doc["_id"], "count": int(doc["count"])} async for doc in cursor]

    async def count_by_status(self, query: Document | None = None) -> dict[str, int]:
        pipeline: list[Document] = []
        if query:
            pipeline.append({"$match": query})
        pipeline.append({"$group": {"_id": "$status", "n": {"$sum": 1}}})
        return {doc["_id"]: doc["n"] async for doc in await self.collection.aggregate(pipeline)}
