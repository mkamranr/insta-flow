"""One admin must never see or control another's Instagram account.

Single-operator is the intended deployment, but the schema allows more than one
user and `make bootstrap-admin` can create them. Scoping is what stops that
being a trap.
"""

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.config import get_settings
from app.db.repositories import RolesRepository, UsersRepository
from app.services.security.encryption import encrypt
from app.services.security.passwords import hash_password
from tests.conftest import TEST_PASSWORD


@pytest.fixture
async def two_admins(database, admin):
    """`admin` owns an Instagram account; a second admin owns nothing."""
    settings = get_settings()
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "user_id": admin["_id"],
            "instagram_user_id": "17841400000000000",
            "username": "first_admins_account",
            "status": "connected",
            "access_token_encrypted": encrypt("secret-token", settings.encryption_key),
            "token_expires_at": datetime.now(UTC) + timedelta(days=59),
        }
    )
    users = UsersRepository(database)
    other = await users.create(
        "other@example.com",
        hash_password(TEST_PASSWORD),
        role_id=await RolesRepository(database).ensure_owner(),
    )
    return {"account_id": account_id, "other": other}


async def sign_in_as(api, email):
    response = await api.post("/api/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert response.status_code == 200


async def test_the_owner_sees_their_account(api, two_admins, admin):
    await sign_in_as(api, admin["email"])
    accounts = (await api.get("/api/instagram/accounts")).json()
    assert [a["username"] for a in accounts] == ["first_admins_account"]


async def test_another_admin_sees_nothing(api, two_admins):
    """Before scoping, this returned the first admin's account."""
    await sign_in_as(api, "other@example.com")
    assert (await api.get("/api/instagram/accounts")).json() == []


async def test_another_admin_cannot_fetch_it_by_id(api, two_admins):
    await sign_in_as(api, "other@example.com")
    response = await api.get(f"/api/instagram/{two_admins['account_id']}")
    # 404 rather than 403: the response must not reveal that the id exists.
    assert response.status_code == 404


async def test_another_admin_cannot_disconnect_it(api, database, two_admins):
    await sign_in_as(api, "other@example.com")
    response = await api.delete(f"/api/instagram/{two_admins['account_id']}")
    assert response.status_code == 404
    assert await database["instagram_accounts"].count_documents({}) == 1


async def test_another_admin_cannot_subscribe_its_webhooks(api, two_admins):
    await sign_in_as(api, "other@example.com")
    response = await api.post(f"/api/instagram/{two_admins['account_id']}/subscribe")
    assert response.status_code == 404


async def test_another_admin_cannot_refresh_its_token(api, two_admins):
    await sign_in_as(api, "other@example.com")
    response = await api.post(f"/api/instagram/{two_admins['account_id']}/refresh")
    assert response.status_code == 404


async def test_another_admin_gets_no_posts_or_rules(api, two_admins):
    """These call the account lookup, which previously took the first row."""
    await sign_in_as(api, "other@example.com")
    assert (await api.get("/api/posts")).status_code == 422
    assert (await api.get("/api/rules")).status_code == 422


async def test_another_admin_sees_no_analytics(api, two_admins):
    await sign_in_as(api, "other@example.com")
    assert (await api.get("/api/analytics/summary")).json() == {"connected": False}
    assert (await api.get("/api/analytics/activity")).json() == []
    assert (await api.get("/api/analytics/contacts")).json() == []


async def test_another_admin_exports_no_contacts(api, two_admins, database, admin):
    """Naming the first admin's post explicitly must not reach across either.

    The media batch is account-scoped for exactly this: the media id arrives from
    the caller, so an unscoped `$in` would let anyone export anyone's leads by
    guessing an id.
    """
    account = await database["instagram_accounts"].find_one({"user_id": admin["_id"]})
    await database["media"].insert_one(
        {
            "instagram_account_id": account["_id"],
            "instagram_media_id": "17890000000000001",
            "caption": "First admin's post",
        }
    )
    await database["contacts"].insert_one(
        {
            "instagram_account_id": account["_id"],
            "instagram_user_id": "u-1",
            "email": "lead@example.com",
            "created_at": datetime.now(UTC),
            "sources": [
                {
                    "instagram_media_id": "17890000000000001",
                    "rule_id": ObjectId(),
                    "rule_name": "Gate",
                    "captured_at": datetime.now(UTC),
                    "backfilled": False,
                }
            ],
        }
    )
    header = "email,instagram_user_id,captured_at,post_caption,post_media_id,automation"

    await sign_in_as(api, "other@example.com")

    for url in (
        "/api/analytics/contacts.csv",
        "/api/analytics/contacts.csv?post=17890000000000001",
    ):
        body = (await api.get(url)).content.decode("utf-8-sig")
        assert body.strip().split("\r\n") == [header], url
        assert "lead@example.com" not in body


async def test_no_token_reaches_either_admin(api, two_admins, admin):
    await sign_in_as(api, admin["email"])
    body = (await api.get("/api/instagram/accounts")).text
    assert "secret-token" not in body
    assert "access_token" not in body


@pytest.fixture
async def rule_of_the_first_admin(database, two_admins):
    """A rule owned by the first admin, with the second admin holding an account too.

    Giving the second admin their own connected account is what makes these tests
    mean anything. `two_admins` alone leaves them with none, so every request would
    stop at the "no Instagram account is connected yet" check (422) and never reach
    the ownership comparison -- a green test that proves nothing about scoping.
    """
    settings = get_settings()
    await database["instagram_accounts"].insert_one(
        {
            "_id": ObjectId(),
            "user_id": two_admins["other"]["_id"],
            "instagram_user_id": "17841499999999999",
            "username": "second_admins_account",
            "status": "connected",
            "access_token_encrypted": encrypt("other-token", settings.encryption_key),
            "token_expires_at": datetime.now(UTC) + timedelta(days=59),
        }
    )
    rule_id = ObjectId()
    await database["rules"].insert_one(
        {
            "_id": rule_id,
            "instagram_account_id": two_admins["account_id"],
            "name": "First admin's rule",
            "trigger": "comment",
            "scope": "any",
            "keywords": ["link"],
            "any_word": False,
            "message": "Here you go.",
            "is_active": True,
        }
    )
    return rule_id


async def test_another_admin_cannot_read_a_rule_by_id(api, rule_of_the_first_admin):
    await sign_in_as(api, "other@example.com")
    response = await api.get(f"/api/rules/{rule_of_the_first_admin}")
    # 404, not 403: the response must not reveal that the id exists.
    assert response.status_code == 404


async def test_another_admin_cannot_update_a_rule(api, database, rule_of_the_first_admin):
    await sign_in_as(api, "other@example.com")
    response = await api.patch(
        f"/api/rules/{rule_of_the_first_admin}", json={"message": "Hijacked."}
    )
    assert response.status_code == 404
    # The status code alone would pass if the write had already happened.
    stored = await database["rules"].find_one({"_id": rule_of_the_first_admin})
    assert stored["message"] == "Here you go."


async def test_another_admin_cannot_delete_a_rule(api, database, rule_of_the_first_admin):
    await sign_in_as(api, "other@example.com")
    response = await api.delete(f"/api/rules/{rule_of_the_first_admin}")
    assert response.status_code == 404
    assert await database["rules"].count_documents({"_id": rule_of_the_first_admin}) == 1


async def test_the_owner_can_still_read_update_and_delete(
    api, database, admin, rule_of_the_first_admin
):
    """The guard must not lock the rightful owner out -- the other half of the fix."""
    await sign_in_as(api, admin["email"])
    assert (await api.get(f"/api/rules/{rule_of_the_first_admin}")).status_code == 200

    updated = await api.patch(f"/api/rules/{rule_of_the_first_admin}", json={"message": "Mine."})
    assert updated.status_code == 200
    assert updated.json()["message"] == "Mine."

    assert (await api.delete(f"/api/rules/{rule_of_the_first_admin}")).status_code == 204
    assert await database["rules"].count_documents({"_id": rule_of_the_first_admin}) == 0
