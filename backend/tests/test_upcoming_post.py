"""The automation that waits for your next post.

Two things carry the weight: *which* post it lands on when several appear
between syncs, and that it lands exactly once. The second is a race, so it is
tested by running two syncs concurrently rather than by reasoning about it.
"""

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.config import Settings
from app.db.indexes import create_indexes
from app.db.repositories import UpcomingPostsRepository
from app.services.meta import MockInstagramClient
from app.services.meta.client import MetaMedia
from app.services.security.encryption import encrypt
from app.workers.tasks import apply_upcoming_post, sync_posts

ACCOUNT_IG_ID = "17841439340431612"
KEY = "k" * 64


def settings_for() -> Settings:
    return Settings(
        dry_run=False,
        meta_app_secret="s",
        meta_webhook_verify_token="verify",
        session_secret="s" * 64,
        encryption_key=KEY,
        meta_app_id="1",
    )


@pytest.fixture
def client():
    return MockInstagramClient()


@pytest.fixture
async def account(database):
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "instagram_user_id": ACCOUNT_IG_ID,
            "username": "daily.techtalks",
            "status": "connected",
            "access_token_encrypted": encrypt("test-access-token", KEY),
        }
    )
    return account_id


TEMPLATE = {
    "name": "Next post",
    "trigger": "comment",
    "scope": "specific",
    "keywords": ["link"],
    "any_word": False,
    "match_mode": "contains",
    "message": "Here it is: https://example.com/thing",
    "buttons": [],
    "opening_dm": {"enabled": False, "message": "", "button_text": ""},
    "require_follow": {"enabled": False, "prompt": ""},
    "collect_email": {"enabled": False, "prompt": ""},
    "once_per_person": True,
    "enabled": True,
    "priority": 100,
    "limits": {"per_minute": 10, "per_hour": 100, "per_day": 500},
}


async def arm(database, account_id, created_at=None):
    return (
        await database["upcoming_posts"].insert_one(
            {
                "instagram_account_id": account_id,
                "pending": True,
                "status": "waiting",
                "rule": dict(TEMPLATE),
                "created_at": created_at or datetime.now(UTC),
            }
        )
    ).inserted_id


def post(media_id: str, published_at: datetime) -> MetaMedia:
    return MetaMedia(
        instagram_media_id=media_id,
        media_type="IMAGE",
        media_product_type="FEED",
        caption="hello",
        media_url="https://cdn/x.jpg",
        published_at=published_at,
    )


def ctx_for(database, client):
    return {"db": database, "settings": settings_for(), "client": client, "job_try": 1}


async def account_doc(database, account_id):
    return await database["instagram_accounts"].find_one({"_id": account_id})


# -- which post it lands on ----------------------------------------------------


async def test_it_applies_to_a_post_published_after_it_was_set(database, client, account):
    await arm(database, account)
    client.media = [post("new_1", datetime.now(UTC) + timedelta(seconds=1))]

    result = await sync_posts(ctx_for(database, client))

    assert result["armed"] == 1
    rule = await database["rules"].find_one({"created_from_upcoming": True})
    assert rule["instagram_media_ids"] == ["new_1"]
    assert rule["trigger"] == "comment"
    assert rule["scope"] == "specific"


async def test_a_post_that_predates_it_is_not_touched(database, client, account):
    """"Upcoming" means upcoming. A post from last week is not what was meant,
    however late we happen to sync it."""
    await arm(database, account)
    client.media = [post("old_1", datetime.now(UTC) - timedelta(days=7))]

    result = await sync_posts(ctx_for(database, client))

    assert result["armed"] == 0
    assert await database["rules"].count_documents({}) == 0
    # Still waiting, not consumed.
    assert await UpcomingPostsRepository(database).pending_for(account) is not None


async def test_the_earliest_of_several_new_posts_wins(database, client, account):
    """Several posts can appear between syncs. The first one published gets it —
    "newest" would make the outcome depend on when the sync happened to run."""
    await arm(database, account)
    base = datetime.now(UTC)
    client.media = [
        post("third", base + timedelta(minutes=30)),
        post("first", base + timedelta(minutes=10)),
        post("second", base + timedelta(minutes=20)),
    ]

    await sync_posts(ctx_for(database, client))

    rule = await database["rules"].find_one({"created_from_upcoming": True})
    assert rule["instagram_media_ids"] == ["first"]


# -- it is consumed ------------------------------------------------------------


async def test_the_record_is_removed_once_applied(database, client, account):
    upcoming_id = await arm(database, account)
    client.media = [post("new_1", datetime.now(UTC) + timedelta(seconds=1))]

    await sync_posts(ctx_for(database, client))

    assert await UpcomingPostsRepository(database).pending_for(account) is None
    record = await database["upcoming_posts"].find_one({"_id": upcoming_id})
    assert record["status"] == "applied"
    assert record["instagram_media_id"] == "new_1"


async def test_a_second_sync_does_not_apply_it_again(database, client, account):
    await arm(database, account)
    client.media = [post("new_1", datetime.now(UTC) + timedelta(seconds=1))]

    first = await sync_posts(ctx_for(database, client))
    second = await sync_posts(ctx_for(database, client))

    assert (first["armed"], second["armed"]) == (1, 0)
    assert await database["rules"].count_documents({"created_from_upcoming": True}) == 1


async def test_a_later_post_does_not_get_it_too(database, client, account):
    """Consumed means consumed: the post after next is not automated."""
    await arm(database, account)
    base = datetime.now(UTC)
    client.media = [post("new_1", base + timedelta(minutes=1))]
    await sync_posts(ctx_for(database, client))

    client.media = [
        post("new_1", base + timedelta(minutes=1)),
        post("new_2", base + timedelta(minutes=2)),
    ]
    await sync_posts(ctx_for(database, client))

    rules = await database["rules"].find({"created_from_upcoming": True}).to_list(None)
    assert [r["instagram_media_ids"] for r in rules] == [["new_1"]]


async def test_two_concurrent_syncs_apply_it_once(database, client, account):
    """The claim is the atomic step, so the loser finds nothing to claim.

    A check-then-write would have both see "pending" and both create a rule.
    """
    import asyncio

    await arm(database, account)
    client.media = [post("new_1", datetime.now(UTC) + timedelta(seconds=1))]

    results = await asyncio.gather(
        sync_posts(ctx_for(database, MockInstagramClient(media=client.media))),
        sync_posts(ctx_for(database, MockInstagramClient(media=client.media))),
    )

    assert sum(r["armed"] for r in results) == 1
    assert await database["rules"].count_documents({"created_from_upcoming": True}) == 1


# -- one at a time -------------------------------------------------------------


async def test_only_one_can_be_pending(database, account):
    """Enforced by a partial unique index, not by asking first."""
    await create_indexes(database)
    await arm(database, account)

    with pytest.raises(DuplicateKeyError):
        await arm(database, account)


async def test_a_new_one_can_be_set_after_the_last_was_applied(database, client, account):
    """`pending` is unset on apply, so the partial index stops covering the row.
    A plain unique index would block every future template."""
    await create_indexes(database)
    await arm(database, account)
    client.media = [post("new_1", datetime.now(UTC) + timedelta(seconds=1))]
    await sync_posts(ctx_for(database, client))

    second = await arm(database, account)

    assert second is not None


# -- failure -------------------------------------------------------------------


async def test_a_failed_fetch_leaves_the_template_waiting(database, client, account):
    from app.errors import MetaAPIError

    await arm(database, account)
    client.fail("get_media", MetaAPIError("token expired", status_code=401, retryable=False))

    result = await sync_posts(ctx_for(database, client))

    assert result["armed"] == 0
    assert await UpcomingPostsRepository(database).pending_for(account) is not None


async def test_a_failed_rule_insert_puts_it_back(database, client, account, monkeypatch):
    """Losing the template because one sync failed would be worse than retrying."""
    from app.db.repositories.rules import RulesRepository

    await arm(database, account)
    await database["media"].insert_one(
        {
            "instagram_account_id": account,
            "instagram_media_id": "new_1",
            "published_at": datetime.now(UTC) + timedelta(seconds=1),
        }
    )

    async def boom(self, document):
        raise RuntimeError("mongo is having a day")

    monkeypatch.setattr(RulesRepository, "insert", boom)

    armed = await apply_upcoming_post(database, await account_doc(database, account))

    assert armed == 0
    record = await UpcomingPostsRepository(database).pending_for(account)
    assert record is not None and record["status"] == "waiting"
