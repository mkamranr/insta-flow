"""Post rules and synced posts.

A rule is: on this post, if someone comments this keyword, send them this
message. One row, one screen, nothing to keep in sync.
"""

import logging
from typing import Any

from fastapi import APIRouter, Query, Request, status

from app.api.deps import (
    AccountsRepo,
    AuditRepo,
    CurrentUser,
    MediaRepo,
    RulesRepo,
    SettingsDep,
)
from app.api.uploads import upload_root
from app.db.repositories import serialise_required, to_object_id, utcnow
from app.errors import MetaAPIError, NotFoundError, ValidationError
from app.models.rules import MessagePreviewRequest, PostPublic, RuleCreate, RuleUpdate
from app.services.attachments import delete as delete_attachment
from app.services.campaigns import ALLOWED_VARIABLES, render, validate
from app.services.meta.factory import build_client
from app.services.security.encryption import decrypt

logger = logging.getLogger(__name__)

posts_router = APIRouter(prefix="/api/posts", tags=["posts"])
router = APIRouter(prefix="/api/rules", tags=["rules"])


def _object_id(value: str, field: str) -> Any:
    try:
        return to_object_id(value)
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid id.") from exc


async def _only_account(accounts: Any, user: dict[str, Any]) -> dict[str, Any]:
    """The signed-in user's connected account.

    Single-account by design, but scoped by user_id rather than taking the first
    row — otherwise a second admin would silently operate on someone else's
    Instagram account.
    """
    found = await accounts.list({"user_id": user["_id"]})
    if not found:
        raise ValidationError("No Instagram account is connected yet.")
    return found[0]


# --- posts ------------------------------------------------------------------


@posts_router.get("", response_model=list[PostPublic])
async def list_posts(
    user: CurrentUser,
    accounts: AccountsRepo,
    media: MediaRepo,
    rules: RulesRepo,
    limit: int = Query(0, ge=0, le=200),
    offset: int = Query(0, ge=0),
) -> list[PostPublic]:
    """Newest first.

    `limit=0` means "as many as the repository will give", which is its own
    ceiling of 200 -- not literally everything. The pages that render a full
    grid rely on that; the dashboard rail passes a page size and walks `offset`
    as it scrolls.
    """
    account = await _only_account(accounts, user)
    counts = await rules.counts_by_media(account["_id"])
    posts = await media.list(
        {"instagram_account_id": account["_id"]},
        sort_field="published_at",
        limit=limit or 200,
        skip=offset,
    )
    return [
        PostPublic.from_document(p, counts.get(p.get("instagram_media_id", ""), 0)) for p in posts
    ]


@posts_router.post("/sync")
async def sync_posts(
    request: Request,
    user: CurrentUser,
    settings: SettingsDep,
    accounts: AccountsRepo,
    media: MediaRepo,
    audit: AuditRepo,
) -> dict[str, int]:
    """Fetch recent posts from Instagram so they can be picked in the UI.

    Upserts on (account, instagram_media_id), so re-syncing refreshes captions
    and thumbnails without duplicating posts or orphaning existing rules.
    """
    account = await _only_account(accounts, user)
    token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)

    try:
        fetched = await build_client(settings).get_media(
            token, account["instagram_user_id"], limit=50
        )
    except MetaAPIError as exc:
        logger.error("media_sync_failed", extra={"error": str(exc)})
        raise

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
                "published_at": item.published_at,
                "like_count": item.like_count,
                "comments_count": item.comments_count,
                "updated_at": utcnow(),
            },
        )

    await audit.record(
        user_id=user["_id"],
        action="media.synced",
        entity_type="instagram_account",
        entity_id=account["_id"],
        changes={"count": len(fetched)},
        ip_address=request.client.host if request.client else None,
    )
    logger.info("media_synced", extra={"count": len(fetched)})
    return {"synced": len(fetched)}


# --- rules ------------------------------------------------------------------


def _refuse_unsupported_features(trigger: str, values: dict[str, Any]) -> None:
    """Reject combinations that cannot work, rather than shipping dead settings.

    An opening DM exists to open a messaging window a private reply cannot. For
    a story reply or an inbound DM the person's own message already opened one,
    so the opener is pure latency -- and it is broken besides: its button
    payload carries a comment id, and the postback handler refuses to proceed
    without that comment row.

    A public reply is `POST /{comment_id}/replies`. No comment, no reply.

    `any_word` on a DM trigger would answer every message anyone sends,
    including real customer questions. `process_message_event` says it plainly:
    this is not a chatbot.
    """
    if trigger == "comment":
        return

    label = "story reply" if trigger == "story_reply" else "DM keyword"
    if (values.get("opening_dm") or {}).get("enabled"):
        raise ValidationError(
            f"An opening DM only applies to comments. A {label} automation replies "
            "directly, because their message already opened the window."
        )
    if (values.get("public_reply") or {}).get("enabled"):
        raise ValidationError(
            f"A public reply needs a comment to reply to, so it cannot apply to a {label}."
        )
    if trigger == "dm_keyword" and values.get("any_word"):
        raise ValidationError(
            "'Any word' would reply to every message anyone sends you, including real "
            "questions. Give this automation at least one keyword."
        )


@router.get("/intents")
async def list_intents(user: CurrentUser, settings: SettingsDep) -> dict[str, Any]:
    """Intents the classifier may return, for the rule editor."""
    from app.services.ai.classifier import INTENTS

    return {
        "intents": [i for i in INTENTS if i != "unknown"],
        "ai_enabled": settings.ai_enabled,
        "model": settings.ai_model if settings.ai_enabled else None,
    }


@router.get("/variables")
async def list_variables(user: CurrentUser) -> dict[str, list[str]]:
    return {"variables": sorted(ALLOWED_VARIABLES)}


@router.post("/preview")
async def preview(payload: MessagePreviewRequest, user: CurrentUser) -> dict[str, str]:
    """Render against sample values so the editor shows the real message."""
    context = payload.model_dump(exclude={"message"})
    context["campaign_name"] = ""
    context["link"] = "https://example.com/your-link"
    return {"rendered": render(payload.message, context)}


@router.get("")
async def list_rules(
    user: CurrentUser,
    accounts: AccountsRepo,
    rules: RulesRepo,
    instagram_media_id: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    account = await _only_account(accounts, user)
    if instagram_media_id:
        found = await rules.list_for_media(account["_id"], instagram_media_id)
    else:
        found = await rules.list_for_account(account["_id"])
    return [serialise_required(r) for r in found]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: RuleCreate,
    request: Request,
    user: CurrentUser,
    accounts: AccountsRepo,
    media: MediaRepo,
    rules: RulesRepo,
    audit: AuditRepo,
) -> dict[str, Any]:
    account = await _only_account(accounts, user)

    # Validate the message now, so a broken one can never reach the worker where
    # it would only surface when a real comment arrived.
    validate(payload.message)
    if payload.opening_dm.enabled and payload.opening_dm.message.strip():
        validate(payload.opening_dm.message)

    # Only a comment rule targets posts. A story reply or an inbound DM is not
    # attached to one, so requiring a post would make those rules impossible.
    if payload.trigger == "comment" and payload.scope == "specific":
        if not payload.instagram_media_ids:
            raise ValidationError("Select at least one post, or set the rule to every post.")
        for media_id in payload.instagram_media_ids:
            if await media.get_by_instagram_media_id(account["_id"], media_id) is None:
                raise ValidationError(f"Post {media_id} is not synced. Sync posts and retry.")

    if not payload.any_word and not payload.keywords:
        raise ValidationError("Add at least one keyword, or switch on 'any word'.")

    _refuse_unsupported_features(payload.trigger, payload.model_dump())

    document = payload.model_dump()
    document["instagram_account_id"] = account["_id"]
    rule_id = await rules.insert(document)

    await audit.record(
        user_id=user["_id"],
        action="rule.created",
        entity_type="rule",
        entity_id=rule_id,
        changes={"keywords": payload.keywords, "scope": payload.scope},
        ip_address=request.client.host if request.client else None,
    )
    logger.info("rule_created", extra={"rule_id": str(rule_id), "keywords": payload.keywords})
    return serialise_required(await rules.get(rule_id))


@router.get("/{rule_id}")
async def get_rule(rule_id: str, user: CurrentUser, rules: RulesRepo) -> dict[str, Any]:
    rule = await rules.get(_object_id(rule_id, "rule_id"))
    if rule is None:
        raise NotFoundError("Rule not found.")
    return serialise_required(rule)


@router.patch("/{rule_id}")
async def update_rule(
    rule_id: str,
    payload: RuleUpdate,
    request: Request,
    user: CurrentUser,
    rules: RulesRepo,
    audit: AuditRepo,
    settings: SettingsDep,
) -> dict[str, Any]:
    existing = await rules.get(_object_id(rule_id, "rule_id"))
    if existing is None:
        raise NotFoundError("Rule not found.")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise ValidationError("No fields to update.")

    # Swapping or clearing an attachment orphans the old file. Nothing else ever
    # references it, so it would sit on disk forever -- up to 25MB a time.
    if "attachment" in changes:
        old_key = ((existing.get("attachment") or {}) or {}).get("storage_key")
        new_key = (changes.get("attachment") or {}).get("storage_key")
        if old_key and old_key != new_key:
            delete_attachment(upload_root(settings), old_key)
    if "message" in changes:
        validate(changes["message"])
    if changes.get("keywords") == [] and not changes.get("any_word"):
        raise ValidationError("A rule needs at least one keyword, or 'any word'.")

    # Merged over the stored rule: a PATCH that only flips `any_word` still has
    # to be judged against the trigger the rule already has.
    merged = {**existing, **changes}
    _refuse_unsupported_features(str(merged.get("trigger") or "comment"), merged)

    updated = await rules.update(_object_id(rule_id, "rule_id"), changes)
    await audit.record(
        user_id=user["_id"],
        action="rule.updated",
        entity_type="rule",
        entity_id=to_object_id(rule_id),
        changes={k: str(v) for k, v in changes.items()},
        ip_address=request.client.host if request.client else None,
    )
    return serialise_required(updated)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: str,
    request: Request,
    user: CurrentUser,
    rules: RulesRepo,
    audit: AuditRepo,
    settings: SettingsDep,
) -> None:
    doomed = await rules.get(_object_id(rule_id, "rule_id"))
    if not await rules.delete(_object_id(rule_id, "rule_id")):
        raise NotFoundError("Rule not found.")
    if doomed:
        delete_attachment(
            upload_root(settings), ((doomed.get("attachment") or {}) or {}).get("storage_key")
        )
    await audit.record(
        user_id=user["_id"],
        action="rule.deleted",
        entity_type="rule",
        entity_id=to_object_id(rule_id),
        ip_address=request.client.host if request.client else None,
    )
    logger.info("rule_deleted", extra={"rule_id": rule_id})
