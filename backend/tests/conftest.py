import os

import httpx
import pytest
from pymongo import AsyncMongoClient

from app.api.deps import get_db
from app.config import get_settings
from app.db.bootstrap import prepare
from app.db.repositories import RolesRepository, UsersRepository
from app.main import app
from app.services.security.passwords import hash_password

# Tests must never inherit a real database name from a developer's shell.
os.environ.setdefault("MONGO_DATABASE", "instaflow_test")

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://mongodb:27017")
TEST_DB = "instaflow_test"

TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
async def database():
    """A clean test database, or skip when MongoDB is unreachable."""
    client = AsyncMongoClient(MONGO_URL, tz_aware=True, serverSelectionTimeoutMS=1500)
    try:
        await client.admin.command("ping")
    except Exception:
        pytest.skip(f"MongoDB not reachable at {MONGO_URL}")
    await client.drop_database(TEST_DB)
    db = client[TEST_DB]
    await prepare(db)
    try:
        yield db
    finally:
        await client.drop_database(TEST_DB)
        await client.close()


class FakeRedis:
    """Enough of the Redis surface for throttling and OAuth state."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.store: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, seconds):
        self.expiries[key] = seconds

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, key):
        removed = self.counts.pop(key, None), self.store.pop(key, None)
        return 1 if any(v is not None for v in removed) else 0


@pytest.fixture(autouse=True)
def isolated_uploads(tmp_path, monkeypatch):
    """Point `upload_dir` at a temp directory for every test.

    Autouse, and not optional. `./uploads` is bind-mounted into the container
    the suite runs in, so a test that stores a thumbnail or an attachment writes
    into the developer's real uploads directory -- which is exactly what
    happened the first time the image cache shipped: the suite left 11 stray
    19-byte mock JPEGs in `uploads/media/`, referenced by nothing.

    Both halves are needed, and patching only the first is the trap. Ten test
    modules build their own `Settings(...)` rather than taking the shared
    fixture, and those instances are born with the real `/app/uploads` default
    -- so setting the env var is what actually covers the suite, and patching
    the cached singleton covers everything already holding a reference to it.
    """
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path), raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def redis(monkeypatch):
    """Every test gets a working Redis: login throttling touches it on each
    attempt, so an unpatched pool breaks tests unrelated to throttling."""
    fake = FakeRedis()
    monkeypatch.setattr("app.queue.get_pool", lambda: fake)
    return fake


@pytest.fixture
async def api(database):
    """HTTP client bound to the app, with the test database injected.

    ASGITransport skips the lifespan, so `get_db` is overridden rather than
    relying on a real startup connection.
    """
    app.dependency_overrides[get_db] = lambda: database
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def admin(database):
    """A known, active admin user, holding the Owner role.

    Owner and not something narrower: every other test in this suite predates
    authorization and assumes the signed-in user can do anything. Giving this
    fixture a lesser role would turn the whole suite red for a reason that has
    nothing to do with what each test is checking.
    """
    users = UsersRepository(database)
    return await users.create(
        "admin@example.com",
        hash_password(TEST_PASSWORD),
        role_id=await RolesRepository(database).ensure_owner(),
    )


@pytest.fixture
async def role_factory(database):
    """Build a role holding exactly the given permissions."""

    async def make(name, permissions):
        return await RolesRepository(database).create(
            name=name, description=f"{name} test role", permissions=list(permissions)
        )

    return make


@pytest.fixture
async def user_factory(database):
    """Build an active user holding a given role."""

    async def make(email, role):
        return await UsersRepository(database).create(
            email, hash_password(TEST_PASSWORD), role_id=role["_id"]
        )

    return make


@pytest.fixture
async def signed_in(api, admin):
    """An API client carrying a valid session cookie."""
    response = await api.post(
        "/api/auth/login", json={"email": admin["email"], "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return api


@pytest.fixture
async def other_client(database):
    """A second, independent HTTP client with its own cookie jar.

    `signed_in` returns the *same* object as `api`, so signing in as a second
    person on `api` silently replaces the first person's session. Any test with
    two simultaneous sessions needs this instead.
    """
    app.dependency_overrides[get_db] = lambda: database
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def settings():
    return get_settings()
