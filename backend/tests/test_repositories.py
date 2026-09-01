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


def test_serialise_passes_none_through():
    assert serialise(None) is None


def test_event_hash_is_deterministic_for_identical_bytes():
    """A redelivered webhook must collide on the unique index (§25)."""
    body = b'{"object":"instagram","entry":[]}'
    assert event_hash(body) == event_hash(body)


def test_event_hash_differs_for_different_bodies():
    assert event_hash(b'{"a":1}') != event_hash(b'{"a":2}')
