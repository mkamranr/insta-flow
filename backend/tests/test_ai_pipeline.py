"""AI in the worker: a fallback, never a replacement (§56)."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from bson import ObjectId

from app.config import Settings
from app.services.ai.client import AiClient
from app.services.meta import MockInstagramClient
from app.services.meta.webhooks import sign
from app.services.security.encryption import encrypt
from app.workers.tasks import process_comment_event

ACCOUNT_IG_ID = "17841400000000000"
MEDIA_ID = "17890000000000000"
COMMENT_ID = "17900000000000000"
SECRET = "webhook-test-secret"


class CountingModel:
    """Records how many times the model was actually consulted."""

    def __init__(self, intent="request_link", confidence=0.95):
        self.calls = 0
        self.intent = intent
        self.confidence = confidence

    def handler(self, request):
        self.calls += 1
        content = json.dumps({"intent": self.intent, "confidence": self.confidence})
        return httpx.Response(
            200, json={"model": "test-model", "choices": [{"message": {"content": content}}]}
        )

    def client(self):
        return AiClient(
            "http://model/v1",
            "test-model",
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )


def payload(text: str):
    return {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_IG_ID,
                "time": int(datetime.now(UTC).timestamp()),
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "from": {"id": "commenter", "username": "john123"},
                            "comment_id": COMMENT_ID,
                            "text": text,
                            "media": {"id": MEDIA_ID, "media_product_type": "REELS"},
                        },
                    }
                ],
            }
        ],
    }


async def store_event(database, body_payload):
    raw = json.dumps(body_payload).encode()
    result = await database["webhook_events"].insert_one(
        {
            "event_hash": sign(raw, SECRET),
            "event_type": "comments",
            "payload": body_payload,
            "status": "received",
            "received_at": datetime.now(UTC),
        }
    )
    return str(result.inserted_id)


@pytest.fixture
async def seeded(database):
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "instagram_user_id": ACCOUNT_IG_ID,
            "username": "myshop",
            "status": "connected",
            "access_token_encrypted": encrypt("tok", "k" * 64),
        }
    )
    await database["media"].insert_one(
        {"instagram_account_id": account_id, "instagram_media_id": MEDIA_ID, "caption": "post"}
    )
    rule_id = (
        await database["rules"].insert_one(
            {
                "instagram_account_id": account_id,
                "name": "Link rule",
                "scope": "specific",
                "instagram_media_ids": [MEDIA_ID],
                "keywords": ["link"],
                "any_word": False,
                "match_mode": "contains",
                "message": "Here's the link!",
                "buttons": [],
                "ai": {"enabled": True, "intents": ["request_link"], "min_confidence": None},
                "once_per_person": False,
                "enabled": True,
                "priority": 100,
                "limits": {"per_minute": 10, "per_hour": 100, "per_day": 500},
            }
        )
    ).inserted_id
    return {"account_id": account_id, "rule_id": rule_id}


def ctx(database, model=None, *, ai_enabled=True):
    return {
        "db": database,
        "settings": Settings(
            dry_run=True,
            ai_enabled=ai_enabled,
            ai_min_confidence=0.7,
            encryption_key="k" * 64,
            session_secret="s" * 64,
        ),
        "client": MockInstagramClient(),
        "ai_client": model.client() if model else None,
        "job_try": 1,
    }


async def test_a_keyword_match_never_consults_the_model(database, seeded):
    """Deterministic first: the model is slower, costlier and less predictable."""
    model = CountingModel()
    event_id = await store_event(database, payload("send me the link"))

    result = await process_comment_event(ctx(database, model), event_id, ACCOUNT_IG_ID, COMMENT_ID)

    assert result["status"] == "dry_run"
    assert model.calls == 0
    assert await database["ai_classifications"].count_documents({}) == 0


async def test_ai_matches_a_comment_no_keyword_caught(database, seeded):
    model = CountingModel(intent="request_link", confidence=0.95)
    event_id = await store_event(database, payload("where can I find that thing?"))

    result = await process_comment_event(ctx(database, model), event_id, ACCOUNT_IG_ID, COMMENT_ID)

    assert result["status"] == "dry_run"
    assert model.calls == 1


async def test_low_confidence_leaves_the_comment_unmatched(database, seeded):
    model = CountingModel(intent="request_link", confidence=0.3)
    event_id = await store_event(database, payload("hmm not sure"))

    result = await process_comment_event(ctx(database, model), event_id, ACCOUNT_IG_ID, COMMENT_ID)
    assert result["status"] == "unmatched"


async def test_an_unrelated_intent_leaves_the_comment_unmatched(database, seeded):
    model = CountingModel(intent="negative_feedback", confidence=0.99)
    event_id = await store_event(database, payload("this is rubbish"))

    result = await process_comment_event(ctx(database, model), event_id, ACCOUNT_IG_ID, COMMENT_ID)
    assert result["status"] == "unmatched"


async def test_the_model_is_not_called_when_ai_is_disabled_globally(database, seeded):
    model = CountingModel()
    event_id = await store_event(database, payload("where do I get it"))

    result = await process_comment_event(
        ctx(database, model, ai_enabled=False), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )
    assert result["status"] == "unmatched"
    assert model.calls == 0


async def test_the_model_is_not_called_when_no_rule_wants_it(database, seeded):
    """Classification costs time; it must not run speculatively."""
    await database["rules"].update_one(
        {"_id": seeded["rule_id"]}, {"$set": {"ai.enabled": False}}
    )
    model = CountingModel()
    event_id = await store_event(database, payload("where do I get it"))

    result = await process_comment_event(ctx(database, model), event_id, ACCOUNT_IG_ID, COMMENT_ID)
    assert result["status"] == "unmatched"
    assert model.calls == 0


async def test_every_classification_is_recorded(database, seeded):
    """Including unusable ones, so a rule that never fires can be explained."""
    model = CountingModel(intent="spam", confidence=0.2)
    event_id = await store_event(database, payload("buy followers cheap"))

    await process_comment_event(ctx(database, model), event_id, ACCOUNT_IG_ID, COMMENT_ID)

    record = await database["ai_classifications"].find_one({})
    assert record["intent"] == "spam"
    assert record["confidence"] == 0.2
    assert record["model"] == "test-model"
    assert record["latency_ms"] >= 0


async def test_an_unreachable_model_does_not_break_the_pipeline(database, seeded):
    """AI is optional; its failure must never stop the deterministic path."""

    class Broken(CountingModel):
        def handler(self, request):
            self.calls += 1
            raise httpx.ConnectError("no route", request=request)

    model = Broken()
    event_id = await store_event(database, payload("where do I get it"))

    result = await process_comment_event(ctx(database, model), event_id, ACCOUNT_IG_ID, COMMENT_ID)
    assert result["status"] == "unmatched"
    assert model.calls == 1


async def test_a_keyword_match_still_works_when_the_model_is_broken(database, seeded):
    class Broken(CountingModel):
        def handler(self, request):
            raise httpx.ConnectError("no route", request=request)

    event_id = await store_event(database, payload("send me the link"))
    result = await process_comment_event(
        ctx(database, Broken()), event_id, ACCOUNT_IG_ID, COMMENT_ID
    )
    assert result["status"] == "dry_run"
