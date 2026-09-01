from typing import Any

from .base import BaseRepository, Document, to_object_id


class InstagramAccountsRepository(BaseRepository[Any]):
    collection_name = "instagram_accounts"

    async def get_by_instagram_user_id(self, instagram_user_id: str) -> Document | None:
        return await self.collection.find_one({"instagram_user_id": str(instagram_user_id)})

    async def list_for_user(self, user_id: str | Any) -> list[Document]:
        return await self.list({"user_id": to_object_id(user_id)})

    async def upsert(self, instagram_user_id: str, values: Document) -> Document | None:
        """Reconnecting an account must update in place, not create a duplicate.

        `instagram_user_id` carries a unique index, so this is the only safe path.
        """
        return await self.collection.find_one_and_update(
            {"instagram_user_id": str(instagram_user_id)},
            {"$set": values},
            upsert=True,
            return_document=True,
        )

    async def set_status(self, account_id: Any, status: str, error: str | None = None) -> None:
        await self.update(account_id, {"status": status, "status_error": error})
