"""ARQ task definitions.

`process_comment_event` is the whole cold path: webhook event -> comment ->
rule -> policy -> execution reservation -> render -> Meta. It is the only
place a private reply is ever sent.
"""

import logging
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from arq.worker import Retry

from app.db.repositories import (
    AccountSnapshotsRepository,
    AiClassificationsRepository,
    AppSettingsRepository,
    CommentsRepository,
    ContactsRepository,
    ExecutionsRepository,
    InstagramAccountsRepository,
    LinkClicksRepository,
    MediaRepository,
    RulesRepository,
    StoriesRepository,
    UpcomingPostsRepository,
    WebhookEventsRepository,
)
from app.errors import MetaAPIError, MetaTokenError, TemplateError
from app.models.rules import FOLLOW_PROMPT_NO_WINDOW, FOLLOW_PROMPT_WITH_WINDOW
from app.services import origins
from app.services.ai.classifier import classify
from app.services.campaigns import (
    ACTION_DIRECT_MESSAGE,
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
from app.services.campaigns.policy import DRY_RUN, NOT_FOLLOWING
from app.services.contacts import extract_email
from app.services.links import is_safe_target, new_token, tracking_url
from app.services.meta import auth as meta_auth
from app.services.meta.credentials import origin_for
from app.services.meta.messages import postback_button, web_url_button
from app.services.meta.tokens import needs_refresh, token_fields
from app.services.meta.webhooks import (
    build_postback_payload,
    parse_comment_events,
    parse_message_events,
)
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

    Without this there is no honest click-through metric (§59). If the origin is
    not HTTPS the original URL is sent unchanged -- Meta rejects non-HTTPS
    web_url buttons, so a broken tracking link would lose the message entirely,
    which is worse than losing the metric.

    The origin comes from `origin_for`, not straight from `PUBLIC_BASE_URL`.
    Reading the environment directly meant an operator who corrected the origin
    on the Settings page still got untracked links, because only the Meta-facing
    URLs honoured the override -- two sources of truth for one value.
    """
    links = LinkClicksRepository(db)
    origin = origin_for(settings, await AppSettingsRepository(db).load())
    trackable = origins.is_trackable(origin)
    if not trackable and (rule.get("buttons") or []):
        # Loud, because the alternative is a click-through rate that reads zero
        # forever and looks exactly like nobody clicking.
        logger.warning(
            "link_tracking_disabled_origin_not_https",
            extra={"origin": origin, "rule_id": str(rule["_id"])},
        )

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
        buttons.append(web_url_button(title, tracking_url(origin, token)))

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
    delayed: bool = False,
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
    rules = await RulesRepository(db).list_for_media(account["_id"], parsed.instagram_media_id)
    rule = select_rule(context, rules)

    # AI is a fallback, never a replacement: the deterministic matcher runs
    # first, and the model is only consulted when it found nothing and some rule
    # on this post actually asked for intent matching (§56).
    if rule is None and settings.ai_enabled and rules_wanting_ai(rules):
        rule = await _classify_and_match(ctx, db, settings, rules, comment_doc, parsed.text, log)

    if rule is None:
        # No execution row: there is no rule to key one on. The Comments page
        # derives "unmatched" from comments having no execution.
        logger.info("comment_unmatched", extra={**log, "rules_on_post": len(rules)})
        await events.mark_processed(event["_id"])
        return {"status": "unmatched"}

    log["rule_id"] = str(rule["_id"])

    # The opening DM and the payoff DM are separate actions, each with its own
    # reservation, so the second can be sent later without colliding with the first.
    # Reply delay. Re-queued rather than slept on: a worker holding a job open
    # for a minute is a worker not draining the queue. The rule is re-read on the
    # second pass, so editing it during the wait takes effect.
    #
    # Nothing is reserved yet, so a Meta redelivery could double-enqueue -- the
    # webhook endpoint's event_hash index is what stops that (§idempotency).
    delay_seconds = int(rule.get("reply_delay_seconds") or 0)
    if delay_seconds > 0 and not delayed:
        redis = ctx.get("redis")
        if redis is not None:
            await redis.enqueue_job(
                "process_comment_event",
                webhook_event_id,
                instagram_account_id,
                instagram_comment_id,
                True,
                _defer_by=timedelta(seconds=delay_seconds),
            )
            logger.info("reply_delayed", extra={**log, "delay_seconds": delay_seconds})
            return {"status": "scheduled", "delay_seconds": delay_seconds}

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
            # Only looked up when a rule actually gates on it -- it costs a Meta
            # round trip per comment. `None` (unknown) is allowed through; the
            # link-bearing payoff is gated again at the postback, where a DM
            # thread exists and Meta is far more likely to answer.
            # Only gate here when the link rides in this very message. If the
            # rule opens with a button DM, that opener carries no link and is
            # the only thing that can open a messaging window -- blocking it
            # would strand the person with a promise we cannot keep, since a
            # comment allows exactly one private reply and no window is open
            # yet. The postback gate does the real enforcement (§4, §5).
            is_following=(
                await lookup_is_following(client, settings, account, parsed.instagram_user_id, log)
                if (rule.get("require_follow") or {}).get("enabled") and not uses_opening_dm(rule)
                else None
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
        # A follow-gated block is the one denial worth explaining. The rule's own
        # prompt says what to do about it ("follow me and comment again"), and
        # silence would leave the person with no idea why nothing arrived. Every
        # other denial stays quiet on purpose -- rate limits and duplicates are
        # our problem, not theirs.
        if decision.reason == NOT_FOLLOWING and not settings.dry_run:
            # No window is open here and the comment's one private reply is
            # about to be spent, so this wording -- unlike the postback's -- has
            # to ask for another comment. Falls back rather than staying silent:
            # a blocked person with no explanation just sees nothing happen.
            prompt = (
                (rule.get("require_follow") or {}).get("prompt") or ""
            ).strip() or FOLLOW_PROMPT_NO_WINDOW
            if prompt:
                try:
                    access_token = decrypt(
                        account.get("access_token_encrypted", ""), settings.encryption_key
                    )
                    await client.send_private_reply(
                        access_token,
                        account["instagram_user_id"],
                        parsed.instagram_comment_id,
                        prompt,
                    )
                    logger.info("follow_prompt_sent", extra=log)
                except (MetaAPIError, EncryptionError) as exc:
                    # The link is withheld either way; only the explanation failed.
                    logger.warning("follow_prompt_failed", extra={**log, "error": str(exc)})

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
        access_token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
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


AWAITING_FOLLOW = "awaiting_follow"

# Meta publishes no "user followed you" webhook (docs/meta-api.md §5.4), so the
# only way to notice is to ask. Tight early so the link feels immediate, then
# sparse -- most people who follow do it within a minute of being asked.
FOLLOW_RECHECK_DELAYS = (15, 30, 60, 120, 180, 300, 600, 900, 1800, 3600)
# The postback tap opened a 24-hour window; stop an hour short of the edge so a
# send never races the expiry.
FOLLOW_WATCH_WINDOW = timedelta(hours=23)


async def _schedule_follow_recheck(ctx: dict[str, Any], execution_id: Any, attempt: int) -> None:
    """Queue the next follow check, backing off as hope fades."""
    delay = FOLLOW_RECHECK_DELAYS[min(attempt, len(FOLLOW_RECHECK_DELAYS) - 1)]
    redis = ctx.get("redis")
    if redis is None:  # pragma: no cover - only in tests that drive tasks directly
        return
    await redis.enqueue_job(
        "recheck_follow", str(execution_id), attempt + 1, _defer_by=timedelta(seconds=delay)
    )


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
    comment_doc = await comments.get_by_instagram_comment_id(account["_id"], instagram_comment_id)
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
        blocked_on_follow = (
            existing is not None
            and existing.get("status") == "skipped"
            and existing.get("policy_decision") == NOT_FOLLOWING
        )
        if existing is None or (
            existing.get("status") in TERMINAL_STATUSES and not blocked_on_follow
        ):
            logger.info("duplicate_postback_ignored", extra=log)
            return {"status": "duplicate"}
        execution = existing
        if blocked_on_follow:
            # Withheld, not delivered. Tapping again is how someone says "I have
            # followed now", and the fresh tap opens a new 24-hour window, so
            # the link can still be handed over.
            await executions.update(existing["_id"], {"status": "processing"})
            logger.info("postback_retry_after_follow_block", extra=log)
        if existing.get("status") == AWAITING_FOLLOW:
            # They tapped again, probably having just followed. A scheduled
            # re-check may be mid-flight on this same row, so claim it first.
            claimed = await executions.claim_awaiting_follow(existing["_id"])
            if claimed is None:
                logger.info("duplicate_postback_ignored", extra=log)
                return {"status": "duplicate"}
            execution = claimed

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

    # Follow gate, enforced here rather than only at the comment. This message
    # carries the link, so this is the moment that actually matters -- and a DM
    # thread now exists, which is when Meta is most willing to answer the
    # profile lookup. `None` (Meta would not say) is allowed through by design.
    gates_on_follow = (rule.get("require_follow") or {}).get("enabled") and not settings.dry_run
    if (
        gates_on_follow
        and await lookup_is_following(client, settings, account, sender_id, log) is False
    ):
        prompt = (rule["require_follow"].get("prompt") or "").strip() or (FOLLOW_PROMPT_WITH_WINDOW)
        try:
            access_token = decrypt(
                account.get("access_token_encrypted", ""), settings.encryption_key
            )
            await client.send_direct_message(
                access_token, account["instagram_user_id"], sender_id, prompt
            )
        except (MetaAPIError, EncryptionError) as exc:
            # The link is still withheld; only the explanation failed.
            logger.warning("follow_prompt_failed", extra={**log, "error": str(exc)})
        # Park rather than finish. Tapping the button opened a 24-hour messaging
        # window (§5), so the link can still be delivered inside it without a
        # second comment -- we just have to notice the follow ourselves, because
        # Meta publishes no follow webhook (§5.4).
        deadline = datetime.now(UTC) + FOLLOW_WATCH_WINDOW
        await executions.update(
            execution["_id"],
            {
                "status": AWAITING_FOLLOW,
                "policy_decision": NOT_FOLLOWING,
                "follow_deadline": deadline,
            },
        )
        await _schedule_follow_recheck(ctx, execution["_id"], 0)
        logger.info("awaiting_follow", extra={**log, "deadline": deadline.isoformat()})
        return {"status": AWAITING_FOLLOW, "execution_id": str(execution["_id"])}

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
        access_token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
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

    await send_attachment_if_any(client, settings, account, sender_id, rule, log)

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
            bundle = await meta_auth.refresh_long_lived(current)
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


async def sync_posts(ctx: dict[str, Any]) -> dict[str, int]:
    """Pull recent posts, then arm the waiting automation on the first new one.

    Posts were previously discovered only when someone pressed Refresh, which
    made "apply this to my next post" meaningless -- the template would sit
    unapplied until a human happened to look.

    Read-only against Meta, so it runs regardless of DRY_RUN. Creating a rule is
    a local write; nothing is sent until someone actually comments.
    """
    db = ctx["db"]
    settings = ctx["settings"]
    client = ctx["client"]
    media = MediaRepository(db)

    synced = armed = 0
    for account in await InstagramAccountsRepository(db).list({"status": "connected"}):
        try:
            token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
            fetched = await client.get_media(token, account["instagram_user_id"], limit=50)
        except (MetaAPIError, EncryptionError) as exc:
            logger.warning(
                "post_sync_failed", extra={"account_id": str(account["_id"]), "error": str(exc)}
            )
            continue

        for item in fetched:
            await media.upsert(
                account["_id"],
                item.instagram_media_id,
                {
                    "instagram_account_id": account["_id"],
                    "instagram_media_id": item.instagram_media_id,
                    "media_type": item.media_type,
                    "media_product_type": item.media_product_type,
                    "caption": item.caption,
                    "permalink": item.permalink,
                    "thumbnail_url": item.thumbnail_url,
                    "media_url": item.media_url,
                    "published_at": item.published_at,
                    "like_count": item.like_count,
                    "comments_count": item.comments_count,
                    "updated_at": datetime.now(UTC),
                },
            )
        synced += len(fetched)
        armed += await apply_upcoming_post(db, account)

    return {"synced": synced, "armed": armed}


async def apply_upcoming_post(db: Any, account: dict[str, Any]) -> int:
    """Arm the waiting template on the earliest post published since it was set.

    "Published since" rather than "not seen before": a post that predates the
    template is not what the operator meant by *upcoming*, however late we
    happen to sync it.

    Earliest first, because more than one post can appear between syncs and the
    operator said the first one wins -- and because "the newest" would silently
    change which post got it depending on sync timing.
    """
    upcoming = UpcomingPostsRepository(db)
    claimed = await upcoming.claim(account["_id"])
    if claimed is None:
        return 0

    cutoff = claimed.get("created_at")
    candidates = await MediaRepository(db).list(
        {
            "instagram_account_id": account["_id"],
            "published_at": {"$gt": cutoff},
        },
        sort_field="published_at",
        descending=False,
        limit=1,
    )
    if not candidates:
        # Nothing published yet. Put it back and wait for the next sync.
        await upcoming.release(claimed["_id"])
        return 0

    post = candidates[0]
    template = dict(claimed.get("rule") or {})
    template.update(
        {
            "instagram_account_id": account["_id"],
            "trigger": "comment",
            "scope": "specific",
            "instagram_media_ids": [post["instagram_media_id"]],
            "created_from_upcoming": True,
        }
    )

    try:
        rule_id = await RulesRepository(db).insert(template)
    except Exception as exc:
        # Put it back rather than lose the template because one sync failed.
        await upcoming.release(claimed["_id"])
        logger.error(
            "upcoming_post_apply_failed",
            extra={"upcoming_id": str(claimed["_id"]), "error": str(exc)},
        )
        return 0

    await upcoming.mark_applied(claimed["_id"], rule_id, post["instagram_media_id"])
    logger.info(
        "upcoming_post_applied",
        extra={
            "rule_id": str(rule_id),
            "instagram_media_id": post["instagram_media_id"],
            "published_at": str(post.get("published_at")),
        },
    )
    return 1


async def sync_stories(ctx: dict[str, Any]) -> dict[str, int]:
    """Refresh the story list, and disable rules whose stories have expired.

    Expiry is **observed, not computed**. Meta returns only active stories, so a
    story we hold that the edge no longer lists has expired. Counting 24 hours
    from `timestamp` would not work: Meta omits that field for video stories
    (docs/meta-api.md §4d), so half of them have nothing to count from.

    Read-only against Meta, so it runs regardless of DRY_RUN -- a rule silently
    matching an expired story is a correctness problem, not a sending one.
    """
    db = ctx["db"]
    settings = ctx["settings"]
    client = ctx["client"]
    stories = StoriesRepository(db)
    rules = RulesRepository(db)

    seen = expired = disabled = 0
    for account in await InstagramAccountsRepository(db).list({"status": "connected"}):
        try:
            token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
            active = await client.get_stories(token, account["instagram_user_id"])
        except (MetaAPIError, EncryptionError) as exc:
            # One account's failure must not strand the others, and an empty
            # response here is indistinguishable from "all expired" -- so a
            # failed fetch expires nothing.
            logger.warning(
                "story_sync_failed",
                extra={"account_id": str(account["_id"]), "error": str(exc)},
            )
            continue

        for story in active:
            await stories.upsert(
                account["_id"],
                story.instagram_media_id,
                {
                    "media_type": story.media_type,
                    "caption": story.caption,
                    "permalink": story.permalink,
                    "thumbnail_url": story.thumbnail_url,
                    "media_url": story.media_url,
                    "published_at": story.published_at,
                },
            )
        seen += len(active)

        gone = await stories.mark_expired(account["_id"], [s.instagram_media_id for s in active])
        expired += len(gone)
        for rule in await rules.disable_for_expired_stories(account["_id"], gone):
            disabled += 1
            logger.info(
                "rule_disabled_story_expired",
                # `rule_name`, not `name`: `name` is the logger's own LogRecord
                # attribute. This would have crashed the sync_stories cron the
                # first time a story expired with a rule attached.
                extra={"rule_id": str(rule["_id"]), "rule_name": rule.get("name", "")},
            )

    return {"active": seen, "expired": expired, "rules_disabled": disabled}


async def send_attachment_if_any(
    client: Any,
    settings: Any,
    account: dict[str, Any],
    recipient_id: str,
    rule: dict[str, Any],
    log: dict[str, Any] | None = None,
) -> None:
    """Send the rule's attachment, if it has one, as its own message.

    Meta takes a URL and fetches it, so this is a second call after the text
    rather than a field on it. A failure here is logged and swallowed: the payoff
    text and its buttons already went out, and failing the whole execution would
    make a delivered message look undelivered.
    """
    attachment = rule.get("attachment") or {}
    url = attachment.get("url")
    kind = attachment.get("type")
    if not url or not kind:
        return
    try:
        token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
        await client.send_attachment(token, account["instagram_user_id"], recipient_id, kind, url)
        logger.info("attachment_sent", extra={**(log or {}), "type": kind})
    except (MetaAPIError, EncryptionError) as exc:
        logger.warning(
            "attachment_send_failed", extra={**(log or {}), "type": kind, "error": str(exc)}
        )


async def lookup_is_following(
    client: Any,
    settings: Any,
    account: dict[str, Any],
    instagram_user_id: str,
    log: dict[str, Any] | None = None,
) -> bool | None:
    """Does this person follow the account? `None` means Meta would not say.

    Uses the Messaging User Profile API's `is_user_follow_business` -- the
    officially supported way to gate on following (docs/meta-api.md §4b). Meta
    answers for most commenters even with no prior DM thread, but refuses some
    with "User consent is required to access user profile".

    `None` is deliberately fail-open: the policy blocks on `False` only, so an
    unanswerable lookup lets the message through rather than silently dropping a
    legitimate follower. Verified against live accounts 2026-09-02.
    """
    try:
        token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
        profile = await client.get_user_profile(token, instagram_user_id)
    except (MetaAPIError, EncryptionError) as exc:
        logger.info(
            "follow_check_unavailable",
            extra={**(log or {}), "instagram_user_id": instagram_user_id, "error": str(exc)},
        )
        return None
    return profile.is_user_follow_business


async def lookup_username(
    client: Any,
    settings: Any,
    account: dict[str, Any],
    instagram_user_id: str,
    log: dict[str, Any] | None = None,
) -> str:
    """The sender's handle, for `{{username}}` on a message trigger.

    A comment webhook carries the username; a messaging one does not, so it has
    to be fetched. Called only when the template actually interpolates it --
    otherwise every DM would pay for a Meta round trip to fill a variable nobody
    used.

    Failure returns "" and the message still sends. Meta refuses this lookup for
    some senders ("User consent is required"), and losing a greeting is a much
    smaller harm than losing the reply.
    """
    try:
        token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
        profile = await client.get_user_profile(token, instagram_user_id)
    except (MetaAPIError, EncryptionError) as exc:
        logger.info(
            "username_lookup_unavailable",
            extra={**(log or {}), "instagram_user_id": instagram_user_id, "error": str(exc)},
        )
        return ""
    return profile.username or ""


async def snapshot_account_metrics(ctx: dict[str, Any]) -> dict[str, int]:
    """Record today's follower counters for every connected account.

    Meta exposes `followers_count` as a point in time and no history at all
    (docs/meta-api.md §2b), so growth is only knowable if we write it down daily.
    A missed day leaves a gap rather than corrupting anything: growth is computed
    between the rows that exist, never assumed to be contiguous.

    Read-only against Meta, so this runs regardless of DRY_RUN.
    """
    db = ctx["db"]
    settings = ctx["settings"]
    client = ctx["client"]

    accounts = InstagramAccountsRepository(db)
    snapshots = AccountSnapshotsRepository(db)
    day = datetime.now(UTC).strftime("%Y-%m-%d")

    recorded = failed = 0
    for account in await accounts.list({"status": "connected"}):
        try:
            token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
            profile = await client.get_account(token)
        except (MetaAPIError, EncryptionError) as exc:
            failed += 1
            logger.warning(
                "account_snapshot_failed",
                extra={"account_id": str(account["_id"]), "error": str(exc)},
            )
            continue

        await snapshots.record(
            account["_id"],
            day,
            followers_count=profile.followers_count,
            follows_count=profile.follows_count,
            media_count=profile.media_count,
        )
        # Keep the account document current too, so the UI has a live number
        # without waiting for a reconnect.
        await accounts.update(
            account["_id"],
            {
                "followers_count": profile.followers_count,
                "follows_count": profile.follows_count,
                "media_count": profile.media_count,
                # Refreshed here because Meta's CDN links expire; a stale one
                # renders as a broken image in the dashboard.
                "profile_picture_url": profile.profile_picture_url,
            },
        )
        recorded += 1
        logger.info(
            "account_snapshot_recorded",
            extra={"account_id": str(account["_id"]), "day": day},
        )

    return {"recorded": recorded, "failed": failed}


async def recheck_follow(ctx: dict[str, Any], execution_id: str, attempt: int) -> dict[str, Any]:
    """Has the person followed yet? If so, send the link they were promised.

    Meta has no follow webhook, so this polls. It only runs for executions parked
    at `awaiting_follow` by the postback gate, and only inside the 24-hour
    messaging window that their button tap opened.

    An *unknown* answer keeps waiting rather than sending. Elsewhere unknown
    fails open, but here we hold positive evidence the person was not following,
    so treating a refused lookup as permission would hand over the link the gate
    just withheld.
    """
    db = ctx["db"]
    settings = ctx["settings"]
    client = ctx["client"]

    executions = ExecutionsRepository(db)
    try:
        execution = await executions.get(execution_id)
    except ValueError:
        return {"status": "skipped", "reason": "bad_execution_id"}
    # Anything but `awaiting_follow` means it already resolved -- delivered, or
    # expired, or superseded by a fresh comment. A stale timer must not resend.
    if execution is None or execution.get("status") != AWAITING_FOLLOW:
        return {"status": "skipped", "reason": "not_awaiting"}

    log = {"execution_id": str(execution["_id"]), "attempt": attempt}

    accounts = InstagramAccountsRepository(db)
    account = await accounts.get(execution["instagram_account_id"])
    rule = await RulesRepository(db).get(execution["rule_id"])
    comment_doc = await CommentsRepository(db).get(execution["comment_id"])
    if account is None or rule is None or comment_doc is None:
        await executions.complete(execution["_id"], status="skipped", policy_decision=NOT_FOLLOWING)
        return {"status": "skipped", "reason": "missing_context"}

    recipient = str(
        execution.get("instagram_user_id") or comment_doc.get("instagram_user_id") or ""
    )
    is_following = await lookup_is_following(client, settings, account, recipient, log)

    if is_following is not True:
        deadline = execution.get("follow_deadline")
        if deadline is not None and datetime.now(UTC) >= deadline:
            await executions.complete(
                execution["_id"], status="skipped", policy_decision=NOT_FOLLOWING
            )
            logger.info("follow_watch_expired", extra=log)
            return {"status": "skipped", "reason": "follow_watch_expired"}
        await _schedule_follow_recheck(ctx, execution["_id"], attempt)
        return {"status": AWAITING_FOLLOW, "reason": "still_not_following"}

    # They followed. Claim the row before sending -- a second button tap can be
    # racing this exact moment.
    if await executions.claim_awaiting_follow(execution["_id"]) is None:
        return {"status": "skipped", "reason": "claimed_elsewhere"}

    context = CommentContext(
        text=comment_doc.get("text", ""),
        username=comment_doc.get("username", ""),
        instagram_comment_id=comment_doc.get("instagram_comment_id", ""),
        instagram_user_id=recipient,
    )
    try:
        message = render(rule["message"], build_render_context(context, rule))
    except TemplateError as exc:
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        return {"status": "failed", "reason": "template_error"}

    try:
        access_token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
        result = await client.send_direct_message(
            access_token,
            account["instagram_user_id"],
            recipient,
            message,
            await tracked_buttons(db, settings, rule, account, execution["_id"], recipient),
        )
    except MetaTokenError as exc:
        await accounts.set_status(account["_id"], "token_error", str(exc))
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        return {"status": "failed", "reason": "token_error"}
    except (MetaAPIError, EncryptionError) as exc:
        # The window may simply have closed. One more try if there is time left.
        deadline = execution.get("follow_deadline")
        if getattr(exc, "retryable", False) and deadline and datetime.now(UTC) < deadline:
            await executions.repark_awaiting_follow(execution["_id"])
            await _schedule_follow_recheck(ctx, execution["_id"], attempt)
            return {"status": AWAITING_FOLLOW, "reason": "send_retry"}
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        return {"status": "failed", "reason": "meta_error"}

    await send_attachment_if_any(client, settings, account, recipient, rule, log)

    await executions.complete(
        execution["_id"],
        status="success",
        message_text=message,
        meta_message_id=result.message_id,
        meta_recipient_id=result.recipient_id,
        policy_decision="delivered_after_follow",
    )
    logger.info("link_delivered_after_follow", extra=log)
    return {"status": "success", "execution_id": str(execution["_id"])}


async def refresh_geoip_database(ctx: dict[str, Any]) -> dict[str, Any]:
    """Keep GeoLite2 current. MaxMind rebuilds it twice a week.

    A HEAD request first: their docs say checking does not count against the
    daily download limit, so a no-op week costs nothing. Silent when no
    credentials are configured -- geolocation is opt-in.
    """
    from pathlib import Path

    from app.services.geoip import GeoIpError, database_path, download, remote_build_date

    settings = ctx["settings"]
    saved = await AppSettingsRepository(ctx["db"]).load()
    account_id = saved.get("maxmind_account_id")
    blob = saved.get("maxmind_license_key_encrypted")
    if not account_id or not blob:
        return {"status": "skipped", "reason": "not_configured"}

    directory = Path(settings.geoip_dir)
    try:
        license_key = decrypt(blob, settings.encryption_key)
        built = await remote_build_date(str(account_id), license_key)
    except (GeoIpError, EncryptionError) as exc:
        logger.warning("geoip_check_failed", extra={"error": str(exc)})
        return {"status": "failed", "reason": str(exc)}

    current = database_path(directory)
    if current.is_file() and built is not None and current.stat().st_mtime >= built.timestamp():
        return {"status": "current"}

    try:
        await download(str(account_id), license_key, directory)
    except Exception as exc:
        logger.warning("geoip_download_failed", extra={"error": str(exc)})
        return {"status": "failed", "reason": str(exc)}

    return {"status": "updated"}


async def _match_inbound_message(
    ctx: dict[str, Any],
    webhook_event_id: str,
    account: dict[str, Any],
    sender_id: str,
    text: str,
    mid: str,
    delayed: bool,
) -> dict[str, Any]:
    """Match a story reply or a plain DM against the rules for that trigger.

    Reached only when nobody is parked at `awaiting_email`. The person's own
    message opened a 24-hour window, so the payoff goes straight out as an
    ordinary DM -- no private reply, no opening DM, no postback.
    """
    db = ctx["db"]
    settings = ctx["settings"]
    client = ctx["client"]

    # No message id, no reservation, no send. The mid is the only duplicate
    # protection a message-triggered execution has.
    if not mid:
        return {"status": "ignored", "reason": "no_message_id"}

    events = WebhookEventsRepository(db)
    event = await events.get(webhook_event_id)
    if event is None:
        return {"status": "skipped", "reason": "event_missing"}

    # Re-derived from the stored payload rather than trusted from the job
    # arguments, as the comment path does -- and it is how `reply_to` reaches us
    # without widening the queue signature every time we need another field.
    parsed = next(
        (m for m in parse_message_events(event.get("payload") or {}) if m.mid == mid), None
    )
    if parsed is None:
        return {"status": "skipped", "reason": "not_in_payload"}

    trigger = "story_reply" if parsed.is_story_reply else "dm_keyword"
    log = {"trigger": trigger, "mid": mid}

    # A story reply names the story it answers, so the rule set is narrowed to
    # the ones targeting it. `None` for a DM keyword leaves the query unscoped.
    rules = await RulesRepository(db).list_for_trigger(
        account["_id"], trigger, instagram_story_id=parsed.story_id or None
    )
    context = CommentContext(text=text, instagram_user_id=sender_id)
    rule = select_rule(context, rules)
    if rule is None:
        await events.mark_processed(event["_id"])
        logger.info("message_unmatched", extra={**log, "rules": len(rules)})
        return {"status": "unmatched", "trigger": trigger}

    log["rule_id"] = str(rule["_id"])

    delay_seconds = int(rule.get("reply_delay_seconds") or 0)
    if delay_seconds > 0 and not delayed:
        redis = ctx.get("redis")
        if redis is not None:
            await redis.enqueue_job(
                "process_message_event",
                webhook_event_id,
                str(account["instagram_user_id"]),
                sender_id,
                text,
                mid,
                True,
                _defer_by=timedelta(seconds=delay_seconds),
            )
            logger.info("reply_delayed", extra={**log, "delay_seconds": delay_seconds})
            return {"status": "scheduled", "delay_seconds": delay_seconds}

    executions = ExecutionsRepository(db)
    now = datetime.now(UTC)
    decision = evaluate(
        PolicyContext(
            rule=rule,
            account=account,
            action=ACTION_DIRECT_MESSAGE,
            message_sent_at=parsed.sent_at,
            dry_run=settings.dry_run,
            sent_last_minute=await executions.count_since(
                rule["_id"], now - timedelta(minutes=1), ACTION_DIRECT_MESSAGE
            ),
            sent_last_hour=await executions.count_since(
                rule["_id"], now - timedelta(hours=1), ACTION_DIRECT_MESSAGE
            ),
            sent_last_day=await executions.count_since(
                rule["_id"], now - timedelta(days=1), ACTION_DIRECT_MESSAGE
            ),
            already_messaged=(
                await executions.already_sent_to(rule["_id"], sender_id)
                if rule.get("once_per_person", True)
                else False
            ),
            is_following=(
                await lookup_is_following(client, settings, account, sender_id, log)
                if (rule.get("require_follow") or {}).get("enabled")
                else None
            ),
            now=now,
        )
    )

    execution = await executions.reserve(
        instagram_account_id=account["_id"],
        trigger_message_id=mid,
        rule_id=rule["_id"],
        action=ACTION_DIRECT_MESSAGE,
        dry_run=settings.dry_run,
        instagram_user_id=sender_id,
    )
    if execution is None:
        existing = await executions.find_reservation(
            rule_id=rule["_id"], action=ACTION_DIRECT_MESSAGE, trigger_message_id=mid
        )
        if existing is None or existing.get("status") in TERMINAL_STATUSES:
            logger.info("duplicate_message_ignored", extra=log)
            return {"status": "duplicate"}
        execution = existing

    # Denormalised so the activity feed can describe a row that has no comment.
    await executions.update(
        execution["_id"],
        {
            "trigger": trigger,
            "trigger_text": text,
            "story_id": parsed.story_id or None,
            "story_url": parsed.story_url or None,
        },
    )
    log["execution_id"] = str(execution["_id"])

    if not decision.allowed:
        if decision.reason == NOT_FOLLOWING and not settings.dry_run:
            await _send_follow_prompt(client, settings, account, sender_id, rule, log)
        await executions.complete(
            execution["_id"], status="skipped", policy_decision=decision.reason
        )
        await events.mark_processed(event["_id"])
        logger.info("policy_denied", extra={**log, "reason": decision.reason})
        return {"status": "skipped", "reason": decision.reason}

    # A messaging webhook carries no username, so `{{username}}` would render
    # empty -- "Hi !" -- on a trigger whose editor offers the variable. Fetched
    # only when the template asks for it.
    if "{{username}}" in rule.get("message", ""):
        context = replace(
            context, username=await lookup_username(client, settings, account, sender_id, log)
        )

    try:
        message = render(rule["message"], build_render_context(context, rule))
    except TemplateError as exc:
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        return {"status": "failed", "reason": "template_error"}

    if settings.dry_run:
        await executions.complete(
            execution["_id"], status="dry_run", message_text=message, policy_decision=DRY_RUN
        )
        await events.mark_processed(event["_id"])
        return {"status": "dry_run", "execution_id": str(execution["_id"])}

    # Resolved before the send so the execution can record what actually went
    # out. Storing the pre-disclosure render would make the activity log show a
    # message the recipient never received.
    sent_text = await _with_disclosure(db, message)

    try:
        access_token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
        result = await client.send_direct_message(
            access_token,
            account["instagram_user_id"],
            sender_id,
            sent_text,
            await tracked_buttons(db, settings, rule, account, execution["_id"], sender_id),
        )
    except MetaTokenError as exc:
        await InstagramAccountsRepository(db).set_status(account["_id"], "token_error", str(exc))
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        return {"status": "failed", "reason": "token_error"}
    except (MetaAPIError, EncryptionError) as exc:
        await executions.complete(execution["_id"], status="failed", error=str(exc))
        return {"status": "failed", "reason": "meta_error"}

    # The window is already open, so a file needs no opening-DM dance here.
    await send_attachment_if_any(client, settings, account, sender_id, rule, log)

    await executions.complete(
        execution["_id"],
        status="success",
        message_text=sent_text,
        meta_message_id=result.message_id,
        meta_recipient_id=result.recipient_id,
    )
    await events.mark_processed(event["_id"])
    logger.info("message_reply_sent", extra=log)
    return {"status": "success", "trigger": trigger, "execution_id": str(execution["_id"])}


async def _send_follow_prompt(
    client: Any,
    settings: Any,
    account: dict[str, Any],
    recipient_id: str,
    rule: dict[str, Any],
    log: dict[str, Any],
) -> None:
    """Tell them why the link is being withheld. Silence teaches nothing."""
    prompt = ((rule.get("require_follow") or {}).get("prompt") or "").strip()
    if not prompt:
        return
    try:
        token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
        await client.send_direct_message(token, account["instagram_user_id"], recipient_id, prompt)
        logger.info("follow_prompt_sent", extra=log)
    except (MetaAPIError, EncryptionError) as exc:
        logger.warning("follow_prompt_failed", extra={**log, "error": str(exc)})


async def _with_disclosure(db: Any, message: str) -> str:
    """Append the automated-experience disclosure, if one is configured.

    Meta requires disclosing an automated experience to users in California and
    Germany. The obligation is the operator's, so the wording is theirs and it
    can be switched off -- but silence by default would be the wrong default.
    """
    saved = await AppSettingsRepository(db).load()
    if not saved.get("automation_disclosure_enabled", True):
        return message
    line = str(saved.get("automation_disclosure") or "Automated reply.").strip()
    return f"{message}\n\n{line}" if line else message


async def process_message_event(
    ctx: dict[str, Any],
    webhook_event_id: str,
    instagram_account_id: str,
    sender_id: str,
    text: str,
    mid: str = "",
    delayed: bool = False,
) -> dict[str, Any]:
    """An inbound DM: a reply to our story, or a plain message.

    Two jobs, in a fixed order that matters. First, completing the "collect
    email" flow. Only if nobody is parked does it try to match a `story_reply`
    or `dm_keyword` rule.

    That order is the whole safety argument. Someone parked at `awaiting_email`
    who sends "LINK me@example.com" must resume the email flow and NOT also fire
    a keyword rule. `once_per_person` would not save us: it counts `success` and
    `dry_run` rows, and a parked execution is neither.

    `mid` defaults to empty because ARQ jobs queued before this argument existed
    carry only four; without a default they would raise on resume.
    """
    db = ctx["db"]
    settings = ctx["settings"]
    client = ctx["client"]

    accounts = InstagramAccountsRepository(db)
    account = await accounts.get_by_instagram_user_id(instagram_account_id)
    if account is None:
        return {"status": "skipped", "reason": "unknown_account"}

    # Our own message coming back. The endpoint filters these too; this is the
    # second guard, because replying to ourselves would loop and a message-id
    # reservation cannot stop it -- every echo carries a fresh mid.
    if sender_id and sender_id == str(account.get("instagram_user_id") or ""):
        return {"status": "skipped", "reason": "self_message"}

    executions = ExecutionsRepository(db)
    execution = await executions.find_awaiting_email(account["_id"], sender_id)
    if execution is None:
        return await _match_inbound_message(
            ctx, webhook_event_id, account, sender_id, text, mid, delayed
        )

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
        access_token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)
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
