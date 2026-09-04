"""Per-story automations, and what happens when the story expires.

A story lives 24 hours. The rule that targets it must stop matching, and -- when
the operator asked for it -- disable itself, rather than sit enabled and quietly
unmatchable.

The expiry signal is deliberately *observational*: Meta returns only active
stories, so anything stored and no longer listed has gone. Counting 24 hours
from `timestamp` is not an option, because Meta omits that field for video
stories (docs/meta-api.md §4d).
"""

import json
from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.config import Settings
from app.db.repositories import RulesRepository, StoriesRepository
from app.services.meta import MockInstagramClient
from app.services.meta.client import MetaMedia
from app.services.security.encryption import encrypt
from app.workers.tasks import process_message_event, sync_stories

ACCOUNT_IG_ID = "17841439340431612"
SENDER_ID = "27962242703427275"
KEY = "k" * 64
STORY_A = "18114310274047199"
STORY_B = "18112752874804936"


def settings_for(dry_run: bool = False) -> Settings:
    return Settings(
        dry_run=dry_run,
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


def story(story_id: str, video: bool = False) -> MetaMedia:
    """A video story deliberately carries no timestamp or permalink -- that is
    what Meta returns, and the code must not depend on them."""
    return MetaMedia(
        instagram_media_id=story_id,
        media_type="VIDEO" if video else "IMAGE",
        media_product_type="STORY",
        caption=None if video else "Working on something new...",
        permalink=None if video else f"https://instagram.com/stories/x/{story_id}",
        published_at=None if video else datetime.now(UTC),
    )


async def add_story_rule(database, account_id, story_ids, **overrides):
    doc = {
        "instagram_account_id": account_id,
        "name": "Story rule",
        "trigger": "story_reply",
        "scope": "any",
        "instagram_media_ids": [],
        "instagram_story_ids": story_ids,
        "expire_with_story": True,
        "keywords": ["link"],
        "message": "Here it is: https://example.com/thing",
        "buttons": [],
        "opening_dm": {"enabled": False, "message": "", "button_text": ""},
        "require_follow": {"enabled": False, "prompt": ""},
        "collect_email": {"enabled": False, "prompt": ""},
        "once_per_person": False,
        "enabled": True,
        "priority": 100,
        "limits": {"per_minute": 10, "per_hour": 100, "per_day": 500},
        **overrides,
    }
    return (await database["rules"].insert_one(doc)).inserted_id


async def reply_to_story(database, client, account_ig_id, story_id, text="link", mid="mid_1"):
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": account_ig_id,
                "time": int(datetime.now(UTC).timestamp() * 1000),
                "messaging": [
                    {
                        "sender": {"id": SENDER_ID},
                        "recipient": {"id": account_ig_id},
                        "timestamp": int(datetime.now(UTC).timestamp() * 1000),
                        "message": {
                            "mid": mid,
                            "text": text,
                            "reply_to": {"story": {"id": story_id, "url": "https://x/s.jpg"}},
                        },
                    }
                ],
            }
        ],
    }
    event_id = (
        await database["webhook_events"].insert_one(
            {
                "object": "instagram",
                "event_hash": str(ObjectId()),
                "raw_body": json.dumps(payload),
                "payload": payload,
                "processed": False,
            }
        )
    ).inserted_id
    ctx = {"db": database, "settings": settings_for(), "client": client, "job_try": 1}
    return await process_message_event(
        ctx, str(event_id), account_ig_id, SENDER_ID, text, mid
    )


def ctx_for(database, client):
    return {"db": database, "settings": settings_for(), "client": client, "job_try": 1}


# -- targeting -----------------------------------------------------------------


async def test_a_rule_pinned_to_a_story_fires_on_that_story(database, client, account):
    await add_story_rule(database, account, [STORY_A])

    result = await reply_to_story(database, client, ACCOUNT_IG_ID, STORY_A)

    assert result["status"] == "success"


async def test_a_rule_pinned_to_a_story_ignores_a_different_story(database, client, account):
    """The whole point of per-story targeting."""
    await add_story_rule(database, account, [STORY_A])

    result = await reply_to_story(database, client, ACCOUNT_IG_ID, STORY_B)

    assert result["status"] == "unmatched"
    assert client.sent_direct_messages == []


async def test_an_untargeted_rule_still_answers_every_story(database, client, account):
    """Empty means every story -- the default, and what account-wide rules rely on."""
    await add_story_rule(database, account, [])

    assert (await reply_to_story(database, client, ACCOUNT_IG_ID, STORY_A))["status"] == "success"
    assert (
        await reply_to_story(database, client, ACCOUNT_IG_ID, STORY_B, mid="mid_2")
    )["status"] == "success"


async def test_a_rule_written_before_per_story_targeting_still_matches(database, client, account):
    """Existing story rules have no `instagram_story_ids` key at all. They must
    keep answering every story rather than silently matching none."""
    rule_id = await add_story_rule(database, account, [])
    await database["rules"].update_one(
        {"_id": rule_id}, {"$unset": {"instagram_story_ids": "", "expire_with_story": ""}}
    )

    result = await reply_to_story(database, client, ACCOUNT_IG_ID, STORY_A)

    assert result["status"] == "success"


# -- expiry --------------------------------------------------------------------


async def test_sync_records_active_stories(database, client, account):
    client.stories = [story(STORY_A), story(STORY_B, video=True)]

    result = await sync_stories(ctx_for(database, client))

    assert result["active"] == 2
    stored = await StoriesRepository(database).list_active(account)
    assert {s["instagram_story_id"] for s in stored} == {STORY_A, STORY_B}


async def test_a_story_meta_stops_listing_is_expired(database, client, account):
    """Expiry is observed, not computed -- a video story has no timestamp to
    count 24 hours from."""
    client.stories = [story(STORY_A), story(STORY_B, video=True)]
    await sync_stories(ctx_for(database, client))

    client.stories = [story(STORY_A)]
    result = await sync_stories(ctx_for(database, client))

    assert result["expired"] == 1
    active = await StoriesRepository(database).list_active(account)
    assert [s["instagram_story_id"] for s in active] == [STORY_A]


async def test_the_rule_is_disabled_when_its_story_expires(database, client, account):
    """The behaviour asked for: the rule switches itself off, rather than staying
    enabled and matching nothing."""
    rule_id = await add_story_rule(database, account, [STORY_A])
    client.stories = [story(STORY_A)]
    await sync_stories(ctx_for(database, client))

    client.stories = []
    result = await sync_stories(ctx_for(database, client))

    assert result["rules_disabled"] == 1
    rule = await database["rules"].find_one({"_id": rule_id})
    assert rule["enabled"] is False
    assert rule["disabled_reason"] == "story_expired"


async def test_a_rule_survives_while_any_of_its_stories_is_live(database, client, account):
    """A rule on three stories must not die with the first one."""
    rule_id = await add_story_rule(database, account, [STORY_A, STORY_B])
    client.stories = [story(STORY_A), story(STORY_B)]
    await sync_stories(ctx_for(database, client))

    client.stories = [story(STORY_B)]
    await sync_stories(ctx_for(database, client))

    assert (await database["rules"].find_one({"_id": rule_id}))["enabled"] is True


async def test_opting_out_keeps_the_rule_enabled(database, client, account):
    rule_id = await add_story_rule(database, account, [STORY_A], expire_with_story=False)
    client.stories = [story(STORY_A)]
    await sync_stories(ctx_for(database, client))

    client.stories = []
    await sync_stories(ctx_for(database, client))

    assert (await database["rules"].find_one({"_id": rule_id}))["enabled"] is True


async def test_an_account_wide_story_rule_is_never_disabled(database, client, account):
    """It targets no story, so no story expiring can invalidate it."""
    rule_id = await add_story_rule(database, account, [])
    client.stories = [story(STORY_A)]
    await sync_stories(ctx_for(database, client))

    client.stories = []
    await sync_stories(ctx_for(database, client))

    assert (await database["rules"].find_one({"_id": rule_id}))["enabled"] is True


async def test_a_failed_fetch_expires_nothing(database, client, account):
    """The dangerous case. A refused call and "all your stories ended" look
    identical if the error is swallowed -- and would disable every story rule."""
    from app.errors import MetaAPIError

    rule_id = await add_story_rule(database, account, [STORY_A])
    client.stories = [story(STORY_A)]
    await sync_stories(ctx_for(database, client))

    client.fail("get_stories", MetaAPIError("token expired", status_code=401, retryable=False))
    result = await sync_stories(ctx_for(database, client))

    assert result["expired"] == 0
    assert (await database["rules"].find_one({"_id": rule_id}))["enabled"] is True
    assert len(await StoriesRepository(database).list_active(account)) == 1


async def test_a_disabled_rule_stops_matching(database, client, account):
    await add_story_rule(database, account, [STORY_A])
    client.stories = [story(STORY_A)]
    await sync_stories(ctx_for(database, client))
    client.stories = []
    await sync_stories(ctx_for(database, client))

    result = await reply_to_story(database, client, ACCOUNT_IG_ID, STORY_A)

    assert result["status"] == "unmatched"
    assert client.sent_direct_messages == []


async def test_expiring_and_reappearing_does_not_duplicate_the_story(database, client, account):
    """Upsert clears `expired_at`, so a story listed again is live again rather
    than a second row the picker would show twice."""
    client.stories = [story(STORY_A)]
    await sync_stories(ctx_for(database, client))
    client.stories = []
    await sync_stories(ctx_for(database, client))
    client.stories = [story(STORY_A)]
    await sync_stories(ctx_for(database, client))

    assert await database["stories"].count_documents({}) == 1
    assert len(await StoriesRepository(database).list_active(account)) == 1


async def test_counts_by_story_powers_the_picker(database, client, account):
    await add_story_rule(database, account, [STORY_A])
    await add_story_rule(database, account, [STORY_A, STORY_B])

    counts = await RulesRepository(database).counts_by_story(account)

    assert counts == {STORY_A: 2, STORY_B: 1}


# -- media_url: the field a photo post's thumbnail actually lives in ----------


def test_thumbnail_url_is_video_only_so_media_url_is_requested():
    """Meta returns `thumbnail_url` for VIDEO only; IMAGE and CAROUSEL_ALBUM
    carry `media_url`. Asking for only the first leaves every photo blank."""
    from app.services.meta.http_client import MEDIA_FIELDS, STORY_FIELDS

    assert "media_url" in MEDIA_FIELDS
    assert "thumbnail_url" in MEDIA_FIELDS
    assert "media_url" in STORY_FIELDS


def test_media_url_survives_the_mapper():
    from app.services.meta.http_client import _media_from

    item = _media_from(
        {"id": "1", "media_type": "CAROUSEL_ALBUM", "media_url": "https://cdn/x.jpg"}
    )

    assert item.media_url == "https://cdn/x.jpg"
    assert item.thumbnail_url is None
