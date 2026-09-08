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

    async def drop_orphaned(self, account_ids: list[Any]) -> int:
        """Delete media belonging to accounts that no longer exist.

        A disconnect used to hard-delete the account row and leave its posts
        behind, keyed on an `_id` nothing pointed at any more -- 168 such rows on
        the live install, across four dead accounts. They cannot be remapped:
        `media` carries no `instagram_user_id`, so there is no way to tell which
        Instagram account a row belonged to once its account document is gone.
        Deleting them is safe because media is a re-fetchable cache of what Meta
        holds; the automations that referenced them are left alone, since those
        contain text somebody wrote.

        The caller passes the live account ids rather than this method reading
        them, so the guard against an empty list is impossible to skip -- see
        `prepare`.
        """
        if not account_ids:
            raise ValueError(
                "drop_orphaned needs the live account ids; an empty list would "
                "delete every media row in the database"
            )
        result = await self.collection.delete_many(
            {"instagram_account_id": {"$nin": [to_object_id(a) for a in account_ids]}}
        )
        return int(result.deleted_count)
