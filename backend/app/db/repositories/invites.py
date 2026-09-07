"""Invitations to create an account.

**Only the token's SHA-256 is stored, never the token.** Worth contrasting with
the Meta webhook verify token, which `api/settings_api.py` encrypts rather than
hashes -- and says why: the operator has to read it back to paste it into Meta's
dashboard, so a write-only field they cannot verify turns every handshake
mismatch into a regenerate-and-resubscribe cycle. An invite token has no such
requirement. Once the email is queued nobody ever needs to read it again, so the
strictly stronger option is available and taking it is not optional. That
matters concretely: `docs/security.md` records that MongoDB has no
authentication, so a database dump must not yield usable invite links.

SHA-256 rather than Argon2, for two reasons that point the same way. The token
is 256 bits of CSPRNG output, so there is no low-entropy guessing to slow down
and no salt to need. And an Argon2 hash cannot be indexed, which would turn
redemption into a scan that verifies every pending invite in turn.

`expired` is **not** a stored status. It is derived from
`status == "pending" and expires_at <= now`, because a stored one would need a
sweeper to write it and, until the sweeper ran, the database would be lying
about its own state. Deriving leaves exactly one authority: the clock.
"""

import hashlib
import secrets
from datetime import datetime
from typing import Any

from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from .base import BaseRepository, Document, utcnow

STATUSES = ("pending", "accepted", "revoked")

# 32 bytes / 256 bits, 43 URL-safe characters.
#
# Not `services/links.py`'s TOKEN_BYTES = 8: a click token protects a click
# count and has to fit inside a direct message, whereas this authorises
# **account creation**. 64 bits is online-guessable at scale if the throttle
# ever stops helping -- and `Throttle.hit` returns (True, 0) on any Redis error,
# by design -- so the entropy has to stand on its own. 32 bytes matches the one
# existing precedent for a standalone security token, the Meta verify token.
TOKEN_BYTES = 32


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 hex. Unconditional: callers must not pre-screen the input.

    Hashing whatever arrived, however malformed, and then always performing the
    lookup keeps the work constant. (Login's `_DUMMY_HASH` exists for the
    opposite reason: there the *Argon2* cost dominates and must not be skipped.
    Here SHA-256 is microseconds and the query dominates, so the query is the
    part that must always happen.)
    """
    return hashlib.sha256(token.encode()).hexdigest()


class InvitesRepository(BaseRepository[Any]):
    collection_name = "invites"

    async def create(
        self,
        *,
        email: str,
        token_hash: str,
        role_id: Any,
        invited_by: Any,
        expires_at: datetime,
        source: str = "manual",
        contact_id: Any = None,
        workspace_user_id: Any = None,
    ) -> Document | None:
        """Open a pending invite, or return None if the address already has one.

        None comes from `uq_invite_pending_email`, caught rather than pre-empted
        by a read: the index is the authority (§25, §63).
        """
        now = utcnow()
        document: Document = {
            "email": email.strip().lower(),
            "token_hash": token_hash,
            "status": "pending",
            "expires_at": expires_at,
            "role_id": role_id,
            "invited_by": invited_by,
            "source": source,
            "contact_id": contact_id,
            "workspace_user_id": workspace_user_id,
            "sent_count": 1,
            "last_sent_at": now,
            "last_send_status": None,
            "last_send_error": None,
            "accepted_at": None,
            "accepted_user_id": None,
            "revoked_at": None,
            "revoked_by": None,
            "created_at": now,
            "updated_at": now,
        }
        try:
            document["_id"] = await self.insert(document)
        except DuplicateKeyError:
            return None
        return document

    async def pending_for_email(self, email: str) -> Document | None:
        return await self.collection.find_one({"email": email.strip().lower(), "status": "pending"})

    async def redeem(self, token_hash: str, *, now: datetime) -> Document | None:
        """Claim this invite. None if unknown, spent, revoked or expired.

        The status flip **is** the reservation: a second concurrent caller
        matches nothing, because `status` is no longer "pending" by the time it
        queries. Same discipline as `ExecutionsRepository.reserve`, and for the
        same reason -- MongoDB decides, not a prior existence check.

        `accepted_user_id` is stamped separately by `attach_user` once the user
        row exists, which makes "accepted but no user" precisely the state that
        `release` is allowed to undo.
        """
        return await self.collection.find_one_and_update(
            {"token_hash": token_hash, "status": "pending", "expires_at": {"$gt": now}},
            {"$set": {"status": "accepted", "accepted_at": now, "updated_at": now}},
            return_document=True,
        )

    async def find_by_token(self, token_hash: str) -> Document | None:
        """For the lookup endpoint, which must distinguish spent from unknown."""
        return await self.collection.find_one({"token_hash": token_hash})

    async def attach_user(self, invite_id: Any, user_id: Any) -> None:
        await self.collection.update_one(
            {"_id": invite_id},
            {"$set": {"accepted_user_id": user_id, "updated_at": utcnow()}},
        )

    async def release(self, invite_id: Any) -> bool:
        """Undo a reservation whose user creation failed.

        Guarded on `accepted_user_id` being absent, which is the only thing that
        makes having this method safe at all: a *successful* signup can never be
        released, however it is called.
        """
        result = await self.collection.update_one(
            {"_id": invite_id, "status": "accepted", "accepted_user_id": None},
            {
                "$set": {
                    "status": "pending",
                    "accepted_at": None,
                    "updated_at": utcnow(),
                }
            },
        )
        return result.modified_count == 1

    async def rotate_token(
        self, invite_id: Any, *, token_hash: str, expires_at: datetime
    ) -> Document | None:
        """Re-issue a pending invite with a new token and deadline.

        Rotating rather than re-sending the same token means there is exactly
        one live link per invite at any moment, so a forwarded first email stops
        working the instant the operator resends. Matched on `status: pending`,
        so a resend cannot revive an accepted or revoked invite.
        """
        return await self.collection.find_one_and_update(
            {"_id": invite_id, "status": "pending"},
            {
                "$set": {
                    "token_hash": token_hash,
                    "expires_at": expires_at,
                    "last_sent_at": utcnow(),
                    "updated_at": utcnow(),
                },
                "$inc": {"sent_count": 1},
            },
            return_document=True,
        )

    async def revoke(self, invite_id: Any, *, revoked_by: Any) -> Document | None:
        """Withdraw a pending invite. The row is kept, for the audit trail.

        Does nothing to an account already created from this invite -- see the
        route's docstring, because that is what an operator will assume it does.
        """
        now = utcnow()
        return await self.collection.find_one_and_update(
            {"_id": invite_id, "status": "pending"},
            {
                "$set": {
                    "status": "revoked",
                    "revoked_at": now,
                    "revoked_by": revoked_by,
                    "updated_at": now,
                }
            },
            return_document=True,
        )

    async def record_send(self, invite_id: Any, *, status: str, error: str | None = None) -> None:
        """Stamp the outcome of the mail attempt, so a bounce is visible."""
        await self.collection.update_one(
            {"_id": invite_id},
            {
                "$set": {
                    "last_send_status": status,
                    "last_send_error": error,
                    "updated_at": utcnow(),
                }
            },
        )

    async def list_for_admin(
        self, *, status: str | None = None, now: datetime | None = None, limit: int = 100
    ) -> list[Document]:
        """Newest first. `status="expired"` is translated, not stored."""
        query: Document = {}
        if status == "expired":
            query = {"status": "pending", "expires_at": {"$lte": now or utcnow()}}
        elif status == "pending":
            # "Pending" in the UI means still usable, so an expired row must not
            # appear under both filters.
            query = {"status": "pending", "expires_at": {"$gt": now or utcnow()}}
        elif status:
            query = {"status": status}

        cursor = self.collection.find(query).sort("created_at", DESCENDING).limit(limit)
        return [row async for row in cursor]

    async def emails_with_live_invites(self, emails: list[str]) -> set[str]:
        """Which of these addresses already hold a usable invite.

        For decorating a contact list. Not used to decide whether to create one
        -- that stays the unique index's job.
        """
        if not emails:
            return set()
        cursor = self.collection.find(
            {
                "email": {"$in": [e.strip().lower() for e in emails]},
                "status": "pending",
                "expires_at": {"$gt": utcnow()},
            },
            {"email": 1},
        )
        return {str(row["email"]) async for row in cursor}
