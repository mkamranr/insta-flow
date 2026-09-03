"""Post rules: one post + one keyword + one message.

Replaces the campaign/trigger/template triple. A rule is a single document, so
creating, editing, and deleting one is a single operation with nothing to keep
in sync.
"""

from typing import Any

from pymongo import DESCENDING

from .base import BaseRepository, Document, to_object_id


class RulesRepository(BaseRepository[Any]):
    collection_name = "rules"

    async def list_for_media(
        self, instagram_account_id: Any, instagram_media_id: str
    ) -> list[Document]:
        """Enabled rules for one post, highest priority first.

        Ties break on id so selection is deterministic — without it two
        equal-priority rules could alternate between runs.
        """
        cursor = self.collection.find(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "enabled": True,
                # Comment rules only. `$in` with None matches a MISSING field as
                # well as an explicit null, which is what lets rules written
                # before `trigger` existed keep working with no backfill.
                #
                # Without this clause every existing rule would also fire on
                # story replies and inbound DMs.
                "trigger": {"$in": ["comment", None]},
                "$or": [
                    {"scope": "any"},
                    {"instagram_media_ids": str(instagram_media_id)},
                ],
            }
        ).sort([("priority", DESCENDING), ("_id", DESCENDING)])
        return [doc async for doc in cursor]

    async def list_for_trigger(self, instagram_account_id: Any, trigger: str) -> list[Document]:
        """Enabled rules for a non-comment trigger, highest priority first.

        Exact match on a value legacy rows cannot hold, so pre-`trigger` rules
        are excluded automatically. Ties break on id, as in `list_for_media`, so
        selection is deterministic.
        """
        cursor = self.collection.find(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "enabled": True,
                "trigger": trigger,
            }
        ).sort([("priority", DESCENDING), ("_id", DESCENDING)])
        return [doc async for doc in cursor]

    async def list_for_account(self, instagram_account_id: Any) -> list[Document]:
        return await self.list(
            {"instagram_account_id": to_object_id(instagram_account_id)},
            sort_field="priority",
        )

    async def counts_by_media(self, instagram_account_id: Any) -> dict[str, int]:
        """How many rules each post has, for the posts list."""
        pipeline: list[Document] = [
            {"$match": {"instagram_account_id": to_object_id(instagram_account_id)}},
            {"$unwind": "$instagram_media_ids"},
            {"$group": {"_id": "$instagram_media_ids", "n": {"$sum": 1}}},
        ]
        return {
            str(doc["_id"]): doc["n"] async for doc in await self.collection.aggregate(pipeline)
        }

    async def count_for_scope_any(self, instagram_account_id: Any) -> int:
        return await self.collection.count_documents(
            {"instagram_account_id": to_object_id(instagram_account_id), "scope": "any"}
        )
