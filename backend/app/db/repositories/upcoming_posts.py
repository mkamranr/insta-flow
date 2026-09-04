"""The automation waiting for your next post.

One per account, enforced by a partial unique index rather than a check — two
syncs running at once would otherwise both see "none pending" and both write.

The record is *claimed* before the rule is created, for the same reason
executions are reserved before a Meta call: the claim is the atomic step, so a
second worker finds nothing to claim rather than applying the same template
twice.
"""

from typing import Any

from .base import BaseRepository, Document, to_object_id, utcnow

WAITING = "waiting"
APPLYING = "applying"
APPLIED = "applied"


class UpcomingPostsRepository(BaseRepository[Any]):
    collection_name = "upcoming_posts"

    async def pending_for(self, instagram_account_id: Any) -> Document | None:
        """The one waiting to be applied, if any."""
        return await self.collection.find_one(
            {"instagram_account_id": to_object_id(instagram_account_id), "pending": True}
        )

    async def claim(self, instagram_account_id: Any) -> Document | None:
        """Take the pending record, atomically. `None` means someone else has it.

        Returns the document as it was *before* the update, which still carries
        `created_at` -- the cutoff that decides which posts count as new.
        """
        return await self.collection.find_one_and_update(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "pending": True,
                "status": WAITING,
            },
            {"$set": {"status": APPLYING, "claimed_at": utcnow()}},
        )

    async def mark_applied(
        self, upcoming_id: Any, rule_id: Any, instagram_media_id: str
    ) -> None:
        """Consume it. `pending` is unset rather than set false, so the partial
        unique index stops covering the row and the next one can be created."""
        await self.collection.update_one(
            {"_id": to_object_id(upcoming_id)},
            {
                "$set": {
                    "status": APPLIED,
                    "applied_at": utcnow(),
                    "rule_id": rule_id,
                    "instagram_media_id": str(instagram_media_id),
                },
                "$unset": {"pending": ""},
            },
        )

    async def release(self, upcoming_id: Any) -> None:
        """Put a claimed record back. Used when the rule could not be created --
        losing the template because one sync failed would be worse than
        retrying on the next."""
        await self.collection.update_one(
            {"_id": to_object_id(upcoming_id)},
            {"$set": {"status": WAITING}, "$unset": {"claimed_at": ""}},
        )

    async def history(self, instagram_account_id: Any, limit: int = 20) -> list[Document]:
        return await self.list(
            {"instagram_account_id": to_object_id(instagram_account_id)},
            sort_field="created_at",
            limit=limit,
        )
