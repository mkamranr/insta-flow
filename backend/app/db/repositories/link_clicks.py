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

    async def by_destination_for_rule(self, rule_id: Any) -> list[Document]:
        """Per-destination totals for one rule.

        Grouped by target URL, not by token: a token is minted per recipient, so
        grouping on it would report one "link" per person instead of one row per
        destination.

        `unique` counts tokens with at least one click. Since a token belongs to
        exactly one recipient, that is the number of distinct people who clicked
        -- not the number of clicks, which repeat taps inflate.
        """
        pipeline: list[Document] = [
            {"$match": {"rule_id": to_object_id(rule_id)}},
            {
                "$group": {
                    "_id": {"url": "$target_url", "title": "$button_title"},
                    "clicks": {"$sum": "$click_count"},
                    "delivered": {"$sum": 1},
                    "unique": {"$sum": {"$cond": [{"$gt": ["$click_count", 0]}, 1, 0]}},
                    "last_clicked_at": {"$max": "$last_clicked_at"},
                }
            },
            {"$sort": {"clicks": -1}},
        ]
        cursor = await self.collection.aggregate(pipeline)
        return [
            {
                "target_url": doc["_id"].get("url") or "",
                "title": doc["_id"].get("title") or "Open",
                "clicks": int(doc.get("clicks") or 0),
                "delivered": int(doc.get("delivered") or 0),
                "unique": int(doc.get("unique") or 0),
                "last_clicked_at": doc.get("last_clicked_at"),
            }
            async for doc in cursor
        ]

    async def clicks_by_rule(self, instagram_account_id: Any) -> dict[str, int]:
        pipeline: list[Document] = [
            {"$match": {"instagram_account_id": to_object_id(instagram_account_id)}},
            {"$group": {"_id": "$rule_id", "n": {"$sum": "$click_count"}}},
        ]
        cursor = await self.collection.aggregate(pipeline)
        return {str(doc["_id"]): int(doc["n"]) async for doc in cursor}
