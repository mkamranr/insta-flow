"""Roles: seeding, editing, and the rules about what may not be changed."""

from app.db.bootstrap import prepare
from app.db.repositories import RolesRepository
from app.permissions import ALL, MODULES, SYSTEM_ROLES


async def test_seeding_is_idempotent(database):
    """`prepare` runs on every boot; a second run must not double the roles."""
    before = await database["roles"].count_documents({})
    await prepare(database)
    assert await database["roles"].count_documents({}) == before == len(SYSTEM_ROLES)


async def test_owner_holds_the_whole_catalogue(database):
    """True by construction today. This test is what keeps it true the day
    somebody replaces `ALL` with a hand-written list."""
    owner = await RolesRepository(database).get_by_slug("owner")
    assert set(owner["permissions"]) == ALL


async def test_owner_is_resynced_when_the_catalogue_grows(database):
    """A release that adds a permission must reach Owner with no migration."""
    roles = RolesRepository(database)
    owner = await roles.get_by_slug("owner")
    await database["roles"].update_one(
        {"_id": owner["_id"]}, {"$set": {"permissions": ["analytics.view"]}}
    )

    await prepare(database)

    restored = await roles.get_by_slug("owner")
    assert set(restored["permissions"]) == ALL


async def test_an_unlocked_seed_roles_edits_survive_a_restart(database):
    """Manager and Viewer are a starting point, not the seeder's property. If a
    restart clobbered them, editing them would be a trap."""
    roles = RolesRepository(database)
    manager = await roles.get_by_slug("manager")
    await database["roles"].update_one(
        {"_id": manager["_id"]}, {"$set": {"permissions": ["analytics.view"]}}
    )

    await prepare(database)

    assert (await roles.get_by_slug("manager"))["permissions"] == ["analytics.view"]


async def test_every_seeded_permission_is_in_the_catalogue(database):
    """Catches a typo in SYSTEM_ROLES, which would otherwise grant nothing and
    look like a permissions bug in the UI."""
    for role in await RolesRepository(database).list_all():
        assert set(role["permissions"]) <= ALL, role["slug"]


def test_the_grid_never_claims_an_action_a_module_lacks():
    from app.permissions import for_grid

    grid = for_grid()
    by_key = {m.key: m for m in MODULES}
    for module in grid["modules"]:
        for action in module["actions"]:
            assert action["key"] in by_key[module["key"]].actions
            assert action["permission"] == f"{module['key']}.{action['key']}"


async def test_the_catalogue_endpoint_feeds_the_grid(signed_in):
    body = (await signed_in.get("/api/permissions")).json()
    assert [a["key"] for a in body["actions"]] == ["view", "create", "edit", "delete", "manage"]
    content = next(m for m in body["modules"] if m["key"] == "content")
    # The label override: "Edit" on posts and stories means "pull fresh ones".
    assert next(a for a in content["actions"] if a["key"] == "edit")["label"] == "Sync"


async def test_a_role_can_be_created_and_listed(signed_in):
    created = await signed_in.post(
        "/api/roles",
        json={"name": "Analyst", "description": "Read only", "permissions": ["analytics.view"]},
    )
    assert created.status_code == 201
    assert created.json()["permissions"] == ["analytics.view"]
    assert created.json()["is_system"] is False

    listed = (await signed_in.get("/api/roles")).json()
    assert "Analyst" in [role["name"] for role in listed]


async def test_an_unknown_permission_is_refused(signed_in):
    response = await signed_in.post(
        "/api/roles", json={"name": "Bogus", "permissions": ["automations.veiw"]}
    )
    assert response.status_code == 422


async def test_a_built_in_role_cannot_be_edited_or_deleted(signed_in, database):
    owner = await RolesRepository(database).get_by_slug("owner")
    patched = await signed_in.patch(f"/api/roles/{owner['_id']}", json={"name": "Boss"})
    assert patched.status_code == 409

    deleted = await signed_in.delete(f"/api/roles/{owner['_id']}")
    assert deleted.status_code == 409
    assert await database["roles"].count_documents({"slug": "owner"}) == 1


async def test_a_role_still_assigned_cannot_be_deleted(signed_in, database, user_factory):
    from bson import ObjectId

    role = (
        await signed_in.post(
            "/api/roles", json={"name": "Temp", "permissions": ["analytics.view"]}
        )
    ).json()
    role_id = ObjectId(role["id"])
    await user_factory("held@example.com", {"_id": role_id})

    response = await signed_in.delete(f"/api/roles/{role['id']}")
    assert response.status_code == 409
    # The count is in the message so the admin knows how much work the fix is.
    assert "1 person" in response.json()["error"]["message"]
    assert await database["roles"].count_documents({"_id": role_id}) == 1


async def test_the_user_count_is_reported(signed_in, user_factory):
    role = (
        await signed_in.post(
            "/api/roles", json={"name": "Counted", "permissions": ["analytics.view"]}
        )
    ).json()
    assert role["user_count"] == 0

    from bson import ObjectId

    await user_factory("counted@example.com", {"_id": ObjectId(role["id"])})
    fetched = (await signed_in.get(f"/api/roles/{role['id']}")).json()
    assert fetched["user_count"] == 1


async def test_you_cannot_grant_access_you_do_not_have(api, database, role_factory, user_factory):
    """Otherwise `roles.manage` alone is a path to every other permission."""
    from tests.conftest import TEST_PASSWORD

    limited = await role_factory("Role editor", ["roles.view", "roles.manage", "analytics.view"])
    await user_factory("limited@example.com", limited)
    await api.post(
        "/api/auth/login", json={"email": "limited@example.com", "password": TEST_PASSWORD}
    )

    allowed = await api.post(
        "/api/roles", json={"name": "Fine", "permissions": ["analytics.view"]}
    )
    assert allowed.status_code == 201

    refused = await api.post(
        "/api/roles", json={"name": "Escalation", "permissions": ["users.manage"]}
    )
    assert refused.status_code == 403
    assert await database["roles"].count_documents({"name": "Escalation"}) == 0
