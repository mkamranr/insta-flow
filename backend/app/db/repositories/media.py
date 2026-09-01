from typing import Any

from .base import BaseRepository, Document, to_object_id


class MediaRepository(BaseRepository[Any]):
    collection_name = "media"

    async def get_by_instagram_media_id(
        self, instagram_account_id: Any, instagram_media_id: str
    ) -> Document | None:
        return await self.collection.find_one(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "instagram_media_id": str(instagram_media_id),
            }
        )

    async def upsert(
        self, instagram_account_id: Any, instagram_media_id: str, values: Document
    ) -> Document | None:
        return await self.collection.find_one_and_update(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "instagram_media_id": str(instagram_media_id),
            },
            {"$set": values},
            upsert=True,
            return_document=True,
        )
