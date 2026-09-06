import pytest
from bson import ObjectId

from app.db.repositories import serialise, to_object_id
from app.db.repositories.webhook_events import event_hash


def test_object_id_round_trips():
    oid = ObjectId()
    assert to_object_id(str(oid)) == oid
    assert to_object_id(oid) == oid


@pytest.mark.parametrize("bad", ["", "nope", "12345", None])
def test_invalid_object_id_raises_value_error(bad):
    """API layers catch ValueError; a raw bson InvalidId would leak as a 500."""
    with pytest.raises(ValueError):
        to_object_id(bad)


def test_serialise_renames_underscore_id_and_stringifies():
    oid, other = ObjectId(), ObjectId()
    out = serialise({"_id": oid, "campaign_id": other, "name": "AI Tools"})
    assert out == {"id": str(oid), "campaign_id": str(other), "name": "AI Tools"}


def test_serialise_handles_lists_and_nesting():
    a, b = ObjectId(), ObjectId()
    out = serialise({"_id": a, "media_ids": [b], "limits": {"per_day": 500}})
    assert out["media_ids"] == [str(b)]
    assert out["limits"] == {"per_day": 500}


def test_serialise_recurses_into_a_list_of_dicts():
    """`contacts.sources` is a list of dicts holding an ObjectId.

    Stringifying only the bare ObjectIds in a list left one nested inside a dict
    element untouched, and FastAPI's encoder raises on it -- so the first field
    of this shape would have 500ed rather than failed a test.
    """
    contact, rule = ObjectId(), ObjectId()
    out = serialise(
        {"_id": contact, "sources": [{"rule_id": rule, "instagram_media_id": None}]}
    )
    assert out["sources"] == [{"rule_id": str(rule), "instagram_media_id": None}]


def test_serialise_passes_none_through():
    assert serialise(None) is None


def test_event_hash_is_deterministic_for_identical_bytes():
    """A redelivered webhook must collide on the unique index (§25)."""
    body = b'{"object":"instagram","entry":[]}'
    assert event_hash(body) == event_hash(body)


def test_event_hash_differs_for_different_bodies():
    assert event_hash(b'{"a":1}') != event_hash(b'{"a":2}')


# --- contacts: which post a lead came from ----------------------------------


ACCOUNT = ObjectId()
PERSON = "17841400000000001"


async def contacts_repo(database):
    from app.db.repositories import ContactsRepository

    return ContactsRepository(database)


async def capture(repo, *, media_id, rule_id, rule_name="Guide", email="a@example.com"):
    return await repo.capture(
        ACCOUNT,
        PERSON,
        {"instagram_account_id": ACCOUNT, "instagram_user_id": PERSON, "email": email},
        instagram_media_id=media_id,
        rule_id=rule_id,
        rule_name=rule_name,
    )


async def sources_of(database):
    row = await database["contacts"].find_one({"instagram_user_id": PERSON})
    return row["sources"]


async def test_a_first_capture_records_the_post_it_came_from(database):
    repo = await contacts_repo(database)
    rule = ObjectId()

    await capture(repo, media_id="17900000000000001", rule_id=rule)

    (source,) = await sources_of(database)
    assert source["instagram_media_id"] == "17900000000000001"
    assert source["rule_id"] == rule
    assert source["rule_name"] == "Guide"
    assert source["backfilled"] is False
    assert source["captured_at"] is not None


async def test_a_second_post_appends_rather_than_replacing(database):
    """The whole point: a post keeps the leads it earned.

    Overwriting would make a per-post CSV shrink between downloads.
    """
    repo = await contacts_repo(database)

    await capture(repo, media_id="17900000000000001", rule_id=ObjectId())
    await capture(repo, media_id="17900000000000002", rule_id=ObjectId())

    assert [s["instagram_media_id"] for s in await sources_of(database)] == [
        "17900000000000001",
        "17900000000000002",
    ]


async def test_a_repeat_capture_on_one_post_neither_duplicates_nor_raises(database):
    """The guarded-upsert trap. With `upsert=True` the filter matches nothing on
    the repeat, Mongo attempts an insert, and `uq_account_user` rejects it -- so
    the *third* capture would raise DuplicateKeyError, not the second."""
    repo = await contacts_repo(database)
    rule = ObjectId()

    for _ in range(3):
        await capture(repo, media_id="17900000000000001", rule_id=rule)

    assert len(await sources_of(database)) == 1


async def test_two_automations_on_one_post_are_two_sources(database):
    """The entry key is (post, rule). Keying on the post alone would drop one and
    make the CSV's `automation` column a lie."""
    repo = await contacts_repo(database)

    await capture(repo, media_id="17900000000000001", rule_id=ObjectId(), rule_name="Guide")
    await capture(repo, media_id="17900000000000001", rule_id=ObjectId(), rule_name="Waitlist")

    assert sorted(s["rule_name"] for s in await sources_of(database)) == ["Guide", "Waitlist"]


async def test_renaming_an_automation_does_not_mint_a_second_source(database):
    """`rule_name` is denormalised but deliberately outside the guard."""
    repo = await contacts_repo(database)
    rule = ObjectId()

    await capture(repo, media_id="17900000000000001", rule_id=rule, rule_name="Guide")
    await capture(repo, media_id="17900000000000001", rule_id=rule, rule_name="Guide v2")

    sources = await sources_of(database)
    assert len(sources) == 1
    assert sources[0]["rule_name"] == "Guide"


async def test_a_capture_with_no_post_stores_an_explicit_null(database):
    """Story replies and DM-keyword rules have no post. Written as null rather
    than omitted, so the group key and the CSV shape stay unconditional."""
    repo = await contacts_repo(database)

    await capture(repo, media_id=None, rule_id=ObjectId())

    (source,) = await sources_of(database)
    assert source["instagram_media_id"] is None


async def test_a_later_capture_still_refreshes_the_email(database):
    repo = await contacts_repo(database)

    await capture(repo, media_id="17900000000000001", rule_id=ObjectId(), email="old@example.com")
    await capture(repo, media_id="17900000000000002", rule_id=ObjectId(), email="new@example.com")

    row = await database["contacts"].find_one({"instagram_user_id": PERSON})
    assert row["email"] == "new@example.com"


async def test_all_for_account_is_not_capped_at_the_page_size(database):
    """`BaseRepository.list` stops at 50, which silently truncated the export."""
    from app.db.repositories import ContactsRepository
    from app.db.repositories.base import utcnow

    repo = ContactsRepository(database)
    await database["contacts"].insert_many(
        [
            {
                "instagram_account_id": ACCOUNT,
                "instagram_user_id": f"1784140000000{n:04d}",
                "email": f"{n}@example.com",
                "created_at": utcnow(),
                "sources": [],
            }
            for n in range(60)
        ]
    )

    assert len(await repo.list_for_account(ACCOUNT)) == 50
    assert len(await repo.all_for_account(ACCOUNT)) == 60


async def test_media_batch_is_keyed_by_id_and_scoped_to_the_account(database):
    """The account scope is what stops `?post=<foreign id>` naming another
    workspace's post."""
    from app.db.repositories import MediaRepository

    other = ObjectId()
    await database["media"].insert_many(
        [
            {"instagram_account_id": ACCOUNT, "instagram_media_id": "1", "caption": "ours"},
            {"instagram_account_id": other, "instagram_media_id": "2", "caption": "theirs"},
        ]
    )

    found = await MediaRepository(database).by_instagram_media_ids(ACCOUNT, ["1", "2"])

    assert set(found) == {"1"}
    assert found["1"]["caption"] == "ours"
    assert await MediaRepository(database).by_instagram_media_ids(ACCOUNT, []) == {}
