from typing import Any

from pymongo.errors import DuplicateKeyError

from .base import BaseRepository, Document, to_object_id, utcnow

# Mirrors app.services.campaigns.policy.NOT_FOLLOWING. Duplicated rather than
# imported so the repository layer stays free of service imports; a test asserts
# the two never drift apart.
NOT_FOLLOWING = "not_following"
AWAITING_FOLLOW = "awaiting_follow"

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
        comment_id: Any,
        rule_id: Any,
        action: str,
        dry_run: bool,
        instagram_user_id: str = "",
    ) -> Document | None:
        """Atomically claim the right to perform this action.

        Returns the reservation, or None if another worker already holds it. This
        MUST be called before any Meta mutation: the unique index on
        (comment_id, rule_id, action) is the only thing standing between a
        duplicated job and a duplicated DM (§63).
        """
        now = utcnow()
        document = {
            "instagram_account_id": to_object_id(instagram_account_id),
            "comment_id": to_object_id(comment_id),
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
        self, comment_id: Any, rule_id: Any, action: str
    ) -> Document | None:
        """Fetch the reservation a `reserve()` collision refers to.

        A retry of the same job must continue the existing execution, not create
        a second one -- and must not resend if it already reached a terminal state.
        """
        return await self.collection.find_one(
            {
                "comment_id": to_object_id(comment_id),
                "rule_id": to_object_id(rule_id),
                "action": action,
            }
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

    async def count_by_status(self, query: Document | None = None) -> dict[str, int]:
        pipeline: list[Document] = []
        if query:
            pipeline.append({"$match": query})
        pipeline.append({"$group": {"_id": "$status", "n": {"$sum": 1}}})
        return {doc["_id"]: doc["n"] async for doc in await self.collection.aggregate(pipeline)}
