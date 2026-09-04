"""User administration, and the guardrails that stop it locking everyone out."""

from bson import ObjectId

from app.db.repositories import RolesRepository
from tests.conftest import TEST_PASSWORD

NEW_PASSWORD = "a-different-correct-horse"


async def _role_id(database, slug: str) -> ObjectId:
    role = await RolesRepository(database).get_by_slug(slug)
    return role["_id"]


async def test_a_user_can_be_created_and_listed(signed_in, database):
    viewer = await _role_id(database, "viewer")
    created = await signed_in.post(
        "/api/users",
        json={"email": "new@example.com", "password": NEW_PASSWORD, "role_id": str(viewer)},
    )
    assert created.status_code == 201
    assert created.json()["role"]["slug"] == "viewer"
    assert created.json()["is_active"] is True

    listed = (await signed_in.get("/api/users")).json()
    assert "new@example.com" in [row["email"] for row in listed]


async def test_the_user_list_never_carries_a_password_hash(signed_in, database):
    """`serialise()` would copy every key. This is why the list is built from
    `UserPublic.from_document` instead."""
    await signed_in.post(
        "/api/users",
        json={
            "email": "secret@example.com",
            "password": NEW_PASSWORD,
            "role_id": str(await _role_id(database, "viewer")),
        },
    )
    body = (await signed_in.get("/api/users")).text
    assert "password_hash" not in body
    assert "session_epoch" not in body
    assert "$argon2" not in body


async def test_the_user_list_does_not_broadcast_everyone_s_permissions(signed_in, database):
    """A role summary, never its permission list -- otherwise `users.view` reveals
    the full capability set of every account."""
    body = (await signed_in.get("/api/users")).json()
    assert body[0]["role"]["slug"] == "owner"
    assert "permissions" not in body[0]["role"]
    assert "permissions" not in body[0]


async def test_me_carries_the_role_and_permissions(signed_in):
    body = (await signed_in.get("/api/auth/me")).json()
    assert body["role"]["slug"] == "owner"
    assert "analytics.view" in body["permissions"]
    assert "password_hash" not in body
    assert "session_epoch" not in body


async def test_a_duplicate_email_is_refused(signed_in, database, admin):
    response = await signed_in.post(
        "/api/users",
        json={
            "email": admin["email"],
            "password": NEW_PASSWORD,
            "role_id": str(await _role_id(database, "viewer")),
        },
    )
    assert response.status_code == 409


async def test_you_cannot_change_your_own_role(signed_in, database, admin):
    response = await signed_in.patch(
        f"/api/users/{admin['_id']}", json={"role_id": str(await _role_id(database, "viewer"))}
    )
    assert response.status_code == 422
    still = await database["users"].find_one({"_id": admin["_id"]})
    assert still["role_id"] == await _role_id(database, "owner")


async def test_you_cannot_deactivate_or_delete_yourself(signed_in, database, admin):
    assert (
        await signed_in.patch(f"/api/users/{admin['_id']}", json={"is_active": False})
    ).status_code == 422
    assert (await signed_in.delete(f"/api/users/{admin['_id']}")).status_code == 422
    assert await database["users"].count_documents({"_id": admin["_id"]}) == 1


async def test_the_last_owner_cannot_be_removed(
    other_client, database, role_factory, user_factory, admin
):
    """The actor here holds a *custom* role carrying every permission, which is
    the only way to reach this guard: they can manage an owner (their permissions
    are a superset), but they do not themselves hold the `owner` role, so the
    admin really is the last one.
    """
    from app.permissions import ALL

    superuser = await role_factory("Superuser", sorted(ALL))
    await user_factory("super@example.com", superuser)
    await other_client.post(
        "/api/auth/login", json={"email": "super@example.com", "password": TEST_PASSWORD}
    )

    deactivated = await other_client.patch(
        f"/api/users/{admin['_id']}", json={"is_active": False}
    )
    assert deactivated.status_code == 409
    assert "last active owner" in deactivated.json()["error"]["message"]

    deleted = await other_client.delete(f"/api/users/{admin['_id']}")
    assert deleted.status_code == 409
    assert await database["users"].count_documents({"_id": admin["_id"]}) == 1

    demoted = await other_client.patch(
        f"/api/users/{admin['_id']}", json={"role_id": str(await _role_id(database, "viewer"))}
    )
    assert demoted.status_code == 409


async def test_deactivating_a_user_stops_them_signing_in(api, signed_in, database, user_factory):
    victim = await user_factory("victim@example.com", {"_id": await _role_id(database, "viewer")})
    assert (
        await signed_in.patch(f"/api/users/{victim['_id']}", json={"is_active": False})
    ).status_code == 200

    refused = await api.post(
        "/api/auth/login", json={"email": "victim@example.com", "password": TEST_PASSWORD}
    )
    assert refused.status_code == 401


async def test_an_admin_reset_invalidates_the_target_s_open_session(
    other_client, signed_in, database, user_factory
):
    """The point of a reset is that the old password stops working everywhere.

    Two clients, because `signed_in` and `api` are the same object and would
    otherwise share -- and overwrite -- one cookie jar.
    """
    victim = await user_factory("open@example.com", {"_id": await _role_id(database, "viewer")})
    await other_client.post(
        "/api/auth/login", json={"email": "open@example.com", "password": TEST_PASSWORD}
    )
    assert (await other_client.get("/api/auth/me")).status_code == 200

    reset = await signed_in.post(
        f"/api/users/{victim['_id']}/password", json={"password": NEW_PASSWORD}
    )
    assert reset.status_code == 204

    # Same cookie, now revoked by the epoch bump.
    assert (await other_client.get("/api/auth/me")).status_code == 401


async def test_changing_your_own_password_keeps_you_signed_in(api, database, admin):
    await api.post(
        "/api/auth/login", json={"email": admin["email"], "password": TEST_PASSWORD}
    )

    wrong = await api.post(
        "/api/auth/password",
        json={"current_password": "not-it-at-all", "password": NEW_PASSWORD},
    )
    assert wrong.status_code == 401

    changed = await api.post(
        "/api/auth/password",
        json={"current_password": TEST_PASSWORD, "password": NEW_PASSWORD},
    )
    assert changed.status_code == 204
    # The tab you are standing in keeps working; the cookie was re-issued.
    assert (await api.get("/api/auth/me")).status_code == 200


async def test_you_cannot_administer_someone_with_more_access(
    api, database, role_factory, user_factory, admin
):
    """The horizontal escalation an otherwise correct grid leaves open: a role
    with `users.manage` but not `roles.manage` must not be able to reset the
    Owner's password and take the deployment."""
    staff = await role_factory("Staff admin", ["users.view", "users.manage"])
    await user_factory("staff@example.com", staff)
    await api.post(
        "/api/auth/login", json={"email": "staff@example.com", "password": TEST_PASSWORD}
    )

    hijack = await api.post(
        f"/api/users/{admin['_id']}/password", json={"password": NEW_PASSWORD}
    )
    assert hijack.status_code == 403

    assert (await api.delete(f"/api/users/{admin['_id']}")).status_code == 403
    assert await database["users"].count_documents({"_id": admin["_id"]}) == 1

    # The owner's password is untouched: they can still sign in.
    assert (
        await api.post(
            "/api/auth/login", json={"email": admin["email"], "password": TEST_PASSWORD}
        )
    ).status_code == 200


async def test_you_cannot_assign_a_role_stronger_than_your_own(
    api, database, role_factory, user_factory
):
    staff = await role_factory("Staff admin 2", ["users.view", "users.manage"])
    await user_factory("staff2@example.com", staff)
    await api.post(
        "/api/auth/login", json={"email": "staff2@example.com", "password": TEST_PASSWORD}
    )

    response = await api.post(
        "/api/users",
        json={
            "email": "puppet@example.com",
            "password": NEW_PASSWORD,
            "role_id": str(await _role_id(database, "owner")),
        },
    )
    assert response.status_code == 403
    assert await database["users"].count_documents({"email": "puppet@example.com"}) == 0
