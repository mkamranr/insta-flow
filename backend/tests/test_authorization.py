"""Authorization: who may call what.

The route-coverage test below is the most valuable one in this file. Permission
checks written inside a handler body are opt-in and therefore forgettable -- that
is exactly how three handlers in `rules.py` came to load a rule with no ownership
check at all. Declaring the requirement in the decorator makes it machine-checkable,
and this is the machine that checks it.
"""

import pytest
from fastapi.routing import APIRoute

from app.api.deps import require, require_any
from app.main import app
from app.permissions import ALL
from tests.conftest import TEST_PASSWORD

# Routes under /api that legitimately carry no permission, with the reason.
# Anything not listed here must declare one.
UNGATED = {
    # Signing in cannot require being signed in.
    ("POST", "/api/auth/login"),
    # Signing out is always allowed; it needs no capability.
    ("POST", "/api/auth/logout"),
    # Reading your own identity is what the frontend gates *on*. Requiring a
    # permission to learn your permissions is circular.
    ("GET", "/api/auth/me"),
    # Changing your own password is available to everyone, whatever their role.
    ("POST", "/api/auth/password"),
    # First-run setup, before any user or role exists. Fenced by ADMIN_SETUP_SECRET
    # and by refusing once any user exists.
    ("GET", "/api/auth/setup"),
    ("POST", "/api/auth/setup"),
    ("POST", "/api/auth/setup/verify"),
    # Meta drives these. The OAuth callback is bound by a signed single-use state,
    # and the two compliance callbacks by Meta's signed_request HMAC. Requiring a
    # permission would make them depend on a session cookie surviving Meta's
    # redirect, which is precisely what SameSite=Lax does not guarantee.
    ("GET", "/api/auth/instagram/callback"),
    ("POST", "/api/auth/instagram/deauthorize"),
    ("GET", "/api/auth/instagram/deauthorize"),
    ("POST", "/api/auth/instagram/data-deletion"),
    ("GET", "/api/auth/instagram/data-deletion"),
}


def _declared_permissions(dependant) -> set[str]:
    """Every permission any dependency of this route requires, however nested."""
    found: set[str] = set()
    for dependency in dependant.dependencies:
        found.update(getattr(dependency.call, "__permissions__", ()))
        found.update(_declared_permissions(dependency))
    return found


def _api_routes():
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, route.path, route


def test_every_api_route_declares_a_permission():
    """The guard that stops a future route shipping silently ungated."""
    ungated = [
        f"{method} {path}"
        for method, path, route in _api_routes()
        if (method, path) not in UNGATED and not _declared_permissions(route.dependant)
    ]
    assert not ungated, "these /api routes declare no permission:\n  " + "\n  ".join(ungated)


def test_the_allowlist_has_not_rotted():
    """An entry that no longer matches a real route is a silent bypass waiting to
    be reintroduced under the same name."""
    live = {(method, path) for method, path, _ in _api_routes()}
    stale = sorted(f"{m} {p}" for m, p in UNGATED - live)
    assert not stale, "UNGATED names routes that no longer exist:\n  " + "\n  ".join(stale)


def test_every_declared_permission_is_in_the_catalogue():
    for method, path, route in _api_routes():
        for permission in _declared_permissions(route.dependant):
            assert permission in ALL, f"{method} {path} requires unknown {permission!r}"


def test_a_typo_is_refused_at_import_time():
    """A permission nobody can hold would silently lock a route, with no symptom
    until someone complains they cannot do their job."""
    with pytest.raises(ValueError, match="not in the permission catalogue"):
        require("automations.veiw")
    with pytest.raises(ValueError, match="not in the permission catalogue"):
        require_any("automations.view", "nonsense.action")


async def test_signed_out_gets_401_not_403(api, database):
    """Authentication is resolved before authorization, so an anonymous caller is
    told to sign in rather than that they lack a permission."""
    assert (await api.get("/api/rules")).status_code == 401


async def test_a_role_without_the_permission_is_refused(
    api, database, role_factory, user_factory
):
    viewer = await role_factory("Read only", ["automations.view"])
    await user_factory("viewer@example.com", viewer)
    await api.post(
        "/api/auth/login", json={"email": "viewer@example.com", "password": TEST_PASSWORD}
    )

    # Allowed: no account is connected, so this is the 422 every rules list gives.
    assert (await api.get("/api/rules")).status_code == 422

    refused = await api.post("/api/rules", json={"name": "x", "message": "y"})
    assert refused.status_code == 403
    body = refused.json()
    assert body["error"]["code"] == "authorization_error"
    # The reply must not name the missing permission -- that detail goes to the log.
    assert "automations.create" not in refused.text


async def test_a_user_with_no_role_can_do_nothing(api, database):
    from app.db.repositories import RolesRepository, UsersRepository
    from app.services.security.passwords import hash_password

    users = UsersRepository(database)
    # `create` will not accept a null role -- `to_object_id(None)` refuses it -- so
    # the only way to reach this state is the one a real deployment reaches it by:
    # a user that predates the field, or whose role was deleted underneath them.
    document = await users.create(
        "roleless@example.com",
        hash_password(TEST_PASSWORD),
        role_id=await RolesRepository(database).ensure_owner(),
    )
    await database["users"].update_one({"_id": document["_id"]}, {"$unset": {"role_id": ""}})
    await api.post(
        "/api/auth/login", json={"email": "roleless@example.com", "password": TEST_PASSWORD}
    )
    assert (await api.get("/api/rules")).status_code == 403
    assert (await api.get("/api/analytics/summary")).status_code == 403


async def test_a_role_change_applies_without_signing_in_again(
    api, database, role_factory, user_factory
):
    """The payoff of a claim-free session cookie: revoking a permission takes
    effect on the next request, not at the next login."""
    role = await role_factory("Temporary", ["analytics.view"])
    await user_factory("temp@example.com", role)
    await api.post(
        "/api/auth/login", json={"email": "temp@example.com", "password": TEST_PASSWORD}
    )
    assert (await api.get("/api/analytics/summary")).status_code == 200

    await database["roles"].update_one({"_id": role["_id"]}, {"$set": {"permissions": []}})

    # Same cookie, no re-login.
    assert (await api.get("/api/analytics/summary")).status_code == 403


async def test_the_user_is_read_once_per_request(api, database, monkeypatch, admin):
    """A guarded route that also takes `user: CurrentUser` must not cost two
    lookups. FastAPI caches dependencies per request; this pins that it does."""
    from app.db.repositories import UsersRepository

    await api.post(
        "/api/auth/login", json={"email": admin["email"], "password": TEST_PASSWORD}
    )

    original = UsersRepository.get
    calls = []

    async def counted(self, user_id):
        calls.append(user_id)
        return await original(self, user_id)

    monkeypatch.setattr(UsersRepository, "get", counted)
    # /api/rules declares require("automations.view") and takes user: CurrentUser.
    await api.get("/api/rules")
    assert len(calls) == 1, f"expected one user lookup, got {len(calls)}"
