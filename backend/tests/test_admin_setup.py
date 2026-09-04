"""First-run admin setup through the browser.

This endpoint creates an account and is reachable by anyone, so the tests that
matter are the ones proving it *refuses*. Each fence is tested on its own,
because any one of them failing quietly turns the login page into an open
admin-account factory.
"""

import pytest

from app.db.indexes import create_indexes
from app.db.repositories import RolesRepository, UsersRepository
from app.services.security.passwords import hash_password

SECRET = "s" * 32
GOOD = {
    "secret": SECRET,
    "email": "admin@example.com",
    "password": "correct horse battery staple",
    "password_confirm": "correct horse battery staple",
}


@pytest.fixture(autouse=True)
def setup_secret_configured(settings):
    """Configure a usable setup secret for the app the tests drive.

    Overriding the dependency rather than the environment: real settings are
    cached and shared, and a leaked value would silently arm this endpoint for
    every other test in the run.
    """
    from app.config import get_settings
    from app.main import app

    patched = settings.model_copy(update={"admin_setup_secret": SECRET})
    app.dependency_overrides[get_settings] = lambda: patched
    yield patched
    app.dependency_overrides.pop(get_settings, None)


# -- the happy path ------------------------------------------------------------


async def test_the_first_admin_can_be_created_and_is_signed_in(api, database, settings):
    response = await api.post("/api/auth/setup", json=GOOD)

    assert response.status_code == 201
    assert response.json()["email"] == "admin@example.com"
    # Signed in immediately: the operator should not have to type the password
    # again the moment after choosing it.
    assert settings.session_cookie_name in response.cookies
    assert await database["users"].count_documents({}) == 1


async def test_the_password_is_stored_only_as_a_hash(api, database):
    await api.post("/api/auth/setup", json=GOOD)

    user = await database["users"].find_one({})
    assert "password" not in user
    assert GOOD["password"] not in str(user)
    assert user["password_hash"].startswith("$argon2")


async def test_setup_is_required_only_while_there_are_no_users(api, database):
    assert (await api.get("/api/auth/setup")).json()["required"] is True

    await api.post("/api/auth/setup", json=GOOD)

    assert (await api.get("/api/auth/setup")).json()["required"] is False


# -- the fences ----------------------------------------------------------------


async def test_a_wrong_secret_is_refused(api, database):
    response = await api.post("/api/auth/setup", json={**GOOD, "secret": "wrong" * 8})

    assert response.status_code == 401
    assert await database["users"].count_documents({}) == 0


async def test_setup_is_refused_once_any_user_exists(api, database):
    """Not "any admin" -- any user. Otherwise a leaked secret is a permanent
    backdoor into a running deployment."""
    await UsersRepository(database).create(
        "someone@example.com",
        hash_password("x" * 14),
        role_id=await RolesRepository(database).ensure_owner(),
    )

    response = await api.post("/api/auth/setup", json=GOOD)

    assert response.status_code == 409
    assert await database["users"].count_documents({}) == 1


async def test_a_short_password_is_refused(api, database):
    response = await api.post(
        "/api/auth/setup", json={**GOOD, "password": "short", "password_confirm": "short"}
    )

    assert response.status_code == 422
    assert await database["users"].count_documents({}) == 0


async def test_mismatched_passwords_are_refused_server_side(api, database):
    """Browser validation is not a check. A mistyped password would lock the
    operator out of the one account nothing else can recreate."""
    response = await api.post(
        "/api/auth/setup", json={**GOOD, "password_confirm": "something else entirely"}
    )

    assert response.status_code == 422
    assert await database["users"].count_documents({}) == 0


async def test_the_verify_step_grants_nothing(api, database):
    """It exists so the operator learns the code is wrong before typing a
    password. Skipping it must not help."""
    ok = await api.post("/api/auth/setup/verify", json={"secret": SECRET})
    assert ok.status_code == 200

    bad = await api.post("/api/auth/setup", json={**GOOD, "secret": "nope" * 8})
    assert bad.status_code == 401
    assert await database["users"].count_documents({}) == 0


# -- the race ------------------------------------------------------------------


async def test_only_one_bootstrap_admin_can_ever_exist(api, database):
    """The count check and the insert are two operations. Two requests can pass
    the check together, with different emails, and the unique email index would
    not stop them -- so a partial unique index on `is_bootstrap` does."""
    await create_indexes(database)
    users = UsersRepository(database)
    owner_id = await RolesRepository(database).ensure_owner()

    await users.create(
        "first@example.com",
        hash_password("x" * 14),
        role_id=owner_id,
        is_bootstrap=True,
    )

    from pymongo.errors import DuplicateKeyError

    with pytest.raises(DuplicateKeyError):
        await users.create(
            "second@example.com",
            hash_password("x" * 14),
            role_id=owner_id,
            is_bootstrap=True,
        )


async def test_ordinary_users_are_not_covered_by_the_bootstrap_index(api, database):
    """The index is partial on the value, so users created any other way -- who
    omit the field entirely -- must still be creatable in numbers."""
    await create_indexes(database)
    users = UsersRepository(database)
    owner_id = await RolesRepository(database).ensure_owner()

    await users.create("a@example.com", hash_password("x" * 14), role_id=owner_id)
    await users.create("b@example.com", hash_password("x" * 14), role_id=owner_id)

    assert await database["users"].count_documents({}) == 2


# -- an unconfigured secret disables the route entirely ------------------------


def with_secret(settings, value: str):
    """Point the app at a specific setup secret for one test."""
    from app.config import get_settings
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"admin_setup_secret": value}
    )


async def test_an_unset_secret_disables_setup(api, database, settings):
    """The dangerous default. An empty secret must never read as
    "no secret required"."""
    with_secret(settings, "")

    # The realistic attack: a caller supplies *some* secret against a
    # deployment that configured none. An empty body field is rejected earlier
    # by validation, which proves nothing about the guard.
    response = await api.post("/api/auth/setup", json={**GOOD, "secret": "anything at all"})

    assert response.status_code == 404
    assert await database["users"].count_documents({}) == 0
    assert (await api.get("/api/auth/setup")).json()["required"] is False


async def test_a_secret_too_short_to_be_worth_having_disables_setup(api, database, settings):
    """A guessable secret is worse than none: it looks like protection. Refused
    at the point of use rather than silently accepted."""
    with_secret(settings, "hunter2")

    response = await api.post("/api/auth/setup", json={**GOOD, "secret": "hunter2"})

    assert response.status_code == 404
    assert await database["users"].count_documents({}) == 0


async def test_setup_is_not_offered_when_the_secret_is_unusable(api, settings):
    """The login page must not show a setup form the server will refuse."""
    with_secret(settings, "short")

    assert (await api.get("/api/auth/setup")).json()["required"] is False
