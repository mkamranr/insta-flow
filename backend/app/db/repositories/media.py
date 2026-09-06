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

    async def by_instagram_media_ids(
        self, instagram_account_id: Any, instagram_media_ids: list[str]
    ) -> dict[str, Document]:
        """Post details for many ids at once, keyed by id.

        Resolving a page of media ids one `get_by_instagram_media_id` at a time
        is a round trip each; every caller is joining, so a dict is the shape
        they all want.

        Scoped to the account rather than a bare `$in` on the media id, and that
        is a security boundary rather than tidiness: these ids arrive from the
        caller, and an unscoped lookup would let `?post=<someone else's media id>`
        name another workspace's post.
        """
        if not instagram_media_ids:
            return {}
        cursor = self.collection.find(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "instagram_media_id": {"$in": [str(m) for m in instagram_media_ids]},
            }
        )
        return {document["instagram_media_id"]: document async for document in cursor}

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
