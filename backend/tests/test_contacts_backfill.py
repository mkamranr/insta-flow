"""Reconstructing a source post for contacts captured before we recorded one.

This is the one part of the feature that infers rather than observes, so the
tests are mostly about the boundaries of the inference: what it refuses to
attribute, and what it admits it does not know.
"""

from datetime import UTC, datetime

from bson import ObjectId

from app.db.bootstrap import prepare
from app.db.repositories import (
    CommentsRepository,
    ContactsRepository,
    ExecutionsRepository,
    RulesRepository,
)

ACCOUNT = ObjectId()
PERSON = "17800000000000000"
MEDIA = "17890000000000000"


async def run_backfill(database):
    return await ContactsRepository(database).backfill_sources(
        ExecutionsRepository(database),
        CommentsRepository(database),
        RulesRepository(database),
    )


async def a_rule(database, *, gated=True, name="Email gate"):
    return (
        await database["rules"].insert_one(
            {
                "instagram_account_id": ACCOUNT,
                "name": name,
                "collect_email": {"enabled": gated},
            }
        )
    ).inserted_id


async def a_legacy_contact(database, *, person=PERSON, source_rule_id=None):
    """A contact as written before `sources` existed: no such field at all."""
    return (
        await database["contacts"].insert_one(
            {
                "instagram_account_id": ACCOUNT,
                "instagram_user_id": person,
                "email": f"{person}@example.com",
                "source_rule_id": source_rule_id,
                "created_at": datetime.now(UTC),
            }
        )
    ).inserted_id


async def an_execution(database, rule_id, *, person=PERSON, media_id=MEDIA, when=None):
    comment_id = None
    if media_id is not None:
        comment_id = (
            await database["comments"].insert_one(
                {
                    "instagram_account_id": ACCOUNT,
                    "instagram_comment_id": f"c-{ObjectId()}",
                    "instagram_media_id": media_id,
                }
            )
        ).inserted_id
    row = {
        "instagram_account_id": ACCOUNT,
        "rule_id": rule_id,
        "instagram_user_id": person,
        "action": "private_reply",
        "status": "success",
        "created_at": when or datetime.now(UTC),
    }
    if comment_id is not None:
        row["comment_id"] = comment_id
    else:
        row["trigger_message_id"] = f"mid-{ObjectId()}"
    await database["executions"].insert_one(row)


async def sources_of(database, person=PERSON):
    return (await database["contacts"].find_one({"instagram_user_id": person}))["sources"]


async def test_a_lead_is_traced_back_through_its_execution_to_the_post(database):
    rule = await a_rule(database)
    await a_legacy_contact(database)
    await an_execution(database, rule)

    assert await run_backfill(database) == 1

    (source,) = await sources_of(database)
    assert source["instagram_media_id"] == MEDIA
    assert source["rule_id"] == rule
    assert source["rule_name"] == "Email gate"


async def test_every_reconstructed_entry_says_it_was_reconstructed(database):
    """Nothing on an execution records "this one produced the email". The flag is
    the difference between a recorded fact and an inference."""
    rule = await a_rule(database)
    await a_legacy_contact(database)
    await an_execution(database, rule)

    await run_backfill(database)

    assert (await sources_of(database))[0]["backfilled"] is True


async def test_two_posts_give_two_sources(database):
    rule = await a_rule(database)
    await a_legacy_contact(database)
    await an_execution(database, rule, media_id=MEDIA)
    await an_execution(database, rule, media_id="17890000000000999")

    await run_backfill(database)

    assert sorted(s["instagram_media_id"] for s in await sources_of(database)) == [
        MEDIA,
        "17890000000000999",
    ]


async def test_an_automation_without_the_gate_is_never_attributed(database):
    """Otherwise someone who gave their address once appears under every post
    they ever commented on, presented as fact."""
    await a_legacy_contact(database)
    await an_execution(database, await a_rule(database, gated=False))

    await run_backfill(database)

    (source,) = await sources_of(database)
    assert source["instagram_media_id"] is None


async def test_a_story_reply_resolves_to_no_post(database):
    """No comment behind it, so no post -- and we say so rather than guessing."""
    rule = await a_rule(database)
    await a_legacy_contact(database)
    await an_execution(database, rule, media_id=None)

    await run_backfill(database)

    (source,) = await sources_of(database)
    assert source["instagram_media_id"] is None


async def test_a_contact_with_no_executions_at_all_still_gets_an_entry(database):
    """Never `[]`: the watermark is `sources` not existing, so an empty list
    would make the job re-run over this row on every startup, forever."""
    await a_legacy_contact(database)

    await run_backfill(database)

    assert len(await sources_of(database)) == 1


async def test_a_contact_that_already_has_sources_is_left_alone(database):
    rule = await a_rule(database)
    await database["contacts"].insert_one(
        {
            "instagram_account_id": ACCOUNT,
            "instagram_user_id": PERSON,
            "email": "a@example.com",
            "created_at": datetime.now(UTC),
            "sources": [
                {
                    "instagram_media_id": "already-here",
                    "rule_id": rule,
                    "rule_name": "Email gate",
                    "captured_at": datetime.now(UTC),
                    "backfilled": False,
                }
            ],
        }
    )
    await an_execution(database, rule)

    assert await run_backfill(database) == 0
    assert [s["instagram_media_id"] for s in await sources_of(database)] == ["already-here"]


async def test_running_it_twice_changes_nothing(database):
    rule = await a_rule(database)
    await a_legacy_contact(database)
    await an_execution(database, rule)

    assert await run_backfill(database) == 1
    before = await sources_of(database)
    assert await run_backfill(database) == 0
    assert await sources_of(database) == before


async def test_startup_runs_it(database):
    """`prepare()` is the only caller in production, and four callers share it."""
    rule = await a_rule(database)
    await a_legacy_contact(database)
    await an_execution(database, rule)

    await prepare(database)

    assert (await sources_of(database))[0]["instagram_media_id"] == MEDIA


async def test_nothing_to_do_costs_one_query(database):
    """It runs inside every test's `database` fixture; it has to be free."""
    assert await run_backfill(database) == 0
