"""Captured leads."""

from typing import Any

from .base import BaseRepository, Document, to_object_id, utcnow


class ContactsRepository(BaseRepository[Any]):
    collection_name = "contacts"

    async def upsert(
        self, instagram_account_id: Any, instagram_user_id: str, values: Document
    ) -> Document | None:
        """One row per person per account.

        Someone can trigger several rules; overwriting keeps the latest details
        rather than accumulating near-duplicates.
        """
        return await self.collection.find_one_and_update(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "instagram_user_id": str(instagram_user_id),
            },
            {"$set": {**values, "updated_at": utcnow()}, "$setOnInsert": {"created_at": utcnow()}},
            upsert=True,
            return_document=True,
        )

    async def list_for_account(self, instagram_account_id: Any) -> list[Document]:
        return await self.list({"instagram_account_id": to_object_id(instagram_account_id)})
