from typing import Any

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from .base import BaseRepository, Document, to_object_id, utcnow


class InstagramAccountsRepository(BaseRepository[Any]):
    collection_name = "instagram_accounts"

    async def get_by_instagram_user_id(self, instagram_user_id: str) -> Document | None:
        return await self.collection.find_one({"instagram_user_id": str(instagram_user_id)})

    # An operator disconnect marks the row rather than deleting it, so that
    # reconnecting the same Instagram account reuses it -- `upsert` is keyed on
    # `instagram_user_id` -- and its posts and automations come back with it.
    # The consequence is that "this workspace's account" now has to mean "not
    # disconnected" everywhere, or the app reads a dead account as live. Named
    # once here rather than repeated as a query filter at each call site.
    LIVE = {"status": {"$ne": "disconnected"}}

    async def live_for_user(self, user_id: str | Any) -> list[Document]:
        """The accounts a workspace can actually act on, oldest first.

        What `_only_account` and the analytics resolvers want. `list_for_user`
        still returns disconnected rows, because the Instagram page has to offer
        to reconnect them.
        """
        return await self.all_matching({"user_id": to_object_id(user_id), **self.LIVE})

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

    async def pause_automation(self, account_id: Any, *, until: Any, now: Any) -> Document | None:
        """Hold every automated send for this account until `until`.

        The backlog window is written first, and only when it is missing or
        null: a second pause started before the first one's held comments were
        answered must not move the window forward and hide them.
        """
        await self.collection.update_one(
            {"_id": to_object_id(account_id), "automation_backlog_since": None},
            {"$set": {"automation_backlog_since": now}},
        )
        return await self.update(
            account_id, {"automation_paused_until": until, "automation_paused_at": now}
        )

    async def resume_automation(self, account_id: Any, *, now: Any) -> Document | None:
        """End the pause.

        Writes `now` rather than unsetting the field, so a cancelled pause and
        one that lapsed on its own are the same state and everything reading it
        has one code path.

        Deliberately does not touch `automation_backlog_since`: cancelling is not
        the same as deciding what to do with what piled up, and that decision is
        still owed.
        """
        return await self.update(account_id, {"automation_paused_until": now})

    async def close_pause_backlog(self, account_id: Any, *, now: Any) -> Document | None:
        """Answer the held comments -- by delivering or discarding them.

        Advancing the window is what retires them, which is also how rows that
        can never be replayed (their rule or webhook event is gone) stop being
        offered forever.
        """
        return await self.update(account_id, {"automation_backlog_since": now})

    async def set_status(self, account_id: Any, status: str, error: str | None = None) -> None:
        await self.update(account_id, {"status": status, "status_error": error})

    async def start_media_resync(self, account_id: Any) -> Document | None:
        """Stamp a full resync as running.

        Written by the task rather than the route that queued it, the same way
        `close_pause_backlog` is: if the queue were down, the button must not
        have already reported success.
        """
        return await self.update(
            account_id,
            {
                "media_resync_state": "running",
                "media_resync_started_at": utcnow(),
                "media_resync_error": None,
            },
        )

    async def finish_media_resync(
        self, account_id: Any, *, count: int, pages: int, error: str | None = None
    ) -> Document | None:
        return await self.update(
            account_id,
            {
                "media_resync_state": "failed" if error else "done",
                "media_resync_finished_at": utcnow(),
                "media_resync_count": count,
                "media_resync_pages": pages,
                "media_resync_error": error,
            },
        )

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
        chosen = await self.collection.find_one(
            {"user_id": owner, "is_default": True, **self.LIVE}
        )
        if chosen is not None:
            return chosen
        found = await self.live_for_user(owner)
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
        found = await self.live_for_user(owner)
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
