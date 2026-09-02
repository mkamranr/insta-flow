"""Daily point-in-time counters for a connected account.

Meta returns `followers_count` as a single number and offers no history for it
(docs/meta-api.md §2b), so follower growth cannot be asked for -- it has to be
derived from snapshots this app records itself. One row per account per calendar
day; growth is the difference between two rows.

`day` is an ISO `YYYY-MM-DD` string rather than a datetime: "one snapshot per
day" is a calendar fact, and a string removes any question of which instant in
the day a stored timestamp refers to. ISO dates also sort lexicographically, so
range queries behave.
"""

from typing import Any

from .base import BaseRepository, Document, to_object_id, utcnow


class AccountSnapshotsRepository(BaseRepository[Any]):
    collection_name = "account_snapshots"

    async def record(
        self,
        instagram_account_id: Any,
        day: str,
        *,
        followers_count: int | None,
        follows_count: int | None = None,
        media_count: int | None = None,
    ) -> Document | None:
        """Write today's counters, replacing an earlier run on the same day.

        Upsert against the unique `(instagram_account_id, day)` index rather than
        checking first: a retry, a restart, or two workers must converge on one
        row per day without a read-modify-write race (§5).
        """
        return await self.collection.find_one_and_update(
            {"instagram_account_id": to_object_id(instagram_account_id), "day": day},
            {
                "$set": {
                    "followers_count": followers_count,
                    "follows_count": follows_count,
                    "media_count": media_count,
                    "captured_at": utcnow(),
                },
                "$setOnInsert": {"first_captured_at": utcnow()},
            },
            upsert=True,
            return_document=True,
        )

    async def history(self, instagram_account_id: Any, since_day: str) -> list[Document]:
        """Snapshots from `since_day` onward, oldest first."""
        cursor = self.collection.find(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "day": {"$gte": since_day},
            }
        ).sort("day", 1)
        return [doc async for doc in cursor]

    async def earliest_day(self, instagram_account_id: Any) -> str | None:
        """The first day we have data for, so the UI can say when collection began.

        Without this a fresh install cannot tell "no growth" apart from "no
        history yet", and would report a misleading zero.
        """
        doc = await self.collection.find_one(
            {"instagram_account_id": to_object_id(instagram_account_id)},
            sort=[("day", 1)],
        )
        return str(doc["day"]) if doc else None
