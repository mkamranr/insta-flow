"""Runtime settings an operator can change without editing `.env`.

Everything security-critical stays in the environment. This collection holds only
things that are legitimately editable from the dashboard -- currently the MaxMind
credentials, whose license key is encrypted at rest the same way Meta tokens are
(§4: secrets never leave the server).

One document, fixed `_id`, so there is no "which row is current" question.
"""

from typing import Any

from .base import BaseRepository, Document, utcnow

SETTINGS_ID = "app"


class AppSettingsRepository(BaseRepository[Any]):
    collection_name = "app_settings"

    async def load(self) -> Document:
        """Current settings, or an empty dict before anything has been saved."""
        return await self.collection.find_one({"_id": SETTINGS_ID}) or {}

    async def save(self, values: Document) -> Document:
        """Upsert the singleton. Only the keys passed are touched."""
        updated = await self.collection.find_one_and_update(
            {"_id": SETTINGS_ID},
            {"$set": {**values, "updated_at": utcnow()}},
            upsert=True,
            return_document=True,
        )
        return updated or {}
