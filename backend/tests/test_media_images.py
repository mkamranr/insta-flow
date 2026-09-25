"""Serving post and story thumbnails from our own copy.

The bug this file exists for: Meta's `media_url` and `thumbnail_url` are signed
and expire, and re-fetching `/me/media` returns a different URL string carrying
the *same* `oe` (docs/meta-api.md §2d). So a resync cannot rescue an expired
image and the dashboard cannot render those links directly. For a story it is
worse than worse -- Meta stops listing it after 24 hours, so the moment it is
synced is the only moment its picture can ever be copied.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.repositories import MediaRepository, StoriesRepository
from app.errors import MediaUnavailableError
from app.services import media_cache
from app.services.meta.client import MetaMedia
from app.services.meta.mock import DEFAULT_JPEG, MockInstagramClient
from app.services.security.encryption import encrypt
from app.workers.tasks import prune_media_cache, sync_posts, sync_stories

from .conftest import TEST_PASSWORD

IG_ID = "17841400000000000"
LIVE = "https://scontent.cdninstagram.com/v/t51/{id}.jpg?oe=7FFFFFFF"


def post(media_id: str, *, media_type: str = "IMAGE", days_old: int = 1) -> MetaMedia:
    return MetaMedia(
        instagram_media_id=media_id,
        media_type=media_type,
        media_product_type="FEED",
        caption="hi",
        permalink=f"https://instagram.com/p/{media_id}",
        thumbnail_url=LIVE.format(id=f"t{media_id}") if media_type == "VIDEO" else None,
        media_url=LIVE.format(id=media_id) if media_type != "VIDEO" else None,
        published_at=datetime.now(UTC) - timedelta(days=days_old),
        like_count=1,
        comments_count=0,
    )


def story(story_id: str) -> MetaMedia:
    return MetaMedia(
        instagram_media_id=story_id,
        media_type="IMAGE",
        media_product_type="STORY",
        caption=None,
        permalink=f"https://instagram.com/stories/{story_id}",
        thumbnail_url=None,
        media_url=LIVE.format(id=story_id),
        published_at=datetime.now(UTC),
    )


@pytest.fixture
def uploads(isolated_uploads):
    """The temp directory `conftest.isolated_uploads` already pointed the
    settings at. Named here so a test can say what it is asserting about."""
    return isolated_uploads


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


def ctx(database, settings, client):
    return {"db": database, "settings": settings, "client": client, "job_try": 1}


# --- the bug --------------------------------------------------------------


async def test_a_story_image_survives_the_story_expiring(
    signed_in, database, account, settings, uploads, monkeypatch
):
    """The headline case.

    A story's CDN URL dies with the story and cannot be re-obtained from Meta at
    any price. Once the bytes are copied at sync time, the picture has to keep
    rendering even after the story has expired and its URL returns 403.
    """
    client = MockInstagramClient()
    client.stories = [story("18114310274047199")]
    await sync_stories(ctx(database, settings, client))

    # The story expires: Meta stops listing it, and its URL starts 403ing.
    client.stories = []
    await sync_stories(ctx(database, settings, client))
    client.fail("fetch_media_bytes", MediaUnavailableError("URL signature expired"), times=10)
    monkeypatch.setattr("app.api.rules.build_client", lambda settings, creds=None: client)

    response = await signed_in.get("/api/stories/18114310274047199/image")

    assert response.status_code == 200, response.text
    assert response.content == DEFAULT_JPEG
    assert response.headers["content-type"] == "image/jpeg"


async def test_an_expired_post_url_is_recorded_and_never_refetched(
    signed_in, database, account, settings, uploads, monkeypatch
):
    """A 403 is permanent, so the second request must not spend a round trip
    rediscovering that. Without this the page re-hits every dead URL on every
    render."""
    client = MockInstagramClient(media=[post("1")])
    client.fail("fetch_media_bytes", MediaUnavailableError("URL signature expired"), times=10)
    monkeypatch.setattr("app.api.rules.build_client", lambda settings, creds=None: client)

    await sync_posts(ctx(database, settings, client))
    first = await signed_in.get("/api/posts/1/image")
    calls_after_first = len(client.calls_to("fetch_media_bytes"))
    second = await signed_in.get("/api/posts/1/image")

    assert first.status_code == 404
    assert second.status_code == 404
    assert len(client.calls_to("fetch_media_bytes")) == calls_after_first
    assert second.headers["cache-control"] == "no-store", "a cached 404 would outlive the fix"

    stored = await MediaRepository(database).get_by_instagram_media_id(account["_id"], "1")
    assert stored["image_cache"]["failed_at"] is not None


# --- caching at sync time ---------------------------------------------------


async def test_the_cron_caches_every_image_once_and_then_stops(
    database, account, settings, uploads
):
    """Cold run fetches; warm run fetches nothing.

    The anti-storm assertion. ARQ replays missed crons in a burst on wake, and
    without the skip each one would redownload the whole grid.
    """
    client = MockInstagramClient(media=[post(str(i)) for i in range(5)])

    first = await sync_posts(ctx(database, settings, client))
    fetched = len(client.calls_to("fetch_media_bytes"))
    second = await sync_posts(ctx(database, settings, client))

    assert fetched == 5
    assert first["cached"] == 5
    assert len(client.calls_to("fetch_media_bytes")) == 5, "a second pass must refetch nothing"
    assert second["cached"] == 0


async def test_a_video_is_cached_from_its_thumbnail_not_its_video_file(
    database, account, settings, uploads
):
    """`media_url` on a video is the MP4. It is also the shorter-lived of the
    two (~36h against ~4.5 days), so `thumbnail_url` wins twice over."""
    client = MockInstagramClient(media=[post("9", media_type="VIDEO")])

    await sync_posts(ctx(database, settings, client))

    assert client.calls_to("fetch_media_bytes")[0].kwargs["url"] == LIVE.format(id="t9")


async def test_caching_is_bounded_per_run(database, account, settings, uploads, monkeypatch):
    """`job_timeout` is class-wide at 60s and a cancelled ARQ job raises nothing
    to classify, so a cold account must not try to download its whole grid."""
    monkeypatch.setattr(settings, "media_cache_per_run", 3, raising=False)
    client = MockInstagramClient(media=[post(str(i)) for i in range(10)])

    await sync_posts(ctx(database, settings, client))

    assert len(client.calls_to("fetch_media_bytes")) == 3


async def test_dry_run_still_caches(database, account, settings, uploads, monkeypatch):
    """Downloading an image changes nothing at Meta. A dry run that skipped it
    would throw away the only chance to keep a story's picture."""
    monkeypatch.setattr(settings, "dry_run", True, raising=False)
    client = MockInstagramClient()
    client.stories = [story("1")]

    result = await sync_stories(ctx(database, settings, client))

    assert result["cached"] == 1
    assert client.mutation_calls == []


def test_fetching_an_image_is_not_a_mutation():
    assert "fetch_media_bytes" not in MockInstagramClient.MUTATIONS


async def test_the_cache_can_be_turned_off(database, account, settings, uploads, monkeypatch):
    monkeypatch.setattr(settings, "media_cache_enabled", False, raising=False)
    client = MockInstagramClient(media=[post("1")])

    await sync_posts(ctx(database, settings, client))

    assert client.calls_to("fetch_media_bytes") == []


async def test_the_story_refresh_button_caches_before_it_expires_anything(
    signed_in, database, account, settings, uploads, monkeypatch
):
    """A hand-pressed story Refresh is very often the first time a story is seen.

    If it only upserted and left the download to the hourly cron, a story posted
    and pulled inside that hour would lose its picture with no way back.
    """
    client = MockInstagramClient()
    client.stories = [story("1")]
    monkeypatch.setattr("app.api.rules.build_client", lambda settings, creds=None: client)

    response = await signed_in.post("/api/stories/sync")

    assert response.status_code == 200, response.text
    stored = await StoriesRepository(database).get_by_instagram_story_id(account["_id"], "1")
    assert stored["image_cache"]["storage_key"]


async def test_the_posts_refresh_button_caches_too(
    signed_in, database, account, settings, uploads, monkeypatch
):
    """Otherwise the grid stays blank until the next cron tick, which looks
    broken to the person who just pressed Refresh."""
    client = MockInstagramClient(media=[post("1")])
    monkeypatch.setattr("app.api.rules.build_client", lambda settings, creds=None: client)

    response = await signed_in.post("/api/posts/sync")

    assert response.status_code == 200, response.text
    stored = await MediaRepository(database).get_by_instagram_media_id(account["_id"], "1")
    assert stored["image_cache"]["storage_key"]


# --- the route --------------------------------------------------------------


async def test_a_cached_image_is_served_immutably(signed_in, database, account, settings, uploads):
    client = MockInstagramClient(media=[post("1")])
    await sync_posts(ctx(database, settings, client))
    stored = await MediaRepository(database).get_by_instagram_media_id(account["_id"], "1")
    digest = stored["image_cache"]["digest"]

    response = await signed_in.get(f"/api/posts/1/image?v={digest[:12]}")

    assert response.status_code == 200
    assert response.content == DEFAULT_JPEG
    # `private`, never `public`: the response is workspace-scoped.
    assert "private" in response.headers["cache-control"]
    assert "immutable" in response.headers["cache-control"]


async def test_a_cold_miss_is_fetched_on_demand(
    signed_in, database, account, settings, uploads, monkeypatch
):
    """The backstop: a row synced before this feature shipped has a URL but no
    bytes, and must fill itself rather than showing a placeholder forever."""
    client = MockInstagramClient(media=[post("1")])
    monkeypatch.setattr(settings, "media_cache_enabled", False, raising=False)
    await sync_posts(ctx(database, settings, client))
    monkeypatch.setattr(settings, "media_cache_enabled", True, raising=False)
    monkeypatch.setattr("app.api.rules.build_client", lambda settings, creds=None: client)

    response = await signed_in.get("/api/posts/1/image")

    assert response.status_code == 200
    assert response.content == DEFAULT_JPEG
    assert response.headers["cache-control"] == "no-store"
    stored = await MediaRepository(database).get_by_instagram_media_id(account["_id"], "1")
    assert stored["image_cache"]["storage_key"]


async def test_an_unknown_media_id_is_a_404(signed_in, account, uploads):
    assert (await signed_in.get("/api/posts/nope/image")).status_code == 404


async def test_the_image_route_needs_a_session(api, account, uploads):
    assert (await api.get("/api/posts/1/image")).status_code == 401


async def test_the_image_route_needs_the_view_permission(
    api, database, role_factory, user_factory, account, uploads
):
    role = await role_factory("No content", ["analytics.view"])
    await user_factory("narrow@example.com", role)
    await api.post(
        "/api/auth/login", json={"email": "narrow@example.com", "password": TEST_PASSWORD}
    )

    assert (await api.get("/api/posts/1/image")).status_code == 403


async def test_the_route_works_under_the_workspace_mount(
    signed_in, database, admin, account, settings, uploads
):
    """Every router is mounted twice. An image that only rendered on the bare
    layout would be blank for every workspace-prefixed user."""
    client = MockInstagramClient(media=[post("1")])
    await sync_posts(ctx(database, settings, client))
    slug = (await database["users"].find_one({"_id": admin["_id"]}))["slug"]

    response = await signed_in.get(f"/u/{slug}/api/posts/1/image")

    assert response.status_code == 200
    assert response.content == DEFAULT_JPEG


async def test_one_workspace_cannot_read_anothers_image(
    api, database, admin, account, settings, uploads, role_factory, user_factory
):
    """Media ids are not secret and arrive from Meta.

    The second user has an Instagram account of their own, so this is not the
    "nothing connected yet" path -- it is a caller in good standing asking for a
    media id that belongs to somebody else. The lookup is account-scoped, so it
    must read as absent rather than forbidden: a 403 would confirm the id exists.
    """
    client = MockInstagramClient(media=[post("1")])
    await sync_posts(ctx(database, settings, client))

    role = await role_factory("Other owner", ["content.view"])
    other = await user_factory("other@example.com", role)
    await database["instagram_accounts"].insert_one(
        {
            "user_id": other["_id"],
            "instagram_user_id": "17841499999999999",
            "username": "neighbour",
            "status": "connected",
            "is_default": True,
            "access_token_encrypted": encrypt("token", settings.encryption_key),
            "created_at": datetime.now(UTC),
        }
    )
    await api.post(
        "/api/auth/login", json={"email": "other@example.com", "password": TEST_PASSWORD}
    )

    assert (await api.get("/api/posts/1/image")).status_code == 404


# --- what the payloads may contain -----------------------------------------


async def test_the_posts_payload_carries_no_meta_cdn_url(
    signed_in, database, account, settings, uploads
):
    """The regression guard. Leaving the CDN URLs on the wire is what let every
    page render a link that dies a day later."""
    client = MockInstagramClient(media=[post("1")])
    await sync_posts(ctx(database, settings, client))

    body = (await signed_in.get("/api/posts")).json()

    assert body, "expected a post"
    for item in body:
        assert "thumbnail_url" not in item
        assert "media_url" not in item
        assert "image_cache" not in item
        assert item["image_path"] == "/api/posts/1/image?v=" + item["image_path"].split("v=")[1]


async def test_the_analytics_payload_leaks_nothing_either(
    signed_in, database, account, settings, uploads
):
    """`/api/analytics/posts` used to spread the raw Mongo document, which would
    now also ship the on-disk storage key."""
    client = MockInstagramClient(media=[post("1")])
    await sync_posts(ctx(database, settings, client))

    body = (await signed_in.get("/api/analytics/posts")).json()

    for item in body:
        assert "thumbnail_url" not in item
        assert "media_url" not in item
        assert "image_cache" not in item
        assert "image_path" in item


async def test_a_post_with_no_image_at_all_has_no_path(
    signed_in, database, account, settings, uploads
):
    """A video whose thumbnail Meta never supplied. The client renders its
    placeholder rather than requesting an image that cannot exist."""
    bare = MetaMedia(
        instagram_media_id="7",
        media_type="VIDEO",
        media_product_type="REELS",
        caption="no thumb",
        permalink="https://instagram.com/p/7",
        published_at=datetime.now(UTC),
    )
    await sync_posts(ctx(database, settings, MockInstagramClient(media=[bare])))

    body = (await signed_in.get("/api/posts")).json()

    assert body[0]["image_path"] is None


# --- pruning ----------------------------------------------------------------


async def test_pruning_keeps_the_newest_and_drops_the_rest(
    database, account, settings, uploads, monkeypatch
):
    monkeypatch.setattr(settings, "media_cache_keep_per_account", 2, raising=False)
    client = MockInstagramClient(media=[post(str(i), days_old=i + 1) for i in range(5)])
    await sync_posts(ctx(database, settings, client))
    assert len(list(uploads.rglob("*.jpg"))) == 5

    result = await prune_media_cache(ctx(database, settings, client))

    assert result["pruned"] == 3
    assert len(list(uploads.rglob("*.jpg"))) == 2
    media = MediaRepository(database)
    # The two newest -- days_old 1 and 2 -- keep their bytes.
    assert (await media.get_by_instagram_media_id(account["_id"], "0"))["image_cache"]
    assert "image_cache" not in await media.get_by_instagram_media_id(account["_id"], "4")


async def test_pruning_a_missing_file_still_clears_the_row(
    database, account, settings, uploads, monkeypatch
):
    """A row pointing at a file that is gone degrades to a placeholder; a file
    no row points at is invisible and never reclaimed. Clear the row either way.
    """
    monkeypatch.setattr(settings, "media_cache_keep_per_account", 0, raising=False)
    client = MockInstagramClient(media=[post("1")])
    await sync_posts(ctx(database, settings, client))
    for path in uploads.rglob("*.jpg"):
        path.unlink()

    await prune_media_cache(ctx(database, settings, client))

    stored = await MediaRepository(database).get_by_instagram_media_id(account["_id"], "1")
    assert "image_cache" not in stored


async def test_a_story_keeps_its_bytes_when_posts_are_pruned(
    database, account, settings, uploads, monkeypatch
):
    """Stories are never evicted: their bytes are irreplaceable, and there are
    only ever a handful."""
    monkeypatch.setattr(settings, "media_cache_keep_per_account", 0, raising=False)
    client = MockInstagramClient(media=[])
    client.stories = [story("1")]
    await sync_stories(ctx(database, settings, client))

    await prune_media_cache(ctx(database, settings, client))

    stored = await StoriesRepository(database).get_by_instagram_story_id(account["_id"], "1")
    assert stored["image_cache"]["storage_key"]
    assert media_cache.path_for(uploads, stored["image_cache"]["storage_key"]) is not None
