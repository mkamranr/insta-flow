"""Phase 3 authentication. Covers the seven cases required by §76."""

import pytest

from app.config import get_settings
from app.db.repositories import AuditLogsRepository, UsersRepository
from app.services.security import sessions
from app.services.security.passwords import hash_password, verify_password
from tests.conftest import TEST_PASSWORD

COOKIE = get_settings().session_cookie_name


# --- password hashing -------------------------------------------------------


def test_hash_is_not_the_password():
    hashed = hash_password("hunter2-hunter2")
    assert hashed != "hunter2-hunter2"
    assert hashed.startswith("$argon2")


def test_verify_accepts_the_right_password():
    assert verify_password("hunter2-hunter2", hash_password("hunter2-hunter2")) is True


def test_verify_rejects_the_wrong_password():
    assert verify_password("nope", hash_password("hunter2-hunter2")) is False


def test_same_password_hashes_differently_each_time():
    """Argon2 salts per hash, so identical passwords must not collide."""
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_survives_a_malformed_hash():
    """A corrupt stored hash must return False, not raise a 500."""
    assert verify_password("anything", "not-a-hash") is False


# --- session cookies --------------------------------------------------------


def test_session_round_trips():
    token = sessions.issue("secret", "507f1f77bcf86cd799439011", 3)
    session = sessions.verify("secret", token, 3600)
    assert session == ("507f1f77bcf86cd799439011", 3)


def test_session_signed_with_another_secret_is_rejected():
    token = sessions.issue("secret-a", "507f1f77bcf86cd799439011", 0)
    assert sessions.verify("secret-b", token, 3600) is None


def test_tampered_session_is_rejected():
    token = sessions.issue("secret", "507f1f77bcf86cd799439011", 0)
    assert sessions.verify("secret", token[:-4] + "AAAA", 3600) is None


def test_expired_session_is_rejected():
    """max_age=0 means the token is already older than its permitted age."""
    token = sessions.issue("secret", "507f1f77bcf86cd799439011", 0)
    assert sessions.verify("secret", token, -1) is None


def test_empty_cookie_is_rejected():
    assert sessions.verify("secret", "", 3600) is None


# --- login ------------------------------------------------------------------


async def test_valid_login_sets_an_httponly_cookie(api, admin):
    response = await api.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["email"] == "admin@example.com"

    cookie_header = response.headers["set-cookie"]
    assert "httponly" in cookie_header.lower()
    assert "samesite=lax" in cookie_header.lower()


async def test_login_response_never_includes_the_password_hash(api, admin):
    response = await api.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": TEST_PASSWORD}
    )
    body = response.text
    assert "password_hash" not in body
    assert admin["password_hash"] not in body
    assert "session_epoch" not in body


async def test_invalid_password_is_rejected(api, admin):
    response = await api.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


async def test_unknown_email_gives_the_same_error_as_a_wrong_password(api, admin):
    """No account enumeration: both failures must be indistinguishable."""
    wrong_password = await api.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )
    unknown_email = await api.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "wrong"}
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_inactive_user_cannot_log_in(api, database, admin):
    await UsersRepository(database).update(admin["_id"], {"is_active": False})
    response = await api.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": TEST_PASSWORD}
    )
    assert response.status_code == 401


async def test_login_is_recorded_in_the_audit_log(api, database, admin):
    await api.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": TEST_PASSWORD}
    )
    entries = await AuditLogsRepository(database).list({"action": "auth.login"})
    assert len(entries) == 1
    assert entries[0]["entity_type"] == "user"


# --- protected endpoints ----------------------------------------------------


async def test_me_requires_a_session(api, admin):
    assert (await api.get("/api/auth/me")).status_code == 401


async def test_me_returns_the_user_when_signed_in(signed_in):
    response = await signed_in.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "admin@example.com"


async def test_a_forged_cookie_is_rejected(api, admin):
    api.cookies.set(COOKIE, "forged-value")
    assert (await api.get("/api/auth/me")).status_code == 401


async def test_a_session_for_a_deleted_user_is_rejected(api, database, admin):
    settings = get_settings()
    token = sessions.issue(settings.session_secret, str(admin["_id"]), 0)
    await UsersRepository(database).delete(admin["_id"])
    api.cookies.set(COOKIE, token)
    assert (await api.get("/api/auth/me")).status_code == 401


async def test_a_session_with_a_malformed_user_id_is_rejected(api):
    settings = get_settings()
    api.cookies.set(COOKIE, sessions.issue(settings.session_secret, "not-an-objectid", 0))
    assert (await api.get("/api/auth/me")).status_code == 401


# --- logout -----------------------------------------------------------------


async def test_logout_clears_the_session(signed_in):
    assert (await signed_in.post("/api/auth/logout")).status_code == 204
    assert (await signed_in.get("/api/auth/me")).status_code == 401


async def test_logout_revokes_cookies_issued_before_it(api, database, admin):
    """Two devices signed in; logging out on one must invalidate the other.

    This is what the session_epoch bump buys over merely clearing the cookie.
    """
    settings = get_settings()
    other_device = sessions.issue(settings.session_secret, str(admin["_id"]), 0)

    await api.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": TEST_PASSWORD}
    )
    await api.post("/api/auth/logout")

    api.cookies.clear()
    api.cookies.set(COOKIE, other_device)
    assert (await api.get("/api/auth/me")).status_code == 401


async def test_logout_requires_a_session(api):
    assert (await api.post("/api/auth/logout")).status_code == 401


@pytest.mark.parametrize("path", ["/api/auth/me", "/api/auth/logout"])
async def test_protected_routes_return_a_structured_error(api, path):
    method = api.get if path.endswith("me") else api.post
    body = (await method(path)).json()
    assert body["error"]["code"] == "authentication_error"
    assert "message" in body["error"]
