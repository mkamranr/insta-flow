"""Captured leads."""

from typing import Any

from pymongo import DESCENDING, UpdateOne

from .base import BaseRepository, Document, to_object_id, utcnow


class ContactsRepository(BaseRepository[Any]):
    collection_name = "contacts"

    async def capture(
        self,
        instagram_account_id: Any,
        instagram_user_id: str,
        values: Document,
        *,
        instagram_media_id: str | None,
        rule_id: Any,
        rule_name: str,
    ) -> Document | None:
        """Record that this person gave us their email, and where.

        Still one row per person per account -- `uq_account_user` -- but the row
        now accumulates a `sources` entry per place they were captured, so a post
        keeps the leads it earned instead of losing them the next time that person
        replies to something else.

        Two statements, deliberately. The tempting single guarded upsert --
        matching on "no such source yet" with `upsert=True` -- is a trap: on a
        repeat capture the filter matches nothing, Mongo falls back to an insert,
        and that collides with `uq_account_user`. The symptom is a
        DuplicateKeyError on the *third* capture from one post, by which time the
        email is not re-stamped and `email_captured` never logs.
        """
        account = to_object_id(instagram_account_id)
        person = str(instagram_user_id)
        rule = to_object_id(rule_id)
        now = utcnow()

        # The person. `sources` is seeded on insert only, and appears in neither
        # `$set` nor the `$push` below -- naming one field under two operators in
        # a single update is a Mongo path conflict.
        await self.collection.update_one(
            {"instagram_account_id": account, "instagram_user_id": person},
            {
                "$set": {**values, "updated_at": now},
                "$setOnInsert": {"created_at": now, "sources": []},
            },
            upsert=True,
        )

        # The capture. No `upsert`, so a source already on file matches nothing,
        # writes nothing and raises nothing. `$not` also matches a row with no
        # `sources` field at all, which is what makes this correct against a
        # contact the backfill has not reached yet.
        #
        # `rule_name` is outside the guard on purpose: renaming an automation
        # must not mint a second source. `$addToSet` cannot do this job at all --
        # `captured_at` differs every time, so nothing ever matches and it
        # degenerates into an unguarded `$push`.
        return await self.collection.find_one_and_update(
            {
                "instagram_account_id": account,
                "instagram_user_id": person,
                "sources": {
                    "$not": {
                        "$elemMatch": {
                            "instagram_media_id": instagram_media_id,
                            "rule_id": rule,
                        }
                    }
                },
            },
            {
                "$push": {
                    "sources": {
                        "instagram_media_id": instagram_media_id,
                        "rule_id": rule,
                        "rule_name": rule_name,
                        "captured_at": now,
                        "backfilled": False,
                    }
                },
                "$set": {"updated_at": now},
            },
            return_document=True,
        )

    async def list_for_account(self, instagram_account_id: Any) -> list[Document]:
        return await self.list({"instagram_account_id": to_object_id(instagram_account_id)})

    async def all_for_account(self, instagram_account_id: Any) -> list[Document]:
        """Every contact for one account, newest first, with no page cap.

        `BaseRepository.list` stops at 50 and clamps to 200. That ceiling was
        silently truncating both the Contacts page and its CSV: an operator with
        300 leads downloaded 50 and was told nothing. An export that is quietly
        incomplete is worse than no export.

        Deliberately not hoisted onto `BaseRepository` -- the cap is a sensible
        default everywhere else, and a base-class escape hatch invites the next
        uncapped query.
        """
        cursor = self.collection.find(
            {"instagram_account_id": to_object_id(instagram_account_id)}
        ).sort("created_at", DESCENDING)
        return [document async for document in cursor]

    async def backfill_sources(
        self,
        executions: "BaseRepository[Any]",
        comments: "BaseRepository[Any]",
        rules: "BaseRepository[Any]",
    ) -> int:
        """Give contacts written before `sources` existed a best guess at one.

        This is a reconstruction, not a record, and the data says so: every entry
        it writes is stamped `backfilled: true`. Nothing on an execution states
        "this one produced the email" -- by the time it finishes its status is
        `success` or `failed` like any other -- so the closest honest answer is
        "the posts this person was messaged about by an automation whose email
        gate was on".

        Only gated rules count. Attributing every execution would list someone who
        gave their address once under each of the five posts they ever commented
        on, which reads as fact and is not.

        Idempotent through `{"sources": {"$exists": False}}`. That only stays a
        watermark because every contact this touches ends up with at least one
        entry -- the "no post" fallback guarantees it. Leave one at `[]` and the
        job re-runs over it on every startup, forever.
        """
        unsourced = {"sources": {"$exists": False}}
        if not await self.collection.count_documents(unsourced, limit=1):
            return 0

        written = 0
        for account in await self.collection.distinct("instagram_account_id", unsourced):
            gated = {
                rule["_id"]: str(rule.get("name") or "")
                async for rule in rules.collection.find(
                    {"instagram_account_id": account, "collect_email.enabled": True},
                    {"name": 1},
                )
            }
            while (
                batch := await self.collection.find({**unsourced, "instagram_account_id": account})
                .limit(500)
                .to_list()
            ):
                written += await self._backfill_batch(account, batch, gated, executions, comments)
        return written

    async def _backfill_batch(
        self,
        account: Any,
        batch: list[Document],
        gated: dict[Any, str],
        executions: "BaseRepository[Any]",
        comments: "BaseRepository[Any]",
    ) -> int:
        """One page of contacts: two reads and one write, whatever the page size."""
        people = [str(c.get("instagram_user_id") or "") for c in batch]

        # One query per batch, not per contact -- this is what `ix_account_user_created`
        # is for; without it this scans the largest collection in the system.
        runs: dict[str, list[Document]] = {}
        if gated:
            async for run in executions.collection.find(
                {
                    "instagram_account_id": account,
                    "instagram_user_id": {"$in": people},
                    "rule_id": {"$in": list(gated)},
                },
                {"instagram_user_id": 1, "comment_id": 1, "rule_id": 1, "created_at": 1},
            ):
                runs.setdefault(str(run.get("instagram_user_id") or ""), []).append(run)

        comment_ids = [r["comment_id"] for rs in runs.values() for r in rs if r.get("comment_id")]
        media_of = {
            c["_id"]: c.get("instagram_media_id")
            async for c in comments.collection.find(
                {"_id": {"$in": comment_ids}}, {"instagram_media_id": 1}
            )
        }

        writes = [
            UpdateOne(
                {"_id": contact["_id"]},
                {"$set": {"sources": self._reconstruct(contact, runs, gated, media_of)}},
            )
            for contact in batch
        ]
        if writes:
            await self.collection.bulk_write(writes, ordered=False)
        return len(writes)

    @staticmethod
    def _reconstruct(
        contact: Document,
        runs: dict[str, list[Document]],
        gated: dict[Any, str],
        media_of: dict[Any, Any],
    ) -> list[Document]:
        """The sources for one contact. Never empty -- see `backfill_sources`."""
        fallback = contact.get("created_at") or utcnow()
        sources: list[Document] = []
        seen: set[tuple[Any, Any]] = set()

        for run in runs.get(str(contact.get("instagram_user_id") or ""), []):
            media_id = media_of.get(run.get("comment_id"))
            if media_id is None:
                continue
            key = (media_id, run.get("rule_id"))
            if key in seen:
                continue
            seen.add(key)
            sources.append(
                {
                    "instagram_media_id": media_id,
                    "rule_id": run.get("rule_id"),
                    "rule_name": gated.get(run.get("rule_id"), ""),
                    "captured_at": run.get("created_at") or fallback,
                    "backfilled": True,
                }
            )

        if sources:
            return sources
        return [
            {
                "instagram_media_id": None,
                "rule_id": contact.get("source_rule_id"),
                "rule_name": gated.get(contact.get("source_rule_id"), ""),
                "captured_at": fallback,
                "backfilled": True,
            }
        ]
