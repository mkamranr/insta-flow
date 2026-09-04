import logging
from typing import Any

from .base import BaseRepository, Document, to_object_id, utcnow

logger = logging.getLogger(__name__)


class UsersRepository(BaseRepository[Any]):
    collection_name = "users"

    async def get_by_email(self, email: str) -> Document | None:
        return await self.collection.find_one({"email": email.strip().lower()})

    async def create(
        self,
        email: str,
        password_hash: str,
        *,
        role_id: Any,
        is_bootstrap: bool = False,
    ) -> Document:
        """Create a user. `role_id` is required and keyword-only, deliberately.

        A default hidden in here is how an accidental admin gets minted. Making it
        required means the type checker names every call site instead.
        """
        now = utcnow()
        document = {
            "email": email.strip().lower(),
            "password_hash": password_hash,
            "is_active": True,
            "role_id": to_object_id(role_id),
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

    async def set_password(self, user_id: Any, password_hash: str) -> None:
        await self.update(user_id, {"password_hash": password_hash})

    async def count_with_role(self, role_id: Any) -> int:
        return await self.count({"role_id": to_object_id(role_id)})

    async def count_active_with_role(self, role_id: Any) -> int:
        return await self.count({"role_id": to_object_id(role_id), "is_active": True})

    async def backfill_missing_roles(self, role_id: Any) -> int:
        """Give every pre-RBAC user the Owner role. Runs once, then no-ops.

        Owner, and nothing smaller: before this feature there was no authorization
        at all, so every existing user already had every capability. Owner preserves
        that exactly and grants nothing new. Anything less would lock a live
        operator out of their own system on upgrade.

        Matched on `$exists: false` -- never on null, and never on a dangling
        reference. A user whose role was deleted must fail closed with no
        permissions, not be quietly promoted back to Owner.
        """
        result = await self.collection.update_many(
            {"role_id": {"$exists": False}},
            {"$set": {"role_id": to_object_id(role_id), "updated_at": utcnow()}},
        )
        if result.modified_count:
            # WARNING, not INFO: a silent mass privilege grant should leave a trace
            # in the upgrade's logs.
            logger.warning("users_backfilled_to_owner", extra={"count": result.modified_count})
        return int(result.modified_count)
