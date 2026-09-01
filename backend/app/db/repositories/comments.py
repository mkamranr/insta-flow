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
