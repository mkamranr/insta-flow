"""ARQ task definitions.

`process_comment_event` is the whole cold path: webhook event -> comment ->
rule -> policy -> execution reservation -> render -> Meta. It is the only
place a private reply is ever sent.
"""

import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from arq.worker import Retry

from app.db.repositories import (
    AiClassificationsRepository,
    CommentsRepository,
    ContactsRepository,
    ExecutionsRepository,
    InstagramAccountsRepository,
    LinkClicksRepository,
    MediaRepository,
    RulesRepository,
    WebhookEventsRepository,
)
from app.errors import MetaAPIError, MetaTokenError, TemplateError
from app.services.ai.classifier import classify
from app.services.campaigns import (
    ACTION_PRIVATE_REPLY,
    CommentContext,
    PolicyContext,
    build_render_context,
    evaluate,
    normalize_for_search,
    render,
    rules_wanting_ai,
    select_rule,
    select_rule_by_intent,
)
from app.services.campaigns.policy import DRY_RUN
from app.services.contacts import extract_email
from app.services.links import is_safe_target, new_token, tracking_url
from app.services.meta import auth as meta_auth
from app.services.meta.messages import postback_button, web_url_button
from app.services.meta.tokens import needs_refresh, token_fields
from app.services.meta.webhooks import build_postback_payload, parse_comment_events
from app.services.security.encryption import EncryptionError, decrypt

logger = logging.getLogger(__name__)

# §64. Attempt 1 is immediate; later attempts back off. Jitter spreads retries so
# a burst of failures does not resynchronise into a thundering herd.
RETRY_DELAYS = (0, 2, 5, 15, 60)
MAX_ATTEMPTS = len(RETRY_DELAYS)

TERMINAL_STATUSES = ("success", "dry_run", "failed", "skipped")

ACTION_OPENING_DM = "opening_dm"
ACTION_PUBLIC_REPLY = "public_reply"


async def tracked_buttons(
    db: Any,
    settings: Any,
    rule: dict[str, Any],
    account: dict[str, Any],
    execution_id: Any,
    recipient_id: str,
) -> list[dict[str, str]]:
    """Rewrite each button through our redirect so clicks are attributable.

    Without this there is no honest click-through metric (§59). If PUBLIC_BASE_URL
    is not HTTPS the original URL is sent unchanged -- Meta rejects non-HTTPS
    web_url buttons, so a broken tracking link would lose the message entirely,
    which is worse than losing the metric.
    """
    links = LinkClicksRepository(db)
    trackable = settings.public_base_url.lower().startswith("https://")

    buttons: list[dict[str, str]] = []
    for button in rule.get("buttons") or []:
        url = str(button.get("url", ""))
        title = str(button.get("title") or "Open")
        if not url or not is_safe_target(url):
            continue

        if not trackable:
            buttons.append(web_url_button(title, url))
            continue

        token = new_token()
        await links.insert(
            {
                "token": token,
                "instagram_account_id": account["_id"],
                "rule_id": rule["_id"],
                "execution_id": execution_id,
                "instagram_user_id": recipient_id,
                "target_url": url,
                "button_title": title,
                "click_count": 0,
                "first_clicked_at": None,
                "last_clicked_at": None,
            }
        )
        buttons.append(web_url_button(title, tracking_url(settings.public_base_url, token)))

    return buttons


def uses_opening_dm(rule: dict[str, Any]) -> bool:
    opening = rule.get("opening_dm") or {}
    return bool(opening.get("enabled")) and bool(str(opening.get("message", "")).strip())


def retry_delay(attempt: int) -> float:
    base = RETRY_DELAYS[min(max(attempt, 1) - 1, len(RETRY_DELAYS) - 1)]
    return base + random.uniform(0, max(base * 0.25, 0.5))  # noqa: S311


async def ping(ctx: dict[str, Any], note: str = "") -> dict[str, str]:
    """Round-trip probe. Proves the queue is wired without touching Meta."""
    logger.info("worker_ping", extra={"note": note, "job_id": ctx.get("job_id")})
    return {"pong": datetime.now(UTC).isoformat(), "note": note}


async def process_comment_event(
    ctx: dict[str, Any],
    webhook_event_id: str,
    instagram_account_id: str,
    instagram_comment_id: str,
) -> dict[str, Any]:
    db = ctx["db"]
    settings = ctx["settings"]
    client = ctx["client"]
    attempt = int(ctx.get("job_try", 1))

    log = {
        "instagram_comment_id": instagram_comment_id,
        "instagram_account_id": instagram_account_id,
        "attempt": attempt,
    }

    events = WebhookEventsRepository(db)
    event = await events.get(webhook_event_id)
    if event is None:
        logger.warning("webhook_event_missing", extra=log)
        return {"status": "skipped", "reason": "event_missing"}

    accounts = InstagramAccountsRepository(db)
    account = await accounts.get_by_instagram_user_id(instagram_account_id)
    if account is None:
        # Meta's dashboard test payload uses entry.id "0", so this is the normal
        # outcome for a test rather than a fault. Mark it done either way: an
        # event nothing can act on must not sit at "received" forever, looking
        # like a backlog.
        logger.warning("comment_for_unknown_account", extra=log)
        await events.mark_processed(event["_id"], "No connected account for this Instagram id.")
        return {"status": "skipped", "reason": "unknown_account"}

    parsed = next(
        (
            c
            for c in parse_comment_events(event.get("payload") or {})
            if c.instagram_comment_id == instagram_comment_id
        ),
        None,
    )
    if parsed is None:
        logger.warning("comment_not_in_payload", extra=log)
        await events.mark_processed(event["_id"])
        return {"status": "skipped", "reason": "not_in_payload"}

    if parsed.is_from(account):
        # Caught at the webhook too; re-checked here because only now do we know
        # the account's username (§5.6).
        logger.info("self_comment_skipped", extra=log)
        await events.mark_processed(event["_id"])
        return {"status": "skipped", "reason": "self_comment"}

    media = await MediaRepository(db).get_by_instagram_media_id(
        account["_id"], parsed.instagram_media_id
    )

    comments = CommentsRepository(db)
    comment_doc, _created = await comments.insert_unique(
        {
            "instagram_account_id": account["_id"],
            "instagram_comment_id": parsed.instagram_comment_id,
            "instagram_user_id": parsed.instagram_user_id,
            "username": parsed.username,
            "text": parsed.text,
            "normalized_text": normalize_for_search(parsed.text),
            "media_id": media["_id"] if media else None,
            "instagram_media_id": parsed.instagram_media_id,
            "media_product_type": parsed.media_product_type,
            "parent_id": parsed.parent_id,
            "commented_at": parsed.commented_at,
            "webhook_event_id": event["_id"],
            "raw_payload": parsed.raw,
        }
    )

    context = CommentContext(
        text=parsed.text,
        username=parsed.username,
        instagram_comment_id=parsed.instagram_comment_id,
        instagram_user_id=parsed.instagram_user_id,
        media_id=str(media["_id"]) if media else None,
        instagram_media_id=parsed.instagram_media_id,
        post_caption=(media or {}).get("caption"),
    )

    # Rules are scoped to the post the comment was left on.
    rules = await RulesRepository(db).list_for_media(
        account["_id"], parsed.instagram_media_id
    )
    rule = select_rule(context, rules)

    # AI is a fallback, never a replacement: the deterministic matcher runs
    # first, and the model is only consulted when it found nothing and some rule
    # on this post actually asked for intent matching (§56).
    if rule is None and settings.ai_enabled and rules_wanting_ai(rules):
        rule = await _classify_and_match(
            ctx, db, settings, rules, comment_doc, parsed.text, log
        )

    if rule is None:
        # No execution row: there is no rule to key one on. The Comments page
        # derives "unmatched" from comments having no execution.
        logger.info("comment_unmatched", extra={**log, "rules_on_post": len(rules)})
        await events.mark_processed(event["_id"])
        return {"status": "unmatched"}

    log["rule_id"] = str(rule["_id"])

    # The opening DM and the payoff DM are separate actions, each with its own
    # reservation, so the second can be sent later without colliding with the first.
    action = ACTION_OPENING_DM if uses_opening_dm(rule) else ACTION_PRIVATE_REPLY
    log["action"] = action

    executions = ExecutionsRepository(db)
    now = datetime.now(UTC)


    decision = evaluate(
        PolicyContext(
            rule=rule,
            account=account,
            action=ACTION_PRIVATE_REPLY,
            commented_at=parsed.commented_at,
            dry_run=settings.dry_run,
            sent_last_minute=await executions.count_since(
                rule["_id"], now - timedelta(minutes=1), action
            ),
            sent_last_hour=await executions.count_since(
                rule["_id"], now - timedelta(hours=1), action
            ),
            sent_last_day=await executions.count_since(
                rule["_id"], now - timedelta(days=1), action
            ),
            already_messaged=(
                await executions.already_sent_to(rule["_id"], parsed.instagram_user_id)
                if rule.get("once_per_person", True)
                else False
            ),
            now=now,
        )
    )

    # Reserve BEFORE any Meta call. The unique index is what stops two workers
    # both sending (§63).
    execution = await executions.reserve(
        instagram_account_id=account["_id"],
        comment_id=comment_doc["_id"],
        rule_id=rule["_id"],
        action=action,
        dry_run=settings.dry_run,
        instagram_user_id=parsed.instagram_user_id,
    )
    if execution is None:
        existing = await executions.find_reservation(comment_doc["_id"], rule["_id"], action)
        if existing is None:  # pragma: no cover - concurrent delete
            return {"status": "skipped", "reason": "reservation_vanished"}
        if existing.get("status") in TERMINAL_STATUSES:
            logger.info("duplicate_job_ignored", extra=log)
            return {"status": "duplicate", "execution_id": str(existing["_id"])}
        # Still processing: this is our own retry, so continue it.
        execution = existing

    log["execution_id"] = str(execution["_id"])
    await executions.increment_attempts(execution["_id"])

    if not decision.allowed:
        await executions.complete(
            execution["_id"], status="skipped", policy_decision=decision.reason
        )
        await events.mark_processed(event["_id"])
        logger.info("policy_denied", extra={**log, "reason": decision.reason})
        return {"status": "skipped", "reason": decision.reason}

    render_context = build_render_context(context, rule)
    try:
        if action == ACTION_OPENING_DM:
            message = render(rule["opening_dm"]["message"], render_context)
            buttons = [
                postback_button(
                    (rule.get("opening_dm") or {}).get("button_text") or "Send me the link",
                    build_postback_payload(str(rule["_id"]), parsed.instagram_comment_id),
                )
            ]
        else:
            message = render(rule["message"], render_context)
            buttons = await tracked_buttons(
                db, settings, rule, account, execution["_id"], parsed.instagram_user_id
            )
    except TemplateError as exc:
        # Not retryable: the same message will fail identically next time.
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        logger.error("message_render_failed", extra={**log, "error": str(exc)})
        return {"status": "failed", "reason": "template_error"}

    if decision.is_dry_run:
        await executions.complete(
            execution["_id"],
            status="dry_run",
            message_text=message,
            policy_decision=DRY_RUN,
        )
        await events.mark_processed(event["_id"])
        logger.info("dry_run_execution", extra=log)
        return {"status": "dry_run", "execution_id": str(execution["_id"])}

    try:
        access_token = decrypt(
            account.get("access_token_encrypted", ""), settings.encryption_key
        )
    except EncryptionError as exc:
        # Wrong ENCRYPTION_KEY or corrupt storage. Retrying cannot help, and the
        # account must be reconnected.
        await accounts.set_status(account["_id"], "token_error", str(exc))
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        logger.error("token_decrypt_failed", extra=log)
        return {"status": "failed", "reason": "token_decrypt_failed"}

    # Public reply first: it is independent of the DM, and failing it must not
    # block the DM the commenter is actually waiting for.
    await _maybe_public_reply(
        client, access_token, account, rule, parsed, render_context, executions, log
    )

    try:
        result = await client.send_private_reply(
            access_token,
            account["instagram_user_id"],
            parsed.instagram_comment_id,
            message,
            buttons,
        )
    except MetaTokenError as exc:
        # A dead token cannot be fixed by retrying; pause the account so the
        # dashboard prompts a reconnect.
        await accounts.set_status(account["_id"], "token_error", str(exc))
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        logger.error("meta_token_error", extra=log)
        return {"status": "failed", "reason": "token_error"}
    except MetaAPIError as exc:
        if exc.retryable and attempt < MAX_ATTEMPTS:
            logger.warning("meta_call_retrying", extra={**log, "error": str(exc)})
            raise Retry(defer=retry_delay(attempt + 1)) from exc
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        logger.error("meta_call_failed", extra={**log, "error": str(exc)})
        return {"status": "failed", "reason": "meta_error"}

    await executions.complete(
        execution["_id"],
        status="success",
        message_text=message,
        meta_message_id=result.message_id,
        meta_recipient_id=result.recipient_id,
        policy_decision=decision.reason,
    )
    await events.mark_processed(event["_id"])
    logger.info("private_reply_success", extra={**log, "meta_message_id": result.message_id})
    return {"status": "success", "action": action, "execution_id": str(execution["_id"])}


async def _classify_and_match(
    ctx: dict[str, Any],
    db: Any,
    settings: Any,
    rules: list[dict[str, Any]],
    comment_doc: dict[str, Any],
    text: str,
    log: dict[str, Any],
) -> dict[str, Any] | None:
    """Classify the comment and find a rule that wants that intent.

    Every outcome is recorded, including unusable ones, so a rule that never
    fires can be explained by looking at what the model actually said.
    """
    ai_client = ctx.get("ai_client")
    if ai_client is None:
        return None

    result = await classify(ai_client, text)
    await AiClassificationsRepository(db).insert(
        {
            "comment_id": comment_doc["_id"],
            "model": result.model,
            "intent": result.intent,
            "confidence": result.confidence,
            "latency_ms": result.latency_ms,
            "raw_response": result.raw_response,
        }
    )
    logger.info(
        "comment_classified",
        extra={
            **log,
            "intent": result.intent,
            "confidence": result.confidence,
            "latency_ms": result.latency_ms,
        },
    )

    if not result.is_usable:
        return None

    matched = select_rule_by_intent(
        result.intent, result.confidence, rules, settings.ai_min_confidence
    )
    if matched is not None:
        log["matched_by"] = "ai"
    return matched


async def _maybe_public_reply(
    client: Any,
    access_token: str,
    account: dict[str, Any],
    rule: dict[str, Any],
    parsed: Any,
    render_context: dict[str, Any],
    executions: ExecutionsRepository,
    log: dict[str, Any],
) -> None:
    """Optionally reply publicly under the comment (docs §4b).

    Best-effort: a failure here is logged but never propagated, because the
    private reply is the thing the commenter asked for.
    """
    public = rule.get("public_reply") or {}
    if not public.get("enabled") or not str(public.get("message", "")).strip():
        return
    try:
        text = render(public["message"], render_context)
        await client.send_public_reply(access_token, parsed.instagram_comment_id, text)
        logger.info("public_reply_sent", extra=log)
    except (MetaAPIError, TemplateError) as exc:
        logger.warning("public_reply_failed", extra={**log, "error": str(exc)})


async def process_postback_event(
    ctx: dict[str, Any],
    webhook_event_id: str,
    instagram_account_id: str,
    sender_id: str,
    payload: str,
) -> dict[str, Any]:
    """Someone tapped the button on an opening DM.

    The tap opens the 24-hour messaging window, which is what makes this
    follow-up permitted — a private reply is limited to one message, so the
    payoff could not have ridden along with the opener.
    """
    from app.services.meta.webhooks import POSTBACK_PREFIX

    db = ctx["db"]
    settings = ctx["settings"]
    client = ctx["client"]
    attempt = int(ctx.get("job_try", 1))

    parts = payload.split(":")
    if not payload.startswith(f"{POSTBACK_PREFIX}:") or len(parts) < 3:
        return {"status": "skipped", "reason": "not_ours"}
    rule_id, instagram_comment_id = parts[1], parts[2]

    log = {"rule_id": rule_id, "instagram_comment_id": instagram_comment_id, "attempt": attempt}

    accounts = InstagramAccountsRepository(db)
    account = await accounts.get_by_instagram_user_id(instagram_account_id)
    if account is None:
        return {"status": "skipped", "reason": "unknown_account"}

    rules = RulesRepository(db)
    try:
        rule = await rules.get(rule_id)
    except ValueError:
        return {"status": "skipped", "reason": "bad_rule_id"}
    if rule is None:
        return {"status": "skipped", "reason": "rule_missing"}

    comments = CommentsRepository(db)
    comment_doc = await comments.get_by_instagram_comment_id(
        account["_id"], instagram_comment_id
    )
    if comment_doc is None:
        return {"status": "skipped", "reason": "comment_missing"}

    executions = ExecutionsRepository(db)
    execution = await executions.reserve(
        instagram_account_id=account["_id"],
        comment_id=comment_doc["_id"],
        rule_id=rule["_id"],
        action=ACTION_PRIVATE_REPLY,
        dry_run=settings.dry_run,
        instagram_user_id=sender_id,
    )
    if execution is None:
        existing = await executions.find_reservation(
            comment_doc["_id"], rule["_id"], ACTION_PRIVATE_REPLY
        )
        if existing is None or existing.get("status") in TERMINAL_STATUSES:
            logger.info("duplicate_postback_ignored", extra=log)
            return {"status": "duplicate"}
        execution = existing

    log["execution_id"] = str(execution["_id"])
    await executions.increment_attempts(execution["_id"])

    context = CommentContext(
        text=comment_doc.get("text", ""),
        username=comment_doc.get("username", ""),
        instagram_comment_id=instagram_comment_id,
        instagram_user_id=sender_id,
    )
    try:
        message = render(rule["message"], build_render_context(context, rule))
    except TemplateError as exc:
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        return {"status": "failed", "reason": "template_error"}

    gate = rule.get("collect_email") or {}
    if gate.get("enabled") and not settings.dry_run:
        # Ask for the email and pause here. process_message_event resumes this
        # execution when they reply, so the payoff is sent exactly once.
        try:
            access_token = decrypt(
                account.get("access_token_encrypted", ""), settings.encryption_key
            )
            await client.send_direct_message(
                access_token,
                account["instagram_user_id"],
                sender_id,
                gate.get("prompt") or "What's your email? I'll send it straight over.",
            )
        except (MetaAPIError, EncryptionError) as exc:
            await executions.complete(execution["_id"], status="failed", error=str(exc))
            return {"status": "failed", "reason": "email_prompt_failed"}

        await executions.update(execution["_id"], {"status": "awaiting_email"})
        logger.info("awaiting_email", extra=log)
        return {"status": "awaiting_email", "execution_id": str(execution["_id"])}

    if settings.dry_run:
        await executions.complete(
            execution["_id"], status="dry_run", message_text=message, policy_decision=DRY_RUN
        )
        logger.info("dry_run_postback_reply", extra=log)
        return {"status": "dry_run", "execution_id": str(execution["_id"])}

    try:
        access_token = decrypt(
            account.get("access_token_encrypted", ""), settings.encryption_key
        )
        result = await client.send_direct_message(
            access_token,
            account["instagram_user_id"],
            sender_id,
            message,
            await tracked_buttons(db, settings, rule, account, execution["_id"], sender_id),
        )
    except MetaTokenError as exc:
        await accounts.set_status(account["_id"], "token_error", str(exc))
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        return {"status": "failed", "reason": "token_error"}
    except (MetaAPIError, EncryptionError) as exc:
        retryable = getattr(exc, "retryable", False)
        if retryable and attempt < MAX_ATTEMPTS:
            raise Retry(defer=retry_delay(attempt + 1)) from exc
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        return {"status": "failed", "reason": "meta_error"}

    await executions.complete(
        execution["_id"],
        status="success",
        message_text=message,
        meta_message_id=result.message_id,
        meta_recipient_id=result.recipient_id,
    )
    logger.info("postback_reply_success", extra=log)
    return {"status": "success", "execution_id": str(execution["_id"])}


async def refresh_expiring_tokens(ctx: dict[str, Any]) -> dict[str, int]:
    """Refresh long-lived tokens before they lapse.

    Meta expires a token not refreshed within 60 days, and refuses to refresh one
    under 24 hours old. Running this on a schedule means a quiet fortnight cannot
    silently disconnect the account.
    """
    db = ctx["db"]
    settings = ctx["settings"]
    accounts = InstagramAccountsRepository(db)

    refreshed = failed = 0
    for account in await accounts.list({"status": {"$in": ["connected", "token_error"]}}):
        if not needs_refresh(account):
            continue
        try:
            current = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
            bundle = await meta_auth.refresh_long_lived(settings, current)
        except (MetaAPIError, EncryptionError) as exc:
            failed += 1
            await accounts.set_status(account["_id"], "token_error", str(exc))
            logger.error(
                "scheduled_token_refresh_failed",
                extra={"account_id": str(account["_id"]), "error": str(exc)},
            )
            continue

        await accounts.update(
            account["_id"],
            {
                "status": "connected",
                "status_error": None,
                **token_fields(bundle.access_token, bundle.expires_at, settings.encryption_key),
            },
        )
        refreshed += 1
        logger.info("scheduled_token_refreshed", extra={"account_id": str(account["_id"])})

    return {"refreshed": refreshed, "failed": failed}


async def process_message_event(
    ctx: dict[str, Any],
    webhook_event_id: str,
    instagram_account_id: str,
    sender_id: str,
    text: str,
) -> dict[str, Any]:
    """An inbound DM. Used to complete the "collect email" flow.

    Only acts when an execution for this person is parked at `awaiting_email`;
    every other message is ignored, because InstaFlow is not a chatbot and must
    not respond to unrelated conversation.
    """
    db = ctx["db"]
    settings = ctx["settings"]
    client = ctx["client"]

    accounts = InstagramAccountsRepository(db)
    account = await accounts.get_by_instagram_user_id(instagram_account_id)
    if account is None:
        return {"status": "skipped", "reason": "unknown_account"}

    executions = ExecutionsRepository(db)
    execution = await executions.find_awaiting_email(account["_id"], sender_id)
    if execution is None:
        return {"status": "ignored", "reason": "no_pending_capture"}

    log = {"execution_id": str(execution["_id"]), "rule_id": str(execution.get("rule_id"))}

    email = extract_email(text)
    if email is None:
        # Leave the execution parked: they may simply have typed something else
        # first, and re-prompting on every message would be spam.
        logger.info("email_not_found_in_reply", extra=log)
        return {"status": "awaiting_email", "reason": "no_email_in_message"}

    rules = RulesRepository(db)
    rule = await rules.get(execution["rule_id"])
    if rule is None:
        await executions.complete(execution["_id"], status="failed", error="Rule deleted.")
        return {"status": "failed", "reason": "rule_missing"}

    await ContactsRepository(db).upsert(
        account["_id"],
        sender_id,
        {
            "instagram_account_id": account["_id"],
            "instagram_user_id": sender_id,
            "email": email,
            "source_rule_id": rule["_id"],
        },
    )
    logger.info("email_captured", extra=log)

    context = CommentContext(text="", username="", instagram_user_id=sender_id)
    try:
        message = render(rule["message"], build_render_context(context, rule))
        access_token = decrypt(
            account.get("access_token_encrypted", ""), settings.encryption_key
        )
        result = await client.send_direct_message(
            access_token,
            account["instagram_user_id"],
            sender_id,
            message,
            await tracked_buttons(db, settings, rule, account, execution["_id"], sender_id),
        )
    except (MetaAPIError, EncryptionError, TemplateError) as exc:
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        logger.error("payoff_after_email_failed", extra={**log, "error": str(exc)})
        return {"status": "failed", "reason": "send_failed"}

    await executions.complete(
        execution["_id"],
        status="success",
        message_text=message,
        meta_message_id=result.message_id,
        meta_recipient_id=result.recipient_id,
    )
    logger.info("payoff_after_email_sent", extra=log)
    return {"status": "success", "email_captured": True}
