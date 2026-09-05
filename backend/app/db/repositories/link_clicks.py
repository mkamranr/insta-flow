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
        self,
        token: str,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
        country: str | None = None,
        country_code: str | None = None,
        city: str | None = None,
    ) -> Document | None:
        """Increment the click count and return the link, or None if unknown.

        Counts every click, not just the first: repeat clicks are real
        engagement, and collapsing them would understate the metric.

        `ip_address` is accepted and deliberately **not stored**. It is the
        identifying part; the country and city derived from it are not, and are
        all the geography report needs. Keeping the address would turn a click
        counter into a location log.
        """
        now = utcnow()
        located: Document = {}
        if country_code or city:
            located = {"country": country, "country_code": country_code, "city": city}
        return await self.collection.find_one_and_update(
            {"token": token},
            {
                "$inc": {"click_count": 1},
                "$set": {"last_clicked_at": now, "last_user_agent": user_agent, **located},
                "$setOnInsert": {},
                "$min": {"first_clicked_at": now},
            },
            return_document=True,
        )

    async def clicks_by_place(self, rule_id: Any, *, by_city: bool = False) -> list[Document]:
        """Click totals grouped by country, or by city within its country.

        Rows with no location are skipped rather than bucketed as "Unknown":
        before the database is installed every row is unlocated, and a chart
        that is 100% Unknown reads as a finding rather than a missing setup.
        """
        key: Document = (
            {"country": "$country", "country_code": "$country_code", "city": "$city"}
            if by_city
            else {"country": "$country", "country_code": "$country_code"}
        )
        match: Document = {"rule_id": to_object_id(rule_id), "click_count": {"$gt": 0}}
        match["city" if by_city else "country_code"] = {"$nin": [None, ""]}

        pipeline: list[Document] = [
            {"$match": match},
            {"$group": {"_id": key, "clicks": {"$sum": "$click_count"}}},
            {"$sort": {"clicks": -1}},
            {"$limit": 100},
        ]
        cursor = await self.collection.aggregate(pipeline)
        return [
            {
                "country": doc["_id"].get("country"),
                "country_code": doc["_id"].get("country_code"),
                "city": doc["_id"].get("city"),
                "clicks": int(doc.get("clicks") or 0),
            }
            async for doc in cursor
        ]

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

    async def delivered_by_rule(self, instagram_account_id: Any) -> dict[str, int]:
        """How many trackable links each rule has actually put in front of people.

        The denominator for click-through rate. Counting DMs sent instead
        inflated it by exactly the number of openers, and so understated every
        rate: a two-step automation sends the opener *and* the payoff for one
        comment, but only the payoff carries a `web_url` -- the opener's button
        is a postback, which mints no token here. One commenter, sent one link
        and clicking it, read as 50%.

        A row exists from the moment the link is sent, with `click_count: 0`, so
        this counts deliveries and never silently narrows to links that worked.
        """
        pipeline: list[Document] = [
            {"$match": {"instagram_account_id": to_object_id(instagram_account_id)}},
            {"$group": {"_id": "$rule_id", "n": {"$sum": 1}}},
        ]
        cursor = await self.collection.aggregate(pipeline)
        return {str(doc["_id"]): int(doc["n"]) async for doc in cursor}
