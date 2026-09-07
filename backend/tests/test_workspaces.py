"""Workspaces: one user, one Instagram account, one `/u/<slug>/` prefix.

The isolation that `test_account_isolation.py` pins must survive this feature.
What changes is that `workspaces.view` can now lift it deliberately; what must
not change is that nothing else can.
"""

import pytest
from bson import ObjectId

from app.config import get_settings
from app.db.bootstrap import prepare
from app.db.repositories import RolesRepository, UsersRepository
from app.permissions import (
    MODULES,
    OWNER_ONLY_MODULES,
    SYSTEM_ROLES,
    WORKSPACE_OWNER_SLUG,
)
from app.services import slugs
from app.services.security.encryption import encrypt
from app.services.security.passwords import hash_password
from tests.conftest import TEST_PASSWORD


@pytest.fixture
async def other_workspace(database, admin):
    """A second user with their own connected account. `admin` owns nothing."""
    users = UsersRepository(database)
    other = await users.create(
        "zoe@example.com",
        hash_password(TEST_PASSWORD),
        role_id=await RolesRepository(database).ensure_owner(),
    )
    await database["instagram_accounts"].insert_one(
        {
            "_id": ObjectId(),
            "user_id": other["_id"],
            "instagram_user_id": "17841499999999999",
            "username": "zoes_shop",
            "status": "connected",
            "access_token_encrypted": encrypt("zoe-token", get_settings().encryption_key),
        }
    )
    return other


# --- slugs ------------------------------------------------------------------


async def test_every_new_user_gets_a_slug(database):
    users = UsersRepository(database)
    role_id = await RolesRepository(database).ensure_owner()
    user = await users.create("ameera.alshehhi@mod.gov.ae", "hash", role_id=role_id)
    assert user["slug"] == "ameera-alshehhi"
    assert slugs.is_valid(user["slug"])


async def test_a_colliding_local_part_is_suffixed_not_rejected(database):
    """Two people at different domains routinely share a local part."""
    users = UsersRepository(database)
    role_id = await RolesRepository(database).ensure_owner()
    first = await users.create("sales@one.com", "hash", role_id=role_id)
    second = await users.create("sales@two.com", "hash", role_id=role_id)
    assert first["slug"] == "sales"
    assert second["slug"] == "sales-2"


async def test_the_backfill_gives_pre_workspace_users_a_slug(database):
    """An upgraded deployment has users created before slugs existed."""
    users = UsersRepository(database)
    role_id = await RolesRepository(database).ensure_owner()
    await database["users"].insert_one(
        {"_id": ObjectId(), "email": "legacy@example.com", "role_id": role_id, "is_active": True}
    )

    assert await users.backfill_missing_slugs() == 1
    legacy = await users.get_by_email("legacy@example.com")
    assert legacy["slug"] == "legacy"
    # Idempotent: running it again must not re-slug anybody.
    assert await users.backfill_missing_slugs() == 0


async def test_a_user_two_releases_behind_gets_both_backfills(database):
    """The upgrade path a real server actually takes.

    A deployment that skipped the roles release has users with **neither**
    `role_id` nor `slug`. Every other test here covers one backfill or the other
    against a document already carrying the field the previous release added;
    this is the only one that runs the combination a two-releases-behind server
    will run, through `prepare()` -- the same function the API lifespan calls, so
    the ordering being checked is the real one:

        create_indexes -> ensure_system_roles -> backfill_missing_roles
                       -> backfill_missing_slugs

    Order matters twice over. `uq_user_slug` has to exist and be partial before
    any slug is written, and the Owner role has to exist before anyone can be
    pointed at it.
    """
    ancient = ObjectId()
    await database["users"].insert_one(
        {"_id": ancient, "email": "operator@example.com", "is_active": True, "session_epoch": 0}
    )

    await prepare(database)

    user = await UsersRepository(database).get(ancient)
    assert user["slug"] == "operator"
    owner = await RolesRepository(database).get_by_slug("owner")
    assert user["role_id"] == owner["_id"], "a pre-RBAC user must land on Owner, not nothing"

    # Idempotent: `prepare()` runs on every boot, not just the upgrade one.
    await prepare(database)
    again = await UsersRepository(database).get(ancient)
    assert again["slug"] == "operator"
    assert again["role_id"] == owner["_id"]


async def test_the_oldest_user_keeps_the_unsuffixed_slug(database):
    """On a single-operator install that is the user whose Meta app is live.

    Backfill order is by `_id`, which ascends with creation time, so the original
    operator does not get bumped to `-2` by someone who signed up later.
    """
    users = UsersRepository(database)
    role_id = await RolesRepository(database).ensure_owner()
    older, newer = ObjectId(), ObjectId()
    assert older < newer
    await database["users"].insert_many(
        [
            {"_id": newer, "email": "ops@second.com", "role_id": role_id, "is_active": True},
            {"_id": older, "email": "ops@first.com", "role_id": role_id, "is_active": True},
        ]
    )
    await users.backfill_missing_slugs()

    assert (await users.get(older))["slug"] == "ops"
    assert (await users.get(newer))["slug"] == "ops-2"


# --- reaching another workspace ---------------------------------------------


async def test_the_prefixed_route_serves_your_own_workspace(signed_in, admin, database):
    await database["instagram_accounts"].insert_one(
        {
            "_id": ObjectId(),
            "user_id": admin["_id"],
            "instagram_user_id": "17841400000000000",
            "username": "myshop",
            "status": "connected",
        }
    )
    response = await signed_in.get(f"/u/{admin['slug']}/api/instagram/accounts")
    assert response.status_code == 200
    assert [a["username"] for a in response.json()] == ["myshop"]


async def test_an_owner_can_open_another_workspace(signed_in, admin, other_workspace):
    """The whole point of the feature: `workspaces.view` reaches across."""
    response = await signed_in.get(f"/u/{other_workspace['slug']}/api/instagram/accounts")
    assert response.status_code == 200
    assert [a["username"] for a in response.json()] == ["zoes_shop"]


async def test_without_the_permission_another_workspace_is_a_404(
    api, database, role_factory, user_factory, other_workspace
):
    """404 and not 403: a 403 confirms the slug exists, which turns this into a
    way to enumerate who has an account on this instance."""
    role = await role_factory("Narrow", ["instagram.view", "automations.view"])
    await user_factory("narrow@example.com", role)
    await api.post(
        "/api/auth/login", json={"email": "narrow@example.com", "password": TEST_PASSWORD}
    )

    response = await api.get(f"/u/{other_workspace['slug']}/api/instagram/accounts")
    assert response.status_code == 404


async def test_an_unknown_slug_is_a_404_too(signed_in):
    assert (await signed_in.get("/u/nobody-here/api/instagram/accounts")).status_code == 404


async def test_rules_are_scoped_to_the_workspace_not_the_caller(
    signed_in, database, other_workspace
):
    """Analytics and rules read the workspace's account, so an admin switching in
    must see that workspace's rows -- and, crucially, its emptiness rather than
    their own data."""
    account = await database["instagram_accounts"].find_one({"user_id": other_workspace["_id"]})
    await database["rules"].insert_one(
        {
            "_id": ObjectId(),
            "instagram_account_id": account["_id"],
            "name": "Zoe's rule",
            "trigger": "comment",
            "keywords": ["link"],
            "enabled": True,
        }
    )
    response = await signed_in.get(f"/u/{other_workspace['slug']}/api/rules")
    assert response.status_code == 200
    assert [r["name"] for r in response.json()] == ["Zoe's rule"]

    # The admin's own workspace has no account at all, so the same call unprefixed
    # must refuse rather than borrow Zoe's. 422 is what ValidationError maps to.
    own = await signed_in.get("/api/rules")
    assert own.status_code == 422
    assert "No Instagram account" in own.json()["error"]["message"]


# --- the switcher's data ----------------------------------------------------


async def test_you_always_see_your_own_workspace(
    api, database, role_factory, user_factory, other_workspace
):
    role = await role_factory("Narrow", ["instagram.view"])
    user = await user_factory("narrow@example.com", role)
    await api.post(
        "/api/auth/login", json={"email": "narrow@example.com", "password": TEST_PASSWORD}
    )

    body = (await api.get("/api/workspaces")).json()
    assert [w["slug"] for w in body] == [user["slug"]]
    assert body[0]["is_own"] is True


async def test_the_permission_widens_the_list(signed_in, admin, other_workspace):
    body = (await signed_in.get("/api/workspaces")).json()
    slugs_seen = {w["slug"] for w in body}
    assert slugs_seen == {admin["slug"], other_workspace["slug"]}

    zoe = next(w for w in body if w["slug"] == other_workspace["slug"])
    assert zoe["username"] == "zoes_shop"
    assert zoe["connected"] is True
    assert zoe["is_own"] is False


# --- seeded roles -----------------------------------------------------------


def test_no_seeded_role_but_owner_reaches_other_workspaces():
    """Viewer is built by comprehension over MODULES, so a new module hands it a
    `.view` automatically. That is how a read-only role would silently acquire
    the run of every other person's workspace. `workspace-owner` is built by
    subtraction and has the same exposure from the other direction."""
    by_slug = {role.slug: role for role in SYSTEM_ROLES}
    assert "workspaces.view" in by_slug["owner"].permissions
    for slug in ("manager", "viewer", "workspace-owner"):
        assert "workspaces.view" not in by_slug[slug].permissions, slug


def test_owner_only_modules_reach_owner_and_nobody_else():
    """The guard for both halves at once, so a module added to
    `OWNER_ONLY_MODULES` cannot be half-applied -- excluded from Viewer's
    comprehension but left in Manager's subtraction, or the reverse."""
    by_slug = {role.slug: role for role in SYSTEM_ROLES}
    owner_only = {
        f"{m.key}.{a}" for m in MODULES for a in m.actions if m.key in OWNER_ONLY_MODULES
    }
    assert owner_only, "OWNER_ONLY_MODULES names no real module"
    assert owner_only <= by_slug["owner"].permissions
    assert not owner_only & by_slug["manager"].permissions
    assert not owner_only & by_slug["viewer"].permissions
    assert not owner_only & by_slug["workspace-owner"].permissions


# The role every invited person holds. Written out rather than derived, because
# deriving it here would just restate the implementation and pass forever.
WORKSPACE_OWNER_PERMISSIONS = frozenset(
    {
        "analytics.view",
        "automations.create",
        "automations.delete",
        "automations.edit",
        "automations.view",
        "contacts.view",
        "content.edit",
        "content.view",
        "instagram.manage",
        "instagram.view",
        "settings.view",
    }
)


def test_the_workspace_owner_role_is_exactly_these_permissions():
    """The most important assertion in this file, and the least obvious.

    `workspace-owner` is built by subtracting `_BEYOND_ONE_WORKSPACE` from ALL,
    **and** it is `locked=True`. Together those mean a module added in a later
    release lands in the role automatically and `ensure_system_roles` then
    `$set`s the new permission onto every existing tenant on the next boot --
    no migration, no symptom, nobody asked.

    A subset assertion cannot catch that, because the set only ever grows. Only
    a frozen literal can. If this test fails after adding a module, that is the
    guard working: decide deliberately whether tenants should have the new
    permission, then update this list.
    """
    by_slug = {role.slug: role for role in SYSTEM_ROLES}
    assert by_slug[WORKSPACE_OWNER_SLUG].permissions == WORKSPACE_OWNER_PERMISSIONS


def test_the_workspace_owner_role_is_locked():
    """Unlocked, it would be seeded once and then belong to the operator -- so
    an edit meant for one person would change what every future invitee gets,
    with nothing to catch it."""
    by_slug = {role.slug: role for role in SYSTEM_ROLES}
    assert by_slug[WORKSPACE_OWNER_SLUG].locked is True


def test_a_workspace_owner_cannot_see_who_else_is_on_the_install():
    """The specific reason Manager is not reused. `GET /api/users` returns every
    user's email, role and last sign-in, and invitees are strangers to one
    another."""
    by_slug = {role.slug: role for role in SYSTEM_ROLES}
    tenant = by_slug[WORKSPACE_OWNER_SLUG].permissions
    for permission in ("users.view", "users.manage", "roles.view", "roles.manage"):
        assert permission not in tenant, permission
    # Manager, by contrast, does hold them -- which is what made it unusable here.
    assert "users.view" in by_slug["manager"].permissions


def test_a_workspace_owner_cannot_touch_server_wide_settings():
    """`instance.*` is one value for the whole deployment: the public origin it
    holds re-points every workspace's Meta callbacks at once."""
    tenant = {role.slug: role for role in SYSTEM_ROLES}[WORKSPACE_OWNER_SLUG].permissions
    assert not {p for p in tenant if p.startswith("instance.")}
    # The disclosure line lives behind `instance.manage` for the same reason:
    # it is one line every workspace's worker reads.
    assert "settings.view" in tenant, "a tenant still needs to open the page"


def test_a_workspace_owner_can_configure_their_own_meta_app():
    """The entire point of the role. `instagram.manage` gates
    `PUT /api/settings/meta-app`, and `settings.view` gates the page it lives
    on -- without the second, the first is unreachable through the UI."""
    tenant = {role.slug: role for role in SYSTEM_ROLES}[WORKSPACE_OWNER_SLUG].permissions
    assert "instagram.manage" in tenant
    assert "settings.view" in tenant
