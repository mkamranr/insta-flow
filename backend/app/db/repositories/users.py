from typing import Any

from .base import BaseRepository, Document, to_object_id, utcnow


class UsersRepository(BaseRepository[Any]):
    collection_name = "users"

    async def get_by_email(self, email: str) -> Document | None:
        return await self.collection.find_one({"email": email.strip().lower()})

    async def create(
        self, email: str, password_hash: str, *, is_bootstrap: bool = False
    ) -> Document:
        now = utcnow()
        document = {
            "email": email.strip().lower(),
            "password_hash": password_hash,
            "is_active": True,
            # Bumped on logout to invalidate every cookie already issued.
            "session_epoch": 0,
            "created_at": now,
            "updated_at": now,
            "last_login_at": None,
        }
        if is_bootstrap:
            # Carried only by the first admin, and covered by a partial unique
            # index -- two concurrent setup requests cannot both succeed.
            document["is_bootstrap"] = True
        document["_id"] = await self.insert(document)
        return document

    async def touch_login(self, user_id: Any) -> None:
        await self.update(user_id, {"last_login_at": utcnow()})

    async def bump_session_epoch(self, user_id: Any) -> int:
        """Invalidate all outstanding sessions for this user. Returns the new epoch."""
        updated = await self.collection.find_one_and_update(
            {"_id": to_object_id(user_id)},
            {"$inc": {"session_epoch": 1}, "$set": {"updated_at": utcnow()}},
            return_document=True,
        )
        return int(updated["session_epoch"]) if updated else 0
