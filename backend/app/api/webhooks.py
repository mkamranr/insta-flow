"""Meta webhook endpoints.

The hot path (§21): read the raw body, verify the signature, deduplicate,
persist, enqueue, return. It must be fast and must never call Meta — a slow
response makes Meta redeliver, amplifying load exactly when the system is
already struggling.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from app import queue
from app.api.deps import SettingsDep, WebhookEventsRepo
from app.errors import WebhookValidationError
from app.services.meta.webhooks import (
    SIGNATURE_HEADER,
    parse_comment_events,
    parse_message_events,
    parse_postback_events,
    verify_signature,
    verify_subscription,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

PROCESS_COMMENT_TASK = "process_comment_event"
PROCESS_POSTBACK_TASK = "process_postback_event"
PROCESS_MESSAGE_TASK = "process_message_event"


@router.get("/instagram", response_class=PlainTextResponse)
async def verify(request: Request, settings: SettingsDep) -> Response:
    """Meta's subscription handshake (§22).

    Returns the challenge verbatim as text/plain. Any other content type or a
    JSON-quoted body fails verification in the dashboard.
    """
    params = request.query_params
    challenge = verify_subscription(
        params.get("hub.mode"),
        params.get("hub.verify_token"),
        params.get("hub.challenge"),
        settings.meta_webhook_verify_token,
    )
    if challenge is None:
        logger.warning("webhook_verification_rejected", extra={"mode": params.get("hub.mode")})
        raise WebhookValidationError("Webhook verification failed.")

    logger.info("webhook_verification_succeeded")
    return PlainTextResponse(challenge)


@router.post("/instagram")
async def receive(
    request: Request, settings: SettingsDep, events: WebhookEventsRepo
) -> dict[str, Any]:
    # Raw bytes first: the signature covers exactly what was sent, and
    # re-serialising the parsed JSON would produce a different digest.
    raw_body = await request.body()

    signature = request.headers.get(SIGNATURE_HEADER)
    if not verify_signature(raw_body, signature, settings.meta_app_secret):
        logger.warning("webhook_signature_invalid", extra={"bytes": len(raw_body)})
        raise WebhookValidationError("Invalid webhook signature.")

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Signed but unparseable. Record and acknowledge: returning an error
        # would make Meta redeliver a payload that can never succeed.
        logger.warning("webhook_payload_unparseable")
        return {"status": "ignored", "reason": "unparseable"}

    record, is_new = await events.record(raw_body=raw_body, payload=payload, event_type="comments")
    if not is_new:
        logger.info("webhook_duplicate_ignored", extra={"event_id": str(record["_id"])})
        return {"status": "duplicate", "queued": 0}

    postbacks = [p for p in parse_postback_events(payload) if p.is_ours]
    # Echoes are our own sends coming back; acting on them would loop. `is_self`
    # is the second guard: echo filtering rests on one field Meta controls, and
    # a message-id reservation would NOT catch a loop, because every echo has a
    # distinct mid and would earn its own valid reservation.
    messages = [
        m for m in parse_message_events(payload) if not m.is_echo and not m.is_self and m.text
    ]

    # Any messaging event we recognised but cannot act on. Logged by shape only,
    # so that when a story reaction finally arrives we have its real structure
    # rather than a guess -- Meta does not document how they are delivered.
    for entry in payload.get("entry") or []:
        for event in (entry or {}).get("messaging") or []:
            if isinstance(event, dict) and "message" not in event and "postback" not in event:
                logger.info(
                    "webhook_messaging_event_unhandled",
                    extra={"keys": ",".join(sorted(event.keys()))},
                )
    comments = parse_comment_events(payload)

    if not comments and not postbacks and not messages:
        await events.mark_processed(record["_id"])
        logger.info("webhook_no_comment_events")
        return {"status": "ignored", "reason": "no comment events", "queued": 0}

    queued = 0
    for postback in postbacks:
        try:
            await queue.enqueue(
                PROCESS_POSTBACK_TASK,
                str(record["_id"]),
                postback.instagram_account_id,
                postback.sender_id,
                postback.payload,
            )
            queued += 1
        except Exception:
            logger.exception("webhook_postback_enqueue_failed")

    for message in messages:
        try:
            await queue.enqueue(
                PROCESS_MESSAGE_TASK,
                str(record["_id"]),
                message.instagram_account_id,
                message.sender_id,
                message.text,
                message.mid,
            )
            queued += 1
        except Exception:
            logger.exception("webhook_message_enqueue_failed")

    for comment in comments:
        if comment.is_self_comment:
            # The account's own comments fire this webhook too; replying would
            # trigger on our own replies (§5.6).
            logger.info(
                "webhook_self_comment_skipped",
                extra={"comment_id": comment.instagram_comment_id},
            )
            continue
        try:
            await queue.enqueue(
                PROCESS_COMMENT_TASK,
                str(record["_id"]),
                comment.instagram_account_id,
                comment.instagram_comment_id,
            )
            queued += 1
        except Exception:
            # The event is already durably stored, so a queue outage loses
            # nothing permanently -- it can be replayed from webhook_events.
            logger.exception(
                "webhook_enqueue_failed",
                extra={"comment_id": comment.instagram_comment_id},
            )

    if queued == 0:
        await events.mark_processed(record["_id"])

    logger.info(
        "webhook_received",
        extra={
            "event_id": str(record["_id"]),
            "comments": len(comments),
            "postbacks": len(postbacks),
            "messages": len(messages),
            "queued": queued,
        },
    )
    return {"status": "accepted", "queued": queued}
