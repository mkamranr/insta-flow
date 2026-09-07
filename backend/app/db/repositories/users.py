import logging
from typing import Any

from pymongo import ASCENDING

from app.services import slugs

from .base import BaseRepository, Document, to_object_id, utcnow

logger = logging.getLogger(__name__)


class UsersRepository(BaseRepository[Any]):
    collection_name = "users"

    async def get_by_email(self, email: str) -> Document | None:
        return await self.collection.find_one({"email": email.strip().lower()})

    async def get_by_slug(self, slug: str) -> Document | None:
        """Resolve a workspace slug -- the `/u/<slug>/` segment -- to its owner."""
        return await self.collection.find_one({"slug": slug.strip().lower()})

    async def claim_slug(self, email: str) -> str:
        """A slug for a new user, made unique by suffixing.

        Advisory only. `uq_user_slug` is the authority: two concurrent creates can
        both read "free" here and only one insert will survive, which is the
        intended outcome and why the caller must be ready for a DuplicateKeyError.
        """
        base = slugs.from_email(email)
        candidate = base
        suffix = 2
        while await self.get_by_slug(candidate) is not None:
            tail = f"-{suffix}"
            candidate = f"{base[: slugs.MAX_LENGTH - len(tail)].rstrip('-')}{tail}"
            suffix += 1
        return candidate

    async def create(
        self,
        email: str,
        password_hash: str,
        *,
        role_id: Any,
        is_bootstrap: bool = False,
        slug: str | None = None,
    ) -> Document:
        """Create a user. `role_id` is required and keyword-only, deliberately.

        A default hidden in here is how an accidental admin gets minted. Making it
        required means the type checker names every call site instead.

        `slug` is for the one caller that has a slug the *user* chose -- accepting
        an invitation. Everywhere else it stays None and is derived from the
        email, so all the existing call sites behave exactly as before. Note
        `claim_slug` is only advisory either way: `uq_user_slug` decides, and a
        caller-supplied slug that collides raises `DuplicateKeyError` for the
        route to turn into "that address was just taken".
        """
        now = utcnow()
        email = email.strip().lower()
        document = {
            "email": email,
            "slug": slug or await self.claim_slug(email),
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

    async def backfill_missing_slugs(self) -> int:
        """Give every pre-workspace user a slug. Runs once, then no-ops.

        A slug is what puts a user at `/u/<slug>/`, so an upgraded deployment has
        to acquire them before any workspace URL can resolve. Assigned one at a
        time rather than in a single `update_many`: each slug must be unique, and
        `claim_slug` can only see the ones already written.

        Ordered by `_id`, which is ascending by creation time, so the oldest user
        -- on a single-operator install, the one whose Meta app is already live --
        gets the unsuffixed slug.
        """
        assigned = 0
        cursor = self.collection.find({"slug": {"$exists": False}}).sort("_id", ASCENDING)
        async for user in cursor:
            slug = await self.claim_slug(str(user.get("email", "")))
            await self.collection.update_one(
                {"_id": user["_id"], "slug": {"$exists": False}},
                {"$set": {"slug": slug, "updated_at": utcnow()}},
            )
            assigned += 1

        if assigned:
            logger.info("users_backfilled_slugs", extra={"count": assigned})
        return assigned

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

    async def set_onboarding_step(self, user_id: Any, step_id: str, done: bool) -> Document:
        """Record one step of the guided setup as done or not done.

        Kept on the user document rather than in a collection of its own: it is
        a handful of booleans per person, and `current_user` has already loaded
        this document on every request, so a read costs nothing extra.

        Dotted `$set` so two steps ticked from two tabs cannot clobber each
        other by writing the whole map back.
        """
        updated = await self.collection.find_one_and_update(
            {"_id": to_object_id(user_id)},
            {
                "$set": {
                    f"onboarding.steps.{step_id}": {"done": bool(done), "at": utcnow()},
                    "updated_at": utcnow(),
                }
            },
            return_document=True,
        )
        return updated or {}

    async def dismiss_onboarding(self, user_id: Any) -> Document:
        """Hide the setup banner. Deliberately permanent, and deliberately not
        the same thing as finishing: the banner clears by itself once an
        Instagram account is connected."""
        updated = await self.collection.find_one_and_update(
            {"_id": to_object_id(user_id)},
            {"$set": {"onboarding.dismissed": True, "updated_at": utcnow()}},
            return_document=True,
        )
        return updated or {}

    async def emails_in(self, emails: list[str]) -> set[str]:
        """Which of these addresses already have an account.

        One query for a list, rather than one per address: the Contacts page
        asks about every lead it is about to show.
        """
        if not emails:
            return set()
        cursor = self.collection.find(
            {"email": {"$in": [e.strip().lower() for e in emails]}}, {"email": 1}
        )
        return {str(row["email"]) async for row in cursor}
