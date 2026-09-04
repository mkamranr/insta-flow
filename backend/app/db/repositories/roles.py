"""Roles: a named set of permissions, assigned to users."""

import logging
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.permissions import OWNER_SLUG, SYSTEM_ROLES

from .base import BaseRepository, Document, utcnow

logger = logging.getLogger(__name__)


class RolesRepository(BaseRepository[Any]):
    collection_name = "roles"

    async def get_by_slug(self, slug: str) -> Document | None:
        return await self.collection.find_one({"slug": slug})

    async def get_for_user(self, user: Document) -> Document | None:
        """The role a user document points at, or None if it has none.

        None covers both "never assigned" and "assigned a role that has since been
        deleted". Both must fail closed -- see `current_grant` in `api/deps.py`.
        """
        role_id = user.get("role_id")
        if role_id is None:
            return None
        return await self.get(role_id)

    async def list_all(self) -> list[Document]:
        return await self.list({}, sort_field="name", descending=False, limit=200)

    async def create(self, *, name: str, description: str, permissions: list[str]) -> Document:
        now = utcnow()
        document = {
            "slug": _slugify(name),
            "name": name.strip(),
            "description": description.strip(),
            "permissions": sorted(set(permissions)),
            # Only the seeder mints locked roles. Anything created through the API
            # is the operator's to change or remove.
            "is_system": False,
            "created_at": now,
            "updated_at": now,
        }
        document["_id"] = await self.insert(document)
        return document

    async def ensure_owner(self) -> Any:
        """The Owner role's id, seeding the built-ins first if none exist yet."""
        existing = await self.get_by_slug(OWNER_SLUG)
        if existing is not None:
            return existing["_id"]
        return (await self.ensure_system_roles())[OWNER_SLUG]

    async def ensure_system_roles(self) -> dict[str, Any]:
        """Seed the built-in roles. Idempotent, run on every startup.

        A **locked** role's permissions are owned by this seeder, not by the
        database: a release that adds a permission must reach Owner without an
        operator doing anything. That is only safe because a locked role cannot be
        edited through the API -- if it could, this would silently undo their
        change on the next restart and nothing would say so.

        An **unlocked** role is inserted once as a starting point and then never
        touched again, so edits to Manager or Viewer survive every restart. That
        split is why there is no reconciliation ambiguity: a role is either always
        the seeder's or always the operator's, never both.
        """
        ids: dict[str, Any] = {}
        for role in SYSTEM_ROLES:
            fields = {
                "name": role.name,
                "description": role.description,
                "is_system": role.locked,
                "updated_at": utcnow(),
            }
            if role.locked:
                update: dict[str, Any] = {
                    "$set": {**fields, "permissions": sorted(role.permissions)},
                    "$setOnInsert": {"slug": role.slug, "created_at": utcnow()},
                }
            else:
                # Everything on insert only, so an operator's edits are never
                # clobbered by a restart.
                update = {
                    "$setOnInsert": {
                        **fields,
                        "slug": role.slug,
                        "permissions": sorted(role.permissions),
                        "created_at": utcnow(),
                    }
                }

            saved: Document | None
            try:
                saved = await self.collection.find_one_and_update(
                    {"slug": role.slug}, update, upsert=True, return_document=True
                )
            except DuplicateKeyError:
                # A second process won the race between this upsert's read and its
                # insert. `uq_role_slug` is the authority, not a prior existence
                # check -- re-read rather than retrying the upsert.
                saved = await self.get_by_slug(role.slug)
            if saved is None:  # pragma: no cover - the index guarantees one of the two
                raise RuntimeError(f"could not seed the {role.slug} role")
            ids[role.slug] = saved["_id"]

        return ids


def _slugify(name: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in name.lower())
    return "-".join(part for part in cleaned.split("-") if part) or "role"
