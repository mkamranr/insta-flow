"""Where a cached thumbnail lives, recorded on the media or story row.

Shared by `MediaRepository` and `StoriesRepository` because the two collections
have the same problem and must not drift: both hold Meta CDN URLs that expire
(docs/meta-api.md §2d) and both need to remember which ones are dead, so a
failed image is not re-attempted on every page render.

The record is a sub-document rather than its own collection. It is 1:1 with the
row, it has no independent lifetime, and riding along means `drop_orphaned`
already cleans it up.
"""

from typing import Any

from .base import Document, to_object_id, utcnow

FIELD = "image_cache"


class ImageCacheMixin:
    """Adds thumbnail-cache bookkeeping to a repository.

    The host class supplies `collection` and `_image_key`, which names the
    id field the collection is keyed on -- `instagram_media_id` for media,
    `instagram_story_id` for stories.
    """

    collection: Any
    _image_key: str

    def _image_filter(self, instagram_account_id: Any, media_id: str) -> Document:
        return {
            "instagram_account_id": to_object_id(instagram_account_id),
            self._image_key: str(media_id),
        }

    async def set_image_cache(
        self, instagram_account_id: Any, media_id: str, values: Document
    ) -> None:
        """Record a successful download, clearing any earlier failure."""
        await self.collection.update_one(
            self._image_filter(instagram_account_id, media_id),
            {
                "$set": {
                    FIELD: {**values, "cached_at": utcnow(), "failed_at": None, "failure": None}
                }
            },
        )

    async def record_image_failure(
        self, instagram_account_id: Any, media_id: str, failure: str
    ) -> None:
        """Mark an image as unfetchable.

        Load-bearing rather than diagnostic: without it the request-time
        backstop re-hits every dead URL on every render of the page.

        A previously cached file is deliberately left in place -- a stale
        picture beats a placeholder, and the bytes are all we will ever have.
        """
        await self.collection.update_one(
            self._image_filter(instagram_account_id, media_id),
            {"$set": {f"{FIELD}.failed_at": utcnow(), f"{FIELD}.failure": failure}},
        )

    async def needing_image_cache(self, instagram_account_id: Any, limit: int) -> list[Document]:
        """Rows with no usable cached image and no recorded failure.

        Excluding past failures is what stops a cold sync spending its whole
        budget re-trying the same dead URLs and never reaching a fresh post.
        """
        cursor = (
            self.collection.find(
                {
                    "instagram_account_id": to_object_id(instagram_account_id),
                    f"{FIELD}.storage_key": {"$exists": False},
                    f"{FIELD}.failed_at": None,
                    "$or": [
                        {"thumbnail_url": {"$nin": [None, ""]}},
                        {"media_url": {"$nin": [None, ""]}},
                    ],
                }
            )
            .sort("published_at", -1)
            .limit(max(0, limit))
        )
        return [document async for document in cursor]

    async def clear_image_cache(self, document_id: Any) -> None:
        """Forget a cached file, after its bytes have been deleted from disk."""
        await self.collection.update_one(
            {"_id": to_object_id(document_id)}, {"$unset": {FIELD: ""}}
        )
