"""Bringing a database up to date, in one place.

Four callers need the same three steps in the same order: the API lifespan,
`scripts/create_indexes.py`, `scripts/bootstrap_admin.py` and the test fixtures.
Duplicating the sequence is how one of them ends up a step behind.
"""

import logging
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.db.indexes import create_indexes
from app.db.repositories import InstagramAccountsRepository, RolesRepository, UsersRepository
from app.permissions import OWNER_SLUG

logger = logging.getLogger(__name__)


async def prepare(database: AsyncDatabase[dict[str, Any]]) -> dict[str, list[str]]:
    """Indexes, then system roles, then the role backfill.

    Returns what `create_indexes` returns, so the operational script can keep
    reporting on indexes without knowing anything about roles.

    Order matters in both places. `uq_role_slug` must exist before the seeder
    runs, or two processes starting together could each insert an Owner and
    nothing later would notice. The backfills run last: the role one needs the
    Owner role to exist before it can point anyone at it, and the slug one needs
    `uq_user_slug` in place so a collision fails loudly rather than duplicating a
    workspace URL.

    The account backfill is last and is ordered within itself: `created_at` has
    to land before anything sorts on it, because rows written before `upsert`
    stamped it carry no such field at all.
    """
    created = await create_indexes(database)
    role_ids = await RolesRepository(database).ensure_system_roles()
    users = UsersRepository(database)
    await users.backfill_missing_roles(role_ids[OWNER_SLUG])
    await users.backfill_missing_slugs()
    accounts = InstagramAccountsRepository(database)
    await accounts.backfill_created_at()
    await accounts.backfill_defaults()
    return created
