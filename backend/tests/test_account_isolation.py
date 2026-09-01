"""One admin must never see or control another's Instagram account.

Single-operator is the intended deployment, but the schema allows more than one
user and `make bootstrap-admin` can create them. Scoping is what stops that
being a trap.
"""

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.config import get_settings
from app.db.repositories import UsersRepository
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
    other = await UsersRepository(database).create(
        "other@example.com", hash_password(TEST_PASSWORD)
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


async def test_no_token_reaches_either_admin(api, two_admins, admin):
    await sign_in_as(api, admin["email"])
    body = (await api.get("/api/instagram/accounts")).text
    assert "secret-token" not in body
    assert "access_token" not in body
