"""The Meta app a single workspace connects through.

One document per user, so a second person can bring their own Meta app instead
of sharing the instance's. That separation is what keeps this deployment out of
App Review: automating *your own* Instagram account needs only Live mode, while
reaching *someone else's* account through your app is the SaaS case that needs
Advanced Access, App Review and Business Verification. Each user's own app keeps
every user in the first category.

A separate collection rather than fields on the user document, deliberately:
`current_user` loads the whole user document on every single request, and an
encrypted app secret has no business riding along on all of them.

Both secrets are encrypted with the same AES-256-GCM key that protects Meta
access tokens. Only `services/meta/credentials.py` decrypts them.
"""

from typing import Any

from .base import BaseRepository, Document, to_object_id, utcnow


class MetaAppsRepository(BaseRepository[Any]):
    collection_name = "meta_apps"

    async def for_user(self, user_id: str | Any) -> Document | None:
        return await self.collection.find_one({"user_id": to_object_id(user_id)})

    async def save(self, user_id: str | Any, values: Document) -> Document:
        """Upsert one workspace's app. Only the keys passed are touched.

        Partial by design: saving an App ID must not silently clear a verify
        token that was generated in a separate step.
        """
        updated = await self.collection.find_one_and_update(
            {"user_id": to_object_id(user_id)},
            {"$set": {**values, "updated_at": utcnow()}, "$setOnInsert": {"created_at": utcnow()}},
            upsert=True,
            return_document=True,
        )
        return updated or {}

    async def clear(self, user_id: str | Any) -> bool:
        """Forget this workspace's app, falling the workspace back to the
        instance-wide credentials from the environment."""
        result = await self.collection.delete_one({"user_id": to_object_id(user_id)})
        return result.deleted_count == 1
