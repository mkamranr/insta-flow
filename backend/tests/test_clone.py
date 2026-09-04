"""Cloning an automation onto other posts.

The feature copies a rule to N posts as N independent rules. "Independent" is the
load-bearing word: each clone gets its own stats, its own send limits and its own
once-per-person ledger, and the source is left exactly as it was.
"""

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.config import get_settings
from app.constants import MAX_CLONE_TARGETS
from app.services.security.encryption import encrypt
from tests.conftest import TEST_PASSWORD

SOURCE_MEDIA = "18000000000000001"
TARGETS = ["18000000000000002", "18000000000000003", "18000000000000004"]


@pytest.fixture
async def account(database, admin):
    settings = get_settings()
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "user_id": admin["_id"],
            "instagram_user_id": "17841400000000000",
            "username": "cloner",
            "status": "connected",
            "access_token_encrypted": encrypt("token", settings.encryption_key),
            "token_expires_at": datetime.now(UTC) + timedelta(days=59),
        }
    )
    for media_id in [SOURCE_MEDIA, *TARGETS]:
        await database["media"].insert_one(
            {
                "_id": ObjectId(),
                "instagram_account_id": account_id,
                "instagram_media_id": media_id,
                "media_type": "IMAGE",
                "published_at": datetime.now(UTC),
            }
        )
    return account_id


@pytest.fixture
async def source_rule(database, account):
    rule_id = ObjectId()
    await database["rules"].insert_one(
        {
            "_id": rule_id,
            "instagram_account_id": account,
            "name": "Send the link",
            "trigger": "comment",
            "scope": "specific",
            "instagram_media_ids": [SOURCE_MEDIA],
            "keywords": ["link"],
            "any_word": False,
            "message": "Here you go, {{username}}.",
            "enabled": True,
            "priority": 100,
            # Fields the model does not declare -- a clone must not inherit these.
            "created_from_upcoming": True,
            "disabled_reason": "story_expired",
        }
    )
    return rule_id


async def test_cloning_creates_one_independent_rule_per_post(signed_in, database, source_rule):
    response = await signed_in.post(
        f"/api/rules/{source_rule}/clone", json={"instagram_media_ids": TARGETS}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["created"]) == 3
    assert body["skipped"] == []

    for media_id in TARGETS:
        rules = await database["rules"].find({"instagram_media_ids": media_id}).to_list(None)
        assert len(rules) == 1
        assert rules[0]["message"] == "Here you go, {{username}}."
        assert rules[0]["keywords"] == ["link"]
        # Exactly one target each, not the source's list plus one.
        assert rules[0]["instagram_media_ids"] == [media_id]


async def test_the_clones_are_distinct_documents(signed_in, database, source_rule):
    """`BaseRepository.insert` mutates the dict it is given and pymongo writes
    `_id` back into it, so a payload reused across the loop would make inserts
    2..N collide on the first clone's id."""
    await signed_in.post(f"/api/rules/{source_rule}/clone", json={"instagram_media_ids": TARGETS})
    ids = {r["_id"] async for r in database["rules"].find({"_id": {"$ne": source_rule}})}
    assert len(ids) == 3


async def test_the_source_rule_is_untouched(signed_in, database, source_rule):
    before = await database["rules"].find_one({"_id": source_rule})
    await signed_in.post(f"/api/rules/{source_rule}/clone", json={"instagram_media_ids": TARGETS})
    assert await database["rules"].find_one({"_id": source_rule}) == before


async def test_a_clone_does_not_inherit_provenance_it_does_not_have(
    signed_in, database, source_rule
):
    """`created_from_upcoming` and `disabled_reason` belong to the source's own
    history. Building the clone through RuleCreate is what drops them."""
    await signed_in.post(
        f"/api/rules/{source_rule}/clone", json={"instagram_media_ids": [TARGETS[0]]}
    )
    clone = await database["rules"].find_one({"instagram_media_ids": TARGETS[0]})
    assert "created_from_upcoming" not in clone
    assert "disabled_reason" not in clone


async def test_an_account_wide_source_is_refused(signed_in, database, source_rule):
    """It already fires on every post. Cloning it would produce rules that shadow
    the original -- equal priority, and `select_rule` breaks the tie on newer _id."""
    await database["rules"].update_one({"_id": source_rule}, {"$set": {"scope": "any"}})
    response = await signed_in.post(
        f"/api/rules/{source_rule}/clone", json={"instagram_media_ids": TARGETS}
    )
    assert response.status_code == 422
    assert "every post" in response.json()["error"]["message"]
    assert await database["rules"].count_documents({}) == 1


@pytest.mark.parametrize("trigger", ["story_reply", "dm_keyword"])
async def test_a_non_comment_source_is_refused(signed_in, database, source_rule, trigger):
    """`list_for_media` filters on trigger, so a cloned story-reply rule's media
    ids would never be read by anything."""
    await database["rules"].update_one({"_id": source_rule}, {"$set": {"trigger": trigger}})
    response = await signed_in.post(
        f"/api/rules/{source_rule}/clone", json={"instagram_media_ids": TARGETS}
    )
    assert response.status_code == 422
    assert await database["rules"].count_documents({}) == 1


async def test_a_post_that_already_has_an_automation_is_skipped_not_failed(
    signed_in, database, account, source_rule
):
    await database["rules"].insert_one(
        {
            "_id": ObjectId(),
            "instagram_account_id": account,
            "trigger": "comment",
            "scope": "specific",
            "instagram_media_ids": [TARGETS[0]],
            "keywords": ["already"],
            "message": "Taken.",
            "enabled": True,
        }
    )

    response = await signed_in.post(
        f"/api/rules/{source_rule}/clone", json={"instagram_media_ids": TARGETS}
    )
    assert response.status_code == 201
    body = response.json()
    # Partial success is the normal case, not an error.
    assert len(body["created"]) == 2
    assert [s["instagram_media_id"] for s in body["skipped"]] == [TARGETS[0]]
    assert await database["rules"].count_documents({"instagram_media_ids": TARGETS[0]}) == 1


async def test_a_paused_rule_still_counts_as_taken(signed_in, database, account, source_rule):
    """A paused rule makes the post read "Automated" in the UI, so the picker must
    not quietly stack a second automation on top of it."""
    await database["rules"].insert_one(
        {
            "_id": ObjectId(),
            "instagram_account_id": account,
            "trigger": "comment",
            "scope": "specific",
            "instagram_media_ids": [TARGETS[0]],
            "keywords": ["paused"],
            "message": "Paused.",
            "enabled": False,
        }
    )
    response = await signed_in.post(
        f"/api/rules/{source_rule}/clone", json={"instagram_media_ids": [TARGETS[0]]}
    )
    assert response.json()["created"] == []
    assert len(response.json()["skipped"]) == 1


async def test_the_source_post_itself_is_skipped(signed_in, source_rule):
    response = await signed_in.post(
        f"/api/rules/{source_rule}/clone",
        json={"instagram_media_ids": [SOURCE_MEDIA, TARGETS[0]]},
    )
    body = response.json()
    assert len(body["created"]) == 1
    assert body["skipped"][0]["instagram_media_id"] == SOURCE_MEDIA


async def test_too_many_targets_is_refused(signed_in, database, source_rule):
    """Bounded because send limits are per rule: N clones authorise N x per_hour
    against Meta's 750/hour account ceiling."""
    many = [f"1800000000000{i:04d}" for i in range(MAX_CLONE_TARGETS + 1)]
    response = await signed_in.post(
        f"/api/rules/{source_rule}/clone", json={"instagram_media_ids": many}
    )
    assert response.status_code == 422
    assert await database["rules"].count_documents({}) == 1


async def test_an_unsynced_target_is_refused(signed_in, source_rule):
    response = await signed_in.post(
        f"/api/rules/{source_rule}/clone", json={"instagram_media_ids": ["18009999999999999"]}
    )
    assert response.status_code == 422
    assert "not synced" in response.json()["error"]["message"]


async def test_one_audit_row_names_the_source(signed_in, database, source_rule):
    """A bulk clone should be one identifiable event, not N indistinguishable
    `rule.created` rows."""
    await signed_in.post(f"/api/rules/{source_rule}/clone", json={"instagram_media_ids": TARGETS})
    rows = await database["audit_logs"].find({"action": "rule.cloned"}).to_list(None)
    assert len(rows) == 1
    assert rows[0]["entity_id"] == source_rule
    assert rows[0]["changes"]["targets"] == "3"
    assert not await database["audit_logs"].count_documents({"action": "rule.created"})


async def test_a_role_without_create_cannot_clone(
    api, database, source_rule, role_factory, user_factory
):
    viewer = await role_factory("Clone viewer", ["automations.view"])
    await user_factory("cloneviewer@example.com", viewer)
    await api.post(
        "/api/auth/login",
        json={"email": "cloneviewer@example.com", "password": TEST_PASSWORD},
    )
    response = await api.post(
        f"/api/rules/{source_rule}/clone", json={"instagram_media_ids": TARGETS}
    )
    assert response.status_code == 403
    assert await database["rules"].count_documents({}) == 1


# --- attachment refcounting -------------------------------------------------
# Cloning made this urgent, but the hazard predates it: the Duplicate button on
# the Automations page copies `attachment` verbatim, so two rules already share
# one upload folder -- and `attachments.delete` rmtree's the whole folder.


@pytest.fixture
async def shared_attachment(database, account, source_rule, tmp_path, monkeypatch):
    """Two rules pointing at one stored file, as Duplicate has always created."""
    from app.services.attachments import store

    monkeypatch.setattr("app.api.rules.upload_root", lambda _settings: tmp_path)
    stored = store(tmp_path, "guide.pdf", b"%PDF-1.4 fake")
    attachment = {
        "type": "file",
        "url": f"http://test/files/{stored.storage_key}",
        "storage_key": stored.storage_key,
        "filename": "guide.pdf",
        "size_bytes": 13,
    }
    await database["rules"].update_one({"_id": source_rule}, {"$set": {"attachment": attachment}})
    sibling_id = ObjectId()
    await database["rules"].insert_one(
        {
            "_id": sibling_id,
            "instagram_account_id": account,
            "trigger": "comment",
            "scope": "specific",
            "instagram_media_ids": [TARGETS[0]],
            "keywords": ["copy"],
            "message": "A duplicate.",
            "enabled": True,
            "attachment": attachment,
        }
    )
    return {"key": stored.storage_key, "sibling": sibling_id, "root": tmp_path}


def _folder(shared):
    return shared["root"] / shared["key"].split("/")[0]


async def test_deleting_one_of_two_sharing_rules_keeps_the_file(
    signed_in, shared_attachment, source_rule
):
    assert _folder(shared_attachment).is_dir()

    assert (await signed_in.delete(f"/api/rules/{source_rule}")).status_code == 204

    # The survivor's attachment URL must still resolve. Before refcounting this
    # folder was rmtree'd and the recipient silently got the text and no file.
    assert _folder(shared_attachment).is_dir()


async def test_deleting_the_last_referencing_rule_removes_the_file(
    signed_in, shared_attachment, source_rule
):
    await signed_in.delete(f"/api/rules/{source_rule}")
    await signed_in.delete(f"/api/rules/{shared_attachment['sibling']}")
    # The refcount must not leak files once nobody points at them.
    assert not _folder(shared_attachment).exists()


async def test_replacing_one_rules_attachment_keeps_a_siblings_file(
    signed_in, shared_attachment, source_rule
):
    """`update_rule`'s delete path, not just `delete_rule`'s."""
    response = await signed_in.patch(
        f"/api/rules/{source_rule}", json={"attachment": None}
    )
    assert response.status_code == 200
    assert _folder(shared_attachment).is_dir()
