from typing import Any

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from .base import BaseRepository, Document, to_object_id, utcnow


class InstagramAccountsRepository(BaseRepository[Any]):
    collection_name = "instagram_accounts"

    async def get_by_instagram_user_id(self, instagram_user_id: str) -> Document | None:
        return await self.collection.find_one({"instagram_user_id": str(instagram_user_id)})

    async def list_for_user(self, user_id: str | Any) -> list[Document]:
        """Every account in one workspace, oldest first.

        Oldest first, and uncapped: `BaseRepository.list` sorts newest-first
        through a 50-row page cap, neither of which suits "which account does an
        unqualified request act on". The answer has to be stable across calls.
        """
        return await self.all_matching({"user_id": to_object_id(user_id)})

    async def all_matching(self, query: Document | None = None) -> list[Document]:
        """Every matching account, oldest first, with no page cap.

        `BaseRepository.list` caps at 50 (200 maximum) and that ceiling is
        instance-wide, not per workspace -- so the daily jobs would silently stop
        refreshing the 51st account's token and let it expire.
        """
        cursor = self.collection.find(query or {}).sort("created_at", ASCENDING)
        return [doc async for doc in cursor]

    async def upsert(self, instagram_user_id: str, values: Document) -> Document | None:
        """Reconnecting an account must update in place, not create a duplicate.

        `instagram_user_id` carries a unique index, so this is the only safe path.

        `created_at` is stamped only on insert. Without it an OAuth-created
        account carried no `created_at` at all, and every ordering in this
        repository sorts on exactly that field -- so "the workspace's first
        account" was undefined the moment a second one existed, and
        `InstagramAccountPublic.created_at` was always null.

        Stripped from `values` first: naming a field in both `$set` and
        `$setOnInsert` is a write conflict Mongo rejects outright, and a
        reconnect must not move the date anyway.
        """
        values = {key: value for key, value in values.items() if key != "created_at"}
        return await self.collection.find_one_and_update(
            {"instagram_user_id": str(instagram_user_id)},
            {"$set": values, "$setOnInsert": {"created_at": utcnow()}},
            upsert=True,
            return_document=True,
        )

    async def set_status(self, account_id: Any, status: str, error: str | None = None) -> None:
        await self.update(account_id, {"status": status, "status_error": error})

    async def backfill_created_at(self) -> int:
        """Recover `created_at` for rows written before `upsert` stamped it.

        `$toDate` on an ObjectId is its generation time -- the exact insert
        moment, rather than an approximation from `updated_at`, which every
        token refresh and every daily snapshot has already overwritten.

        Matched with `None` rather than `$type`, which is the opposite of the
        rule `indexes.py` follows: here we want the rows that LACK the field, and
        `{"field": None}` matches missing and explicit null alike. There the
        concern is a unique index's partial filter, where matching an explicit
        null reintroduces the collision the filter exists to avoid.
        """
        result = await self.collection.update_many(
            {"created_at": None}, [{"$set": {"created_at": {"$toDate": "$_id"}}}]
        )
        return int(result.modified_count)

    async def default_for_user(self, user_id: str | Any) -> Document | None:
        """The account an unqualified request acts on.

        The chosen default, failing that the oldest. The fallback is reachable
        only in the instant between `set_default`'s two writes, or before the
        backfill has run on an upgrade.
        """
        owner = to_object_id(user_id)
        chosen = await self.collection.find_one({"user_id": owner, "is_default": True})
        if chosen is not None:
            return chosen
        found = await self.all_matching({"user_id": owner})
        return found[0] if found else None

    async def set_default(self, user_id: str | Any, account_id: Any) -> None:
        """Move the default to this account.

        Unset then set: two writes, deliberately in that order. A moment with no
        default degrades to "the oldest", which is a legible answer; a moment
        with two violates `uq_default_account_per_user` and fails the request
        outright.
        """
        owner = to_object_id(user_id)
        await self.collection.update_many(
            {"user_id": owner, "is_default": True}, {"$unset": {"is_default": ""}}
        )
        await self.collection.update_one(
            {"_id": to_object_id(account_id), "user_id": owner}, {"$set": {"is_default": True}}
        )

    async def ensure_default(self, user_id: str | Any) -> None:
        """Make a workspace's first account its default. Called after OAuth.

        Reads before writing, which the duplicate-protection rule normally
        forbids -- but the unique index remains the authority. A lost race raises
        DuplicateKeyError, and that error means another writer has already
        produced the exact end state this method wanted, so ignoring it is
        correct rather than merely convenient.

        Not `$setOnInsert: {"is_default": True}` in `upsert`: that would try to
        make every newly connected account the default and fail the OAuth
        callback outright on the second one.
        """
        owner = to_object_id(user_id)
        if await self.collection.find_one({"user_id": owner, "is_default": True}) is not None:
            return
        found = await self.all_matching({"user_id": owner})
        if not found:
            return
        try:
            await self.collection.update_one(
                {"_id": found[0]["_id"]}, {"$set": {"is_default": True}}
            )
        except DuplicateKeyError:
            return

    async def backfill_defaults(self) -> int:
        """Give every workspace that has accounts but no default one: the oldest.

        Runs after `backfill_created_at`, because "oldest" is meaningless until
        every row carries the field that orders them.
        """
        owners = await self.collection.distinct("user_id")
        marked = 0
        for owner in owners:
            if await self.collection.find_one({"user_id": owner, "is_default": True}) is not None:
                continue
            found = await self.all_matching({"user_id": owner})
            if not found:
                continue
            try:
                await self.collection.update_one(
                    {"_id": found[0]["_id"]}, {"$set": {"is_default": True}}
                )
                marked += 1
            except DuplicateKeyError:
                continue
        return marked
