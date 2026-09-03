from typing import Any

from pymongo.errors import DuplicateKeyError

from .base import BaseRepository, Document, to_object_id, utcnow


class CommentsRepository(BaseRepository[Any]):
    collection_name = "comments"

    async def get_by_instagram_comment_id(
        self, instagram_account_id: Any, instagram_comment_id: str
    ) -> Document | None:
        return await self.collection.find_one(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "instagram_comment_id": str(instagram_comment_id),
            }
        )

    async def insert_unique(self, document: Document) -> tuple[Document, bool]:
        """Insert a comment, tolerating a duplicate webhook delivery.

        Returns (document, created). The unique index on
        (instagram_account_id, instagram_comment_id) is the authority — we let the
        insert fail and read back rather than checking first, because a check-then-
        insert races against a concurrent worker (§63).
        """
        document.setdefault("created_at", utcnow())
        try:
            result = await self.collection.insert_one(document)
            document["_id"] = result.inserted_id
            return document, True
        except DuplicateKeyError:
            existing = await self.get_by_instagram_comment_id(
                document["instagram_account_id"], document["instagram_comment_id"]
            )
            if existing is None:  # pragma: no cover - only on a concurrent delete
                raise
            return existing, False

    async def weekly_counts(self, instagram_account_id: Any, since: Any) -> list[Document]:
        """How many times each person commented, per ISO week.

        ISO weeks (%G-%V) rather than "last 7 days" rolling windows: a superfan
        is defined by turning up week after week, and that only means something
        against a fixed calendar boundary both sides agree on.
        """
        pipeline: list[Document] = [
            {
                "$match": {
                    "instagram_account_id": to_object_id(instagram_account_id),
                    "commented_at": {"$gte": since},
                    "instagram_user_id": {"$nin": [None, ""]},
                }
            },
            {
                "$group": {
                    "_id": {
                        "user": "$instagram_user_id",
                        "week": {"$dateToString": {"format": "%G-%V", "date": "$commented_at"}},
                    },
                    "count": {"$sum": 1},
                    "username": {"$last": "$username"},
                    "last_at": {"$max": "$commented_at"},
                }
            },
            {"$sort": {"_id.week": 1}},
        ]
        cursor = await self.collection.aggregate(pipeline)
        return [
            {
                "instagram_user_id": doc["_id"]["user"],
                "week": doc["_id"]["week"],
                "count": int(doc["count"]),
                "username": doc.get("username") or "",
                "last_at": doc.get("last_at"),
            }
            async for doc in cursor
        ]
