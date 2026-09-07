"""Every message the deployment tried to send, and what became of it.

Exists to answer "did that go out, and if not why" months later without a mail
server login. An asynchronous send whose only feedback is "queued" is precisely
the silent-nothing-happened failure this project keeps writing traps about.

**The rendered body is never stored, and neither is the variable context.** Two
independent reasons. An invitation body carries a single-use activation token, so
keeping it would turn this collection into a credential store with no expiry and
none of the encryption the token itself gets -- which would make hashing the
token pointless. And the body is the one field that grows without bound. The
template key plus the subject identifies a message; if variable names are ever
needed, store names, never values.

`to_address` is stored, because "did it reach that address" is otherwise
unanswerable -- but note that this makes `email_log` a third place email
addresses live, alongside `users` and `contacts`, and therefore part of the data
deletion story (`docs/security.md`). No IP, no user agent: neither helps answer
the question this collection exists for.
"""

from typing import Any

from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from .base import BaseRepository, Document, utcnow

# The vocabulary is lifted from ExecutionsRepository.STATUSES so the two read
# the same way; `sent` rather than `success` because that is the domain word and
# this is not an execution.
STATUSES = ("queued", "sending", "sent", "failed", "dry_run", "skipped")
TERMINAL_STATUSES = ("sent", "failed", "dry_run", "skipped")

MAX_ERROR_LENGTH = 300


class EmailLogRepository(BaseRepository[Any]):
    collection_name = "email_log"

    async def open(
        self,
        *,
        message_id: str,
        purpose: str,
        to_address: str,
        subject: str,
        requested_by: Any = None,
        invite_id: Any = None,
        dry_run: bool = False,
    ) -> Document | None:
        """Record the intent to send, before anything is enqueued.

        Returns None if this `message_id` already has a row. That cannot happen
        with a freshly minted Message-ID, so None means a programming error and
        the caller should surface it rather than silently continue.
        """
        now = utcnow()
        try:
            result = await self.collection.insert_one(
                {
                    "message_id": message_id,
                    "purpose": purpose,
                    "to_address": to_address,
                    "subject": subject,
                    "status": "queued",
                    "attempts": 0,
                    "error": None,
                    "smtp_code": None,
                    "retryable": None,
                    "requested_by": requested_by,
                    "invite_id": invite_id,
                    "dry_run": dry_run,
                    "created_at": now,
                    "updated_at": now,
                    "sent_at": None,
                }
            )
        except DuplicateKeyError:
            return None
        return await self.collection.find_one({"_id": result.inserted_id})

    async def claim(self, message_id: str, *, attempt: int) -> Document | None:
        """Move a row to `sending`, or refuse if it is already finished.

        Returns None when the row is terminal, which is what makes an ARQ
        redelivery safe: the check reads the authoritative store keyed on the
        same Message-ID the send would carry.
        """
        return await self.collection.find_one_and_update(
            {"message_id": message_id, "status": {"$nin": list(TERMINAL_STATUSES)}},
            {
                "$set": {
                    "status": "sending",
                    "attempts": attempt,
                    "updated_at": utcnow(),
                }
            },
            return_document=True,
        )

    async def mark_sent(self, message_id: str, *, response: str = "") -> None:
        now = utcnow()
        await self.collection.update_one(
            {"message_id": message_id},
            {
                "$set": {
                    "status": "sent",
                    "error": None,
                    "smtp_code": None,
                    "retryable": None,
                    "response": response[:MAX_ERROR_LENGTH],
                    "sent_at": now,
                    "updated_at": now,
                }
            },
        )

    async def mark_failed(
        self,
        message_id: str,
        *,
        error: str,
        smtp_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        """Record a failure. `error` must already be redacted by the caller.

        `retryable` is stored rather than merely acted on, so a wrong
        classification is auditable after the fact instead of being invisible.
        """
        await self.collection.update_one(
            {"message_id": message_id},
            {
                "$set": {
                    "status": "failed",
                    "error": error[:MAX_ERROR_LENGTH],
                    "smtp_code": smtp_code,
                    "retryable": retryable,
                    "updated_at": utcnow(),
                }
            },
        )

    async def mark_terminal(self, message_id: str, status: str, *, error: str = "") -> None:
        """For `dry_run` and `skipped`, which never reached a mail server."""
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"not a terminal status: {status}")
        await self.collection.update_one(
            {"message_id": message_id},
            {
                "$set": {
                    "status": status,
                    "error": (error or None) and error[:MAX_ERROR_LENGTH],
                    "updated_at": utcnow(),
                }
            },
        )

    async def by_message_id(self, message_id: str) -> Document | None:
        return await self.collection.find_one({"message_id": message_id})

    async def recent(self, limit: int = 5) -> list[Document]:
        """The newest rows, for the Settings card."""
        cursor = self.collection.find({}).sort("created_at", DESCENDING).limit(limit)
        return [row async for row in cursor]
