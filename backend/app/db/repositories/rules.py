"""Post rules: one post + one keyword + one message.

Replaces the campaign/trigger/template triple. A rule is a single document, so
creating, editing, and deleting one is a single operation with nothing to keep
in sync.
"""

from typing import Any

from pymongo import DESCENDING

from .base import BaseRepository, Document, to_object_id


class RulesRepository(BaseRepository[Any]):
    collection_name = "rules"

    async def list_for_media(
        self, instagram_account_id: Any, instagram_media_id: str
    ) -> list[Document]:
        """Enabled rules for one post, highest priority first.

        Ties break on id so selection is deterministic — without it two
        equal-priority rules could alternate between runs.
        """
        cursor = self.collection.find(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "enabled": True,
                # Comment rules only. `$in` with None matches a MISSING field as
                # well as an explicit null, which is what lets rules written
                # before `trigger` existed keep working with no backfill.
                #
                # Without this clause every existing rule would also fire on
                # story replies and inbound DMs.
                "trigger": {"$in": ["comment", None]},
                "$or": [
                    {"scope": "any"},
                    {"instagram_media_ids": str(instagram_media_id)},
                ],
            }
        ).sort([("priority", DESCENDING), ("_id", DESCENDING)])
        return [doc async for doc in cursor]

    async def list_for_trigger(
        self,
        instagram_account_id: Any,
        trigger: str,
        instagram_story_id: str | None = None,
    ) -> list[Document]:
        """Enabled rules for a non-comment trigger, highest priority first.

        Exact match on a value legacy rows cannot hold, so pre-`trigger` rules
        are excluded automatically. Ties break on id, as in `list_for_media`, so
        selection is deterministic.

        `instagram_story_id` narrows story replies to the rules that target that
        story. An empty or missing `instagram_story_ids` means every story, so
        `$in` with None is used again -- rules written before per-story targeting
        existed have no such field and must keep matching.
        """
        query: Document = {
            "instagram_account_id": to_object_id(instagram_account_id),
            "enabled": True,
            "trigger": trigger,
        }
        if instagram_story_id is not None:
            query["$or"] = [
                {"instagram_story_ids": {"$in": [[], None]}},
                {"instagram_story_ids": {"$exists": False}},
                {"instagram_story_ids": str(instagram_story_id)},
            ]
        cursor = self.collection.find(query).sort([("priority", DESCENDING), ("_id", DESCENDING)])
        return [doc async for doc in cursor]

    async def disable_for_expired_stories(
        self, instagram_account_id: Any, expired_ids: list[str]
    ) -> list[Document]:
        """Disable story rules whose every target story has now expired.

        Only rules that opted in via `expire_with_story`. A rule targeting three
        stories stays enabled while any one of them is still live, so this
        cannot be a blanket update -- each rule's remaining targets are checked.
        """
        if not expired_ids:
            return []

        gone = {str(i) for i in expired_ids}
        cursor = self.collection.find(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "trigger": "story_reply",
                "enabled": True,
                "expire_with_story": True,
                "instagram_story_ids": {"$in": list(gone)},
            }
        )
        disabled: list[Document] = []
        async for rule in cursor:
            targets = {str(i) for i in rule.get("instagram_story_ids") or []}
            if targets - gone:
                continue  # still has a live story to match on
            await self.collection.update_one(
                {"_id": rule["_id"]},
                {"$set": {"enabled": False, "disabled_reason": "story_expired"}},
            )
            disabled.append(rule)
        return disabled

    async def list_for_account(self, instagram_account_id: Any) -> list[Document]:
        return await self.list(
            {"instagram_account_id": to_object_id(instagram_account_id)},
            sort_field="priority",
        )

    async def all_for_account(self, instagram_account_id: Any) -> list[Document]:
        """Every rule for one account, highest priority first, with no page cap.

        `BaseRepository.list` stops at 50. That ceiling silently truncated the
        Automations page and per-rule analytics -- tolerable while the table was
        an undifferentiated list, actively wrong once a pager underneath it
        states a total. The rules past the fiftieth still fired the whole time;
        `list_for_media` and `list_for_trigger` read uncapped cursors.

        Ties break on id, as they do in `list_for_media` -- every rule defaults
        to `priority=100`, so without a tiebreak Mongo may return a different
        order on each refetch and rows would jump between pages.

        Deliberately not hoisted onto `BaseRepository`, for the reason
        `ContactsRepository.all_for_account` gives: the cap is a sensible
        default everywhere else, and a base-class escape hatch invites the next
        uncapped query.
        """
        cursor = self.collection.find(
            {"instagram_account_id": to_object_id(instagram_account_id)}
        ).sort([("priority", DESCENDING), ("_id", DESCENDING)])
        return [document async for document in cursor]

    async def counts_by_media(self, instagram_account_id: Any) -> dict[str, int]:
        """How many rules each post has, for the posts list."""
        pipeline: list[Document] = [
            {"$match": {"instagram_account_id": to_object_id(instagram_account_id)}},
            {"$unwind": "$instagram_media_ids"},
            {"$group": {"_id": "$instagram_media_ids", "n": {"$sum": 1}}},
        ]
        return {
            str(doc["_id"]): doc["n"] async for doc in await self.collection.aggregate(pipeline)
        }

    async def counts_by_story(self, instagram_account_id: Any) -> dict[str, int]:
        """How many rules each story has, for the picker."""
        pipeline: list[Document] = [
            {"$match": {"instagram_account_id": to_object_id(instagram_account_id)}},
            {"$unwind": "$instagram_story_ids"},
            {"$group": {"_id": "$instagram_story_ids", "n": {"$sum": 1}}},
        ]
        return {
            str(doc["_id"]): doc["n"] async for doc in await self.collection.aggregate(pipeline)
        }

    async def count_for_scope_any(self, instagram_account_id: Any) -> int:
        return await self.collection.count_documents(
            {"instagram_account_id": to_object_id(instagram_account_id), "scope": "any"}
        )

    async def count_for_media(self, instagram_account_id: Any, instagram_media_id: str) -> int:
        """How many rules target this post, enabled or not.

        Deliberately unfiltered by `enabled`: a paused rule still makes the post
        read "Automated" in the UI (`counts_by_media` does not filter either), so
        the clone picker must treat it as taken rather than quietly stacking a
        second automation on top of it.
        """
        return await self.count(
            {
                "instagram_account_id": to_object_id(instagram_account_id),
                "instagram_media_ids": str(instagram_media_id),
            }
        )

    async def count_referencing_attachment(self, storage_key: str, exclude_id: Any) -> int:
        """How many OTHER rules point at this stored file.

        `attachments.delete` removes the whole upload folder, so deleting a rule
        that shares a `storage_key` with another would take the file out from
        under it -- and the worker only logs a warning when a send fails, so the
        recipient silently gets the text and no file. Checked before every
        delete; no index needed, `rules` is per-account and small.
        """
        if not storage_key:
            return 0
        return await self.count(
            {"attachment.storage_key": storage_key, "_id": {"$ne": to_object_id(exclude_id)}}
        )
