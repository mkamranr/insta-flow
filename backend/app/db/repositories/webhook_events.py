import hashlib
from typing import Any

from pymongo.errors import DuplicateKeyError

from .base import BaseRepository, Document, utcnow


def event_hash(raw_body: bytes) -> str:
    """Deterministic idempotency key for a webhook delivery (§25).

    Hashing the raw bytes means a redelivery of the identical payload collides,
    while a genuinely different event does not.
    """
    return hashlib.sha256(raw_body).hexdigest()


class WebhookEventsRepository(BaseRepository[Any]):
    collection_name = "webhook_events"

    async def record(
        self, *, raw_body: bytes, payload: Document, event_type: str
    ) -> tuple[Document, bool]:
        """Persist a webhook delivery. Returns (document, is_new).

        is_new=False means this exact payload was already received and must not be
        processed a second time.
        """
        document = {
            "event_hash": event_hash(raw_body),
            "event_type": event_type,
            "payload": payload,
            "status": "received",
            "received_at": utcnow(),
            "processed_at": None,
            "error": None,
        }
        try:
            result = await self.collection.insert_one(document)
            document["_id"] = result.inserted_id
            return document, True
        except DuplicateKeyError:
            existing = await self.collection.find_one({"event_hash": document["event_hash"]})
            if existing is None:  # pragma: no cover - only on a concurrent delete
                raise
            return existing, False

    async def mark_processed(self, event_id: Any, error: str | None = None) -> None:
        await self.update(
            event_id,
            {
                "status": "failed" if error else "processed",
                "processed_at": utcnow(),
                "error": error,
            },
        )
