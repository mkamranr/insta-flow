"""One row per click, so clicks can be placed in time and on a map.

`link_clicks` holds a counter and a first/last timestamp. That is enough to say
how many clicks a link got and never enough to say *when* — a token clicked five
times over three days is one row with one `last_clicked_at`. Bucketing that into
an hourly chart would put all five in the last hour, which is wrong in a way
nobody would notice.

So each click also lands here, with its own timestamp and whatever location was
derived from the request. The IP is not stored, here or anywhere (§geoip).
"""

from datetime import datetime
from typing import Any

from .base import BaseRepository, Document, to_object_id, utcnow


class ClickEventsRepository(BaseRepository[Any]):
    collection_name = "click_events"

    async def record(
        self,
        *,
        instagram_account_id: Any,
        rule_id: Any = None,
        token: str,
        country: str | None = None,
        country_code: str | None = None,
        city: str | None = None,
    ) -> None:
        await self.collection.insert_one(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "rule_id": to_object_id(rule_id) if rule_id else None,
                "token": token,
                "country": country,
                "country_code": country_code,
                "city": city,
                "clicked_at": utcnow(),
            }
        )

    async def series(
        self, instagram_account_id: Any, since: datetime, *, by_hour: bool = True
    ) -> list[Document]:
        """Clicks per hour or per day, oldest first.

        Buckets are formatted in Mongo so the shape is identical to the one the
        executions series produces, and the two can be zipped in the UI.
        """
        fmt = "%Y-%m-%dT%H:00" if by_hour else "%Y-%m-%d"
        pipeline: list[Document] = [
            {
                "$match": {
                    "instagram_account_id": to_object_id(instagram_account_id),
                    "clicked_at": {"$gte": since},
                }
            },
            {
                "$group": {
                    "_id": {"$dateToString": {"format": fmt, "date": "$clicked_at"}},
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        cursor = await self.collection.aggregate(pipeline)
        return [{"bucket": doc["_id"], "count": int(doc["count"])} async for doc in cursor]

    async def by_place(
        self, instagram_account_id: Any, since: datetime, *, by_city: bool = False
    ) -> list[Document]:
        """Click totals by country, or by city within its country.

        Unplaced clicks are excluded rather than bucketed as "Unknown": before
        the GeoLite2 database is installed every click is unplaced, and a chart
        that is entirely Unknown reads as a finding rather than a missing setup.
        """
        key: Document = {"country": "$country", "country_code": "$country_code"}
        if by_city:
            key["city"] = "$city"

        match: Document = {
            "instagram_account_id": to_object_id(instagram_account_id),
            "clicked_at": {"$gte": since},
        }
        match["city" if by_city else "country_code"] = {"$nin": [None, ""]}

        pipeline: list[Document] = [
            {"$match": match},
            {"$group": {"_id": key, "clicks": {"$sum": 1}}},
            {"$sort": {"clicks": -1}},
            {"$limit": 100},
        ]
        cursor = await self.collection.aggregate(pipeline)
        return [
            {
                "country": doc["_id"].get("country"),
                "country_code": doc["_id"].get("country_code"),
                "city": doc["_id"].get("city"),
                "clicks": int(doc["clicks"]),
            }
            async for doc in cursor
        ]
