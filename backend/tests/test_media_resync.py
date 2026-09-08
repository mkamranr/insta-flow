"""Full media resync: the route, the job, and the cheap path it must not change.

The last of those is the point of the file. Before this feature nothing pinned
`POST /api/posts/sync` to a single page -- there was no test of that route at all
-- so a change that turned every Refresh button into a 200-request walk of the
media edge would have been completely silent.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.repositories import InstagramAccountsRepository, MediaRepository
from app.errors import MetaAPIError
from app.services.meta.client import MetaMedia
from app.services.meta.http_client import MEDIA_MAX_PAGES, MEDIA_PAGE_SIZE
from app.services.meta.mock import MockInstagramClient
from app.services.security.encryption import encrypt
from app.workers.tasks import resync_all_media

from .conftest import TEST_PASSWORD

IG_ID = "17841400000000000"


def post(media_id: str, caption: str = "hi") -> MetaMedia:
    return MetaMedia(
        instagram_media_id=media_id,
        media_type="IMAGE",
        media_product_type="FEED",
        caption=caption,
        permalink=f"https://instagram.com/p/{media_id}",
        thumbnail_url=None,
        media_url=None,
        published_at=datetime.now(UTC) - timedelta(days=1),
        like_count=1,
        comments_count=0,
    )


class CapturingRedis:
    """Captures enqueues instead of scheduling them.

    The autouse `redis` fixture's fake has no `enqueue_job`, so any test of a
    route that queues needs its own. Copied from `test_automation_pause.py`.
    """

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.jobs.append({"name": name, "args": args, "job_id": kwargs.get("_job_id")})
        return None

    async def incr(self, key):
        return 1

    async def expire(self, key, seconds):
        return True


@pytest.fixture
async def account(database, admin, settings):
    await database["instagram_accounts"].insert_one(
        {
            "user_id": admin["_id"],
            "instagram_user_id": IG_ID,
            "username": "myshop",
            "status": "connected",
            "is_default": True,
            "access_token_encrypted": encrypt("token", settings.encryption_key),
            "created_at": datetime.now(UTC),
        }
    )
    return await database["instagram_accounts"].find_one({"instagram_user_id": IG_ID})


@pytest.fixture
def queued(monkeypatch):
    fake = CapturingRedis()
    monkeypatch.setattr("app.queue.get_pool", lambda: fake)
    return fake


def ctx(database, settings, client):
    return {"db": database, "settings": settings, "client": client, "job_try": 1}


# --- the cheap path must stay cheap -----------------------------------------


async def test_the_refresh_button_makes_exactly_one_request(
    signed_in, database, account, monkeypatch, settings
):
    """`POST /api/posts/sync` is what both the Dashboard's "Sync from Instagram"
    and the Posts & Reels "Refresh" call. One page, always."""
    client = MockInstagramClient(media=[post(str(i)) for i in range(120)])
    monkeypatch.setattr("app.api.rules.build_client", lambda settings, creds=None: client)

    response = await signed_in.post("/api/posts/sync")

    assert response.status_code == 200, response.text
    calls = client.calls_to("get_media")
    assert len(calls) == 1, "a Refresh must not walk the whole edge"
    assert calls[0].kwargs["limit"] == MEDIA_PAGE_SIZE
    assert response.json()["synced"] == MEDIA_PAGE_SIZE


async def test_the_cron_sync_also_makes_exactly_one_request(database, account, settings):
    from app.workers.tasks import sync_posts

    client = MockInstagramClient(media=[post(str(i)) for i in range(120)])
    result = await sync_posts(ctx(database, settings, client))

    assert len(client.calls_to("get_media")) == 1
    assert result["synced"] == MEDIA_PAGE_SIZE


# --- the resync route -------------------------------------------------------


async def test_the_resync_route_queues_the_job_once_per_account(
    signed_in, account, queued
):
    response = await signed_in.post("/api/posts/resync")

    assert response.status_code == 200
    assert response.json() == {"queued": True}
    assert queued.jobs[0]["name"] == "resync_all_media"
    assert queued.jobs[0]["args"] == (str(account["_id"]),)
    # ARQ discards a duplicate id, so a second press while the first is running
    # is a no-op with no lock and no status pre-check.
    assert queued.jobs[0]["job_id"] == f"media-resync:{account['_id']}"


async def test_the_resync_route_needs_the_sync_permission(
    api, database, role_factory, user_factory, account
):
    """`content.edit`, whose label in the roles grid is literally "Sync"."""
    role = await role_factory("Look only", ["content.view", "settings.view"])
    await user_factory("viewer@example.com", role)
    await api.post(
        "/api/auth/login", json={"email": "viewer@example.com", "password": TEST_PASSWORD}
    )

    assert (await api.post("/api/posts/resync")).status_code == 403


async def test_the_resync_route_is_gated_at_both_mounts(
    api, database, role_factory, user_factory, account, admin
):
    role = await role_factory("Look only", ["content.view"])
    await user_factory("viewer2@example.com", role)
    await api.post(
        "/api/auth/login", json={"email": "viewer2@example.com", "password": TEST_PASSWORD}
    )

    assert (await api.post(f"/u/{admin['slug']}/api/posts/resync")).status_code == 403


# --- the job ----------------------------------------------------------------


async def test_the_job_walks_every_page_and_records_the_outcome(
    database, account, settings
):
    client = MockInstagramClient(media=[post(str(i)) for i in range(120)])

    result = await resync_all_media(ctx(database, settings, client), str(account["_id"]))

    assert result == {"status": "done", "synced": 120, "pages": 3}
    assert len(client.calls_to("get_media")) == 3
    assert await database["media"].count_documents({}) == 120

    row = await database["instagram_accounts"].find_one({"_id": account["_id"]})
    assert row["media_resync_state"] == "done"
    assert row["media_resync_count"] == 120
    assert row["media_resync_error"] is None
    assert row["media_resync_finished_at"] is not None


async def test_the_job_updates_a_caption_that_changed(database, account, settings):
    """The whole reason the feature exists. `upsert` is a plain `$set`, so an
    edit made on Instagram overwrites what is stored."""
    await MediaRepository(database).upsert(
        account["_id"], "1789", {"instagram_media_id": "1789", "caption": "before"}
    )
    client = MockInstagramClient(media=[post("1789", caption="after")])

    await resync_all_media(ctx(database, settings, client), str(account["_id"]))

    row = await database["media"].find_one({"instagram_media_id": "1789"})
    assert row["caption"] == "after"


async def test_a_failure_keeps_the_pages_already_written(database, account, settings):
    """Partial progress is worth more than a clean rollback: pages 1 and 2 are
    correct data, and the recorded error says what stopped page 3."""
    client = MockInstagramClient(media=[post(str(i)) for i in range(120)])
    client.fail("get_media", MetaAPIError("token expired", status_code=401), times=1)

    result = await resync_all_media(ctx(database, settings, client), str(account["_id"]))

    assert result["status"] == "failed"
    row = await database["instagram_accounts"].find_one({"_id": account["_id"]})
    assert row["media_resync_state"] == "failed"
    assert "token expired" in row["media_resync_error"]


async def test_the_job_ignores_a_deleted_account(database, settings):
    from bson import ObjectId

    client = MockInstagramClient(media=[post("1")])
    result = await resync_all_media(ctx(database, settings, client), str(ObjectId()))

    assert result == {"status": "skipped", "reason": "account_missing"}
    assert client.calls_to("get_media") == []


async def test_the_job_does_not_arm_a_waiting_template(database, account, settings):
    """Surfacing a batch of old posts must not apply somebody's upcoming-post
    template to one of them. The resync deliberately does not call
    `apply_upcoming_post` at all."""
    await database["upcoming_posts"].insert_one(
        {
            "instagram_account_id": account["_id"],
            "pending": True,
            "status": "waiting",
            "rule": {"name": "Template", "keywords": ["hi"]},
            "created_at": datetime.now(UTC) - timedelta(days=30),
        }
    )
    client = MockInstagramClient(media=[post("1"), post("2")])

    await resync_all_media(ctx(database, settings, client), str(account["_id"]))

    assert await database["rules"].count_documents({}) == 0


async def test_the_page_cap_is_the_whole_edge():
    """200 pages of 50 is Meta's documented 10K ceiling for this edge, so
    nothing legitimate can be truncated by it."""
    assert MEDIA_MAX_PAGES * MEDIA_PAGE_SIZE == 10_000


# --- the orphan cleanup -----------------------------------------------------


async def test_orphaned_media_is_dropped_on_startup(database, account):
    """168 such rows existed on the live install, left by a disconnect that
    hard-deleted the account row."""
    from bson import ObjectId

    from app.db.bootstrap import prepare

    dead = ObjectId()
    await database["media"].insert_many(
        [
            {"instagram_account_id": dead, "instagram_media_id": "orphan-1"},
            {"instagram_account_id": dead, "instagram_media_id": "orphan-2"},
            {"instagram_account_id": account["_id"], "instagram_media_id": "mine"},
        ]
    )

    await prepare(database)

    remaining = [row["instagram_media_id"] async for row in database["media"].find({})]
    assert remaining == ["mine"]

    # Idempotent: a second boot finds nothing to do and changes nothing.
    await prepare(database)
    assert await database["media"].count_documents({}) == 1


async def test_the_cleanup_deletes_nothing_when_no_account_exists(database):
    """The guard that matters. Derived naively, "media whose account is missing"
    on a fresh install is every row -- so a first boot would delete everything
    the first sync had just written."""
    from bson import ObjectId

    from app.db.bootstrap import prepare

    await database["media"].insert_one(
        {"instagram_account_id": ObjectId(), "instagram_media_id": "keep-me"}
    )
    assert await database["instagram_accounts"].count_documents({}) == 0

    await prepare(database)

    assert await database["media"].count_documents({}) == 1


async def test_dropping_orphans_refuses_an_empty_account_list(database):
    """Belt and braces: the repository refuses outright rather than trusting the
    caller to have applied the guard."""
    with pytest.raises(ValueError, match="every media row"):
        await MediaRepository(database).drop_orphaned([])


# --- resync state on the wire -----------------------------------------------


async def test_the_resync_state_reaches_the_api(signed_in, database, account):
    """`InstagramAccountPublic` is an explicit allowlist, so a field only reaches
    the UI if it was added to both the model and `from_document`."""
    await InstagramAccountsRepository(database).finish_media_resync(
        account["_id"], count=30, pages=1
    )

    listed = (await signed_in.get("/api/instagram/accounts")).json()

    assert listed[0]["media_resync_state"] == "done"
    assert listed[0]["media_resync_count"] == 30
    assert listed[0]["media_resync_pages"] == 1
