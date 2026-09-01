"""Trackable links.

Each button we send gets its own short token, so a click can be attributed to
the rule, the execution, and the person who received it. Without this there is
no honest click or click-through metric — only guesses (§59).
"""

from typing import Any

from .base import BaseRepository, Document, to_object_id, utcnow


class LinkClicksRepository(BaseRepository[Any]):
    collection_name = "link_clicks"

    async def get_by_token(self, token: str) -> Document | None:
        return await self.collection.find_one({"token": token})

    async def record_click(
        self, token: str, *, user_agent: str | None = None, ip_address: str | None = None
    ) -> Document | None:
        """Increment the click count and return the link, or None if unknown.

        Counts every click, not just the first: repeat clicks are real
        engagement, and collapsing them would understate the metric.
        """
        now = utcnow()
        return await self.collection.find_one_and_update(
            {"token": token},
            {
                "$inc": {"click_count": 1},
                "$set": {"last_clicked_at": now, "last_user_agent": user_agent},
                "$setOnInsert": {},
                "$min": {"first_clicked_at": now},
            },
            return_document=True,
        )

    async def count_clicks(self, query: Document | None = None) -> int:
        """Total clicks, not distinct links clicked."""
        pipeline: list[Document] = []
        if query:
            pipeline.append({"$match": query})
        pipeline.append({"$group": {"_id": None, "n": {"$sum": "$click_count"}}})
        async for doc in await self.collection.aggregate(pipeline):
            return int(doc["n"])
        return 0

    async def clicks_by_rule(self, instagram_account_id: Any) -> dict[str, int]:
        pipeline: list[Document] = [
            {"$match": {"instagram_account_id": to_object_id(instagram_account_id)}},
            {"$group": {"_id": "$rule_id", "n": {"$sum": "$click_count"}}},
        ]
        cursor = await self.collection.aggregate(pipeline)
        return {str(doc["_id"]): int(doc["n"]) async for doc in cursor}
