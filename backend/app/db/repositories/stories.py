"""Active stories, and the record of which have expired.

A story lives 24 hours. Meta returns only *active* ones from `/me/stories`, so
expiry is **observed, not computed**: a story we have stored that the edge no
longer returns has expired.

Counting 24 hours from `timestamp` would mostly work -- Meta does supply it --
but it would put us in the business of predicting Meta's clock, and a story
pulled early by the operator would stay "live" in the picker until the deadline
passed. Asking is cheap and cannot drift.
"""

from datetime import datetime
from typing import Any

from pymongo import DESCENDING

from .base import BaseRepository, Document, to_object_id, utcnow
from .image_cache import ImageCacheMixin


class StoriesRepository(ImageCacheMixin, BaseRepository[Any]):
    collection_name = "stories"
    _image_key = "instagram_story_id"

    async def upsert(
        self, instagram_account_id: Any, instagram_story_id: str, values: Document
    ) -> Document | None:
        """Record a story seen as active.

        `first_seen_at` is set once and never overwritten, so a story that
        expires and is somehow listed again keeps its original position rather
        than jumping to the top of the picker.
        """
        return await self.collection.find_one_and_update(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "instagram_story_id": str(instagram_story_id),
            },
            {
                "$set": {**values, "expired_at": None},
                "$setOnInsert": {"first_seen_at": utcnow()},
            },
            upsert=True,
            return_document=True,
        )

    async def mark_expired(
        self, instagram_account_id: Any, keep_ids: list[str], now: datetime | None = None
    ) -> list[str]:
        """Expire every stored story not in `keep_ids`. Returns those expired.

        Read before write so the caller knows which ids changed state -- an
        update count alone cannot say which rules to disable.
        """
        query: Document = {
            "instagram_account_id": to_object_id(instagram_account_id),
            "expired_at": None,
            "instagram_story_id": {"$nin": [str(i) for i in keep_ids]},
        }
        cursor = self.collection.find(query, {"instagram_story_id": 1})
        going = [doc["instagram_story_id"] async for doc in cursor]
        if going:
            await self.collection.update_many(query, {"$set": {"expired_at": now or utcnow()}})
        return going

    async def list_active(self, instagram_account_id: Any) -> list[Document]:
        """Newest first, by Meta's own timestamp; `first_seen_at` breaks ties and
        covers a row stored before that field was read."""
        cursor = self.collection.find(
            {"instagram_account_id": to_object_id(instagram_account_id), "expired_at": None}
        ).sort([("published_at", DESCENDING), ("first_seen_at", DESCENDING), ("_id", DESCENDING)])
        return [doc async for doc in cursor]

    async def get_by_instagram_story_id(
        self, instagram_account_id: Any, instagram_story_id: str
    ) -> Document | None:
        return await self.collection.find_one(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "instagram_story_id": str(instagram_story_id),
            }
        )
