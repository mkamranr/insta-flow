"""Post rules and synced posts.

A rule is: on this post, if someone comments this keyword, send them this
message. One row, one screen, nothing to keep in sync.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pymongo.errors import DuplicateKeyError

from app import queue
from app.api.deps import (
    AccountsRepo,
    AuditRepo,
    CurrentUser,
    CurrentWorkspace,
    MediaRepo,
    RulesRepo,
    SettingsDep,
    StoriesRepo,
    UpcomingRepo,
    Workspace,
    WorkspaceCredentials,
    require,
)
from app.api.uploads import upload_root
from app.db.repositories import serialise, serialise_required, to_object_id, utcnow
from app.errors import MetaAPIError, NotFoundError, ValidationError
from app.models.rules import (
    MessagePreviewRequest,
    PostPublic,
    RuleCloneRequest,
    RuleCreate,
    RuleUpdate,
    StoryPublic,
)
from app.services.attachments import delete as delete_attachment
from app.services.campaigns import ALLOWED_VARIABLES, render, validate
from app.services.meta.factory import build_client
from app.services.security.encryption import decrypt

logger = logging.getLogger(__name__)

posts_router = APIRouter(prefix="/api/posts", tags=["posts"])
stories_router = APIRouter(prefix="/api/stories", tags=["stories"])
upcoming_router = APIRouter(prefix="/api/upcoming", tags=["upcoming"])
router = APIRouter(prefix="/api/rules", tags=["rules"])


def _object_id(value: str, field: str) -> Any:
    try:
        return to_object_id(value)
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid id.") from exc


async def _only_account(accounts: Any, workspace: Workspace) -> dict[str, Any]:
    """This workspace's connected account.

    Single-account by design, but scoped by the workspace's owner rather than
    taking the first row — otherwise a second admin would silently operate on
    someone else's Instagram account.

    The workspace is usually the caller's own. It is somebody else's only when the
    path said so and `current_workspace` allowed it, which costs `workspaces.view`.
    """
    found = await accounts.live_for_user(workspace.user_id)
    if not found:
        raise ValidationError("No Instagram account is connected yet.")
    return found[0]


async def _load_rule(rules: Any, rule_id: str, account: dict[str, Any]) -> dict[str, Any]:
    """Load a rule belonging to this account.

    A rule that does not exist and a rule belonging to someone else both raise
    NotFoundError, so the response cannot be used to discover which ids exist --
    the same rule `instagram.py::_load` follows for accounts.
    """
    rule = await rules.get(_object_id(rule_id, "rule_id"))
    if rule is None or rule.get("instagram_account_id") != account["_id"]:
        raise NotFoundError("Rule not found.")
    return rule


async def _drop_attachment_if_unused(
    rules: Any, settings: Any, storage_key: str | None, rule_id: Any
) -> None:
    """Delete a stored attachment only when no other rule still points at it.

    `attachments.delete` removes the whole upload folder, so an unconditional
    delete would take the file out from under any rule sharing the key -- which
    the Duplicate button on the Automations page has been creating all along, by
    copying `attachment` verbatim. The survivor's URL would then 404, and
    `send_attachment_if_any` only logs a warning, so the recipient gets the text
    and silently no file.
    """
    if not storage_key:
        return
    if await rules.count_referencing_attachment(storage_key, rule_id):
        logger.info("attachment_kept_still_referenced", extra={"storage_key": storage_key})
        return
    delete_attachment(upload_root(settings), storage_key)


async def _validate_and_insert(
    payload: RuleCreate, account: dict[str, Any], media: Any, rules: Any, stories: Any
) -> Any:
    """Validate a rule and write it. Shared by `create_rule` and `clone_rule`.

    Extracted so the clone path cannot drift from the create path: a check that
    lives in only one of them is a check the other silently skips.
    """
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

    await _check_story_targets(payload.trigger, payload.instagram_story_ids, account, stories)
    _refuse_unsupported_features(payload.trigger, payload.model_dump())

    # A fresh dict every call, deliberately. `BaseRepository.insert` mutates what
    # it is given and pymongo writes `_id` back into it, so a dict reused across
    # a clone loop would carry the first insert's `_id` into every later one.
    document = payload.model_dump()
    document["instagram_account_id"] = account["_id"]
    return await rules.insert(document)


# --- upcoming post ----------------------------------------------------------


@upcoming_router.get("", dependencies=[Depends(require("automations.view"))])
async def get_upcoming(
    workspace: CurrentWorkspace, accounts: AccountsRepo, upcoming: UpcomingRepo
) -> dict[str, Any]:
    """The automation waiting for the next post, and the last few applied."""
    account = await _only_account(accounts, workspace)
    pending = await upcoming.pending_for(account["_id"])
    return {
        "pending": serialise(pending) if pending else None,
        "history": [serialise_required(h) for h in await upcoming.history(account["_id"])],
    }


@upcoming_router.post(
    "", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require("automations.create"))]
)
async def create_upcoming(
    payload: RuleCreate,
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    upcoming: UpcomingRepo,
    audit: AuditRepo,
) -> dict[str, Any]:
    """Save an automation to arm on the next post published from now.

    Validated exactly as a rule is, because that is what it becomes -- catching
    a broken message here rather than at 3am when the post goes live.
    """
    account = await _only_account(accounts, workspace)

    if payload.trigger != "comment":
        raise ValidationError(
            "An upcoming-post automation answers comments on the post itself, "
            "so it cannot use a story or DM trigger."
        )
    validate(payload.message)
    if payload.opening_dm.enabled and payload.opening_dm.message.strip():
        validate(payload.opening_dm.message)
    if not payload.any_word and not payload.keywords:
        raise ValidationError("Add at least one keyword, or switch on 'any word'.")

    template = payload.model_dump()
    # The post is not known yet; these are filled in when it is.
    template.pop("instagram_media_ids", None)

    try:
        upcoming_id = await upcoming.insert(
            {
                "instagram_account_id": account["_id"],
                "pending": True,
                "status": "waiting",
                "rule": template,
            }
        )
    except DuplicateKeyError as exc:
        # The partial unique index is the authority on "only one at a time".
        raise ValidationError(
            "An automation is already waiting for your next post. "
            "Remove it first, or edit the one you have."
        ) from exc

    await audit.record(
        user_id=user["_id"],
        action="upcoming.created",
        entity_type="upcoming_post",
        entity_id=upcoming_id,
        ip_address=request.client.host if request.client else None,
    )
    return {"id": str(upcoming_id)}


@upcoming_router.delete(
    "/{upcoming_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require("automations.delete"))],
)
async def delete_upcoming(
    upcoming_id: str,
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    upcoming: UpcomingRepo,
    audit: AuditRepo,
) -> None:
    account = await _only_account(accounts, workspace)
    record = await upcoming.get(_object_id(upcoming_id, "upcoming_id"))
    if record is None or record.get("instagram_account_id") != account["_id"]:
        raise NotFoundError("No upcoming automation found.")
    await upcoming.delete(_object_id(upcoming_id, "upcoming_id"))
    await audit.record(
        user_id=user["_id"],
        action="upcoming.deleted",
        entity_type="upcoming_post",
        entity_id=to_object_id(upcoming_id),
        ip_address=request.client.host if request.client else None,
    )


# --- stories ----------------------------------------------------------------


@stories_router.get(
    "", response_model=list[StoryPublic], dependencies=[Depends(require("content.view"))]
)
async def list_stories(
    workspace: CurrentWorkspace, accounts: AccountsRepo, stories: StoriesRepo, rules: RulesRepo
) -> list[StoryPublic]:
    """Active stories, newest first, with how many rules target each.

    Only active ones: a story expires after 24 hours, and offering an expired
    one in the picker would create a rule that can never match.
    """
    account = await _only_account(accounts, workspace)
    found = await stories.list_active(account["_id"])
    counts = await rules.counts_by_story(account["_id"])
    return [
        StoryPublic.from_document(s, counts.get(s.get("instagram_story_id", ""), 0)) for s in found
    ]


@stories_router.post("/sync", dependencies=[Depends(require("content.edit"))])
async def sync_stories_now(
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    creds: WorkspaceCredentials,
    settings: SettingsDep,
    accounts: AccountsRepo,
    stories: StoriesRepo,
    rules: RulesRepo,
    audit: AuditRepo,
) -> dict[str, int]:
    """Refresh the story list now, rather than waiting for the hourly job.

    Meta returns only active stories, so anything stored and no longer listed
    has expired -- and rules that opted into `expire_with_story` are disabled
    here, not left enabled and quietly unmatchable.
    """
    account = await _only_account(accounts, workspace)
    token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)

    try:
        fetched = await build_client(settings, creds).get_stories(
            token, account["instagram_user_id"]
        )
    except MetaAPIError as exc:
        logger.error("story_sync_failed", extra={"error": str(exc)})
        raise

    for item in fetched:
        await stories.upsert(
            account["_id"],
            item.instagram_media_id,
            {
                "instagram_account_id": account["_id"],
                "instagram_story_id": item.instagram_media_id,
                "media_type": item.media_type,
                "caption": item.caption,
                "permalink": item.permalink,
                "thumbnail_url": item.thumbnail_url,
                "media_url": item.media_url,
                "published_at": item.published_at,
                "updated_at": utcnow(),
            },
        )

    gone = await stories.mark_expired(account["_id"], [item.instagram_media_id for item in fetched])
    disabled = await rules.disable_for_expired_stories(account["_id"], gone)

    await audit.record(
        user_id=user["_id"],
        action="stories.synced",
        entity_type="instagram_account",
        entity_id=account["_id"],
        changes={"active": len(fetched), "expired": len(gone), "disabled": len(disabled)},
        ip_address=request.client.host if request.client else None,
    )
    return {"active": len(fetched), "expired": len(gone), "rules_disabled": len(disabled)}


# --- posts ------------------------------------------------------------------


@posts_router.get(
    "", response_model=list[PostPublic], dependencies=[Depends(require("content.view"))]
)
async def list_posts(
    workspace: CurrentWorkspace,
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
    account = await _only_account(accounts, workspace)
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


@posts_router.post("/sync", dependencies=[Depends(require("content.edit"))])
async def sync_posts(
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    creds: WorkspaceCredentials,
    settings: SettingsDep,
    accounts: AccountsRepo,
    media: MediaRepo,
    audit: AuditRepo,
) -> dict[str, int]:
    """Fetch recent posts from Instagram so they can be picked in the UI.

    Upserts on (account, instagram_media_id), so re-syncing refreshes captions
    and thumbnails without duplicating posts or orphaning existing rules.
    """
    account = await _only_account(accounts, workspace)
    token = decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)

    try:
        fetched = await build_client(settings, creds).get_media(
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
                "media_url": item.media_url,
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
    # A hand-pressed Refresh must arm the waiting template too. Otherwise the
    # feature would only work on the cron, and pressing Refresh right after
    # posting -- the obvious thing to do -- would appear to do nothing.
    from app.workers.tasks import apply_upcoming_post

    armed = await apply_upcoming_post(media.collection.database, account)

    logger.info("media_synced", extra={"count": len(fetched), "armed": armed})
    return {"synced": len(fetched), "armed": armed}


@posts_router.post("/resync", dependencies=[Depends(require("content.edit"))])
async def resync_all_posts(
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    audit: AuditRepo,
) -> dict[str, Any]:
    """Queue a full walk of the media edge, updating every post.

    `POST /sync` above stays deliberately cheap -- one page of the most recent
    posts, which is all the Refresh buttons and the 15-minute cron need. This is
    the manual escape hatch for the case that one cannot serve: an edit to a post
    old enough to have fallen out of the first page.

    Queued rather than done inline. A large account is hundreds of requests to
    Meta, which would hold an HTTP request open far past any sensible timeout,
    and the worker's own ceiling is 60s class-wide -- the job carries its own.

    `_job_id` makes a second press a no-op while the first is still running,
    which is the whole of the double-click story. ARQ discards a duplicate id,
    so this needs no lock and no status pre-check.
    """
    account = await _only_account(accounts, workspace)

    await queue.enqueue(
        "resync_all_media",
        str(account["_id"]),
        _job_id=f"media-resync:{account['_id']}",
    )
    await audit.record(
        user_id=user["_id"],
        action="instagram.media_resync_queued",
        entity_type="instagram_account",
        entity_id=account["_id"],
        ip_address=request.client.host if request.client else None,
    )
    logger.info("media_resync_queued", extra={"account_id": str(account["_id"])})
    return {"queued": True}


# --- rules ------------------------------------------------------------------


async def _check_story_targets(
    trigger: str, story_ids: list[str], account: dict[str, Any], stories: Any
) -> None:
    """Story ids must belong to this account and still be live.

    An expired story cannot be replied to, so a rule pinned to one could never
    match. Refusing at save time is the only place the operator finds out
    before waiting for a reply that never comes.
    """
    if not story_ids:
        return
    if trigger != "story_reply":
        raise ValidationError("Only a story-reply automation can target specific stories.")
    for story_id in story_ids:
        story = await stories.get_by_instagram_story_id(account["_id"], story_id)
        if story is None:
            raise ValidationError(f"Story {story_id} is not synced. Refresh stories and retry.")
        if story.get("expired_at") is not None:
            raise ValidationError(
                "That story has expired, so nobody can reply to it any more. "
                "Pick a live story, or let the automation answer replies to every story."
            )


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

    The email gate is checked first, because it is the one combination that is
    dead on a comment trigger too. The prompt is sent from the postback handler,
    which only runs when someone taps the button on an opening DM, so a rule
    that answers the comment directly asks nobody for anything. It saved
    cleanly and did nothing, which is exactly what this function exists to stop.
    """
    if (values.get("collect_email") or {}).get("enabled"):
        if trigger != "comment":
            label = "story reply" if trigger == "story_reply" else "DM keyword"
            raise ValidationError(
                f"Asking for an email only applies to comments, so it cannot apply to a {label}."
            )
        opening = values.get("opening_dm") or {}
        # Enabled *and* non-blank, matching `uses_opening_dm` in the worker: an
        # empty opener sends nothing, so it carries no button to tap.
        if not (opening.get("enabled") and str(opening.get("message") or "").strip()):
            raise ValidationError(
                "Asking for an email needs the opening DM: the prompt is sent when they tap "
                "its button. Turn on the opening DM, or switch off the email request."
            )

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


@router.get("/intents", dependencies=[Depends(require("automations.view"))])
async def list_intents(user: CurrentUser, settings: SettingsDep) -> dict[str, Any]:
    """Intents the classifier may return, for the rule editor."""
    from app.services.ai.classifier import INTENTS

    return {
        "intents": [i for i in INTENTS if i != "unknown"],
        "ai_enabled": settings.ai_enabled,
        "model": settings.ai_model if settings.ai_enabled else None,
    }


@router.get("/variables", dependencies=[Depends(require("automations.view"))])
async def list_variables(user: CurrentUser) -> dict[str, list[str]]:
    return {"variables": sorted(ALLOWED_VARIABLES)}


@router.post("/preview", dependencies=[Depends(require("automations.view"))])
async def preview(payload: MessagePreviewRequest, user: CurrentUser) -> dict[str, str]:
    """Render against sample values so the editor shows the real message."""
    context = payload.model_dump(exclude={"message"})
    context["campaign_name"] = ""
    context["link"] = "https://example.com/your-link"
    return {"rendered": render(payload.message, context)}


@router.get("", dependencies=[Depends(require("automations.view"))])
async def list_rules(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    rules: RulesRepo,
    instagram_media_id: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    account = await _only_account(accounts, workspace)
    if instagram_media_id:
        found = await rules.list_for_media(account["_id"], instagram_media_id)
    else:
        found = await rules.all_for_account(account["_id"])
    return [serialise_required(r) for r in found]


@router.post(
    "", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require("automations.create"))]
)
async def create_rule(
    payload: RuleCreate,
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    media: MediaRepo,
    rules: RulesRepo,
    stories: StoriesRepo,
    audit: AuditRepo,
) -> dict[str, Any]:
    account = await _only_account(accounts, workspace)
    rule_id = await _validate_and_insert(payload, account, media, rules, stories)

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


@router.post(
    "/{rule_id}/clone",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require("automations.create"))],
)
async def clone_rule(
    rule_id: str,
    payload: RuleCloneRequest,
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    media: MediaRepo,
    rules: RulesRepo,
    stories: StoriesRepo,
    audit: AuditRepo,
) -> dict[str, Any]:
    """Copy an automation onto other posts, one independent rule per post.

    An independent copy rather than adding posts to the source rule's target
    list: each clone then carries its own stats, its own send limits and its own
    "once per person" ledger, and can be edited without touching the others.

    The source is left exactly as it was.
    """
    account = await _only_account(accounts, workspace)
    source = await _load_rule(rules, rule_id, account)

    trigger = str(source.get("trigger") or "comment")
    if trigger != "comment":
        # `list_for_media` filters on trigger, so a cloned story-reply or
        # DM-keyword rule's media ids would never be read by anything.
        raise ValidationError(
            "Only comment automations are attached to a post, so only those can be cloned."
        )
    if source.get("scope") == "any":
        # Cloning would produce redundant rules that shadow the original: equal
        # priority, and `select_rule` breaks the tie on the newer _id.
        raise ValidationError(
            "This automation already runs on every post — there is nothing to clone it to."
        )

    # The source document carries fields the model does not (`_id`, timestamps,
    # `created_from_upcoming`, `disabled_reason`). Building the payload through
    # RuleCreate drops them, so a clone cannot inherit provenance that is not its.
    template = RuleCreate(**{k: v for k, v in source.items() if k in RuleCreate.model_fields})

    created: list[str] = []
    skipped: list[dict[str, str]] = []

    for media_id in dict.fromkeys(payload.instagram_media_ids):
        if media_id in (source.get("instagram_media_ids") or []):
            skipped.append({"instagram_media_id": media_id, "reason": "This is the source post."})
            continue
        if await rules.count_for_media(account["_id"], media_id):
            skipped.append(
                {"instagram_media_id": media_id, "reason": "That post already has an automation."}
            )
            continue

        # model_copy inside the loop: `_validate_and_insert` dumps a fresh dict
        # each call, which is what keeps pymongo from reusing the previous _id.
        clone = template.model_copy(update={"scope": "specific", "instagram_media_ids": [media_id]})
        created.append(str(await _validate_and_insert(clone, account, media, rules, stories)))

    if created:
        # A distinct action, so a bulk clone is one identifiable event in the
        # audit log rather than N indistinguishable hand-made rules.
        await audit.record(
            user_id=user["_id"],
            action="rule.cloned",
            entity_type="rule",
            entity_id=source["_id"],
            changes={"created": ",".join(created), "targets": str(len(created))},
            ip_address=request.client.host if request.client else None,
        )
    logger.info(
        "rule_cloned",
        # `created_count`, not `created`: `extra` may not shadow a LogRecord
        # attribute, and `created` is the record's own timestamp. It raised here
        # AFTER the clones were written, so the caller saw a 500 for a clone that
        # had in fact succeeded.
        extra={
            "source_rule_id": rule_id,
            "created_count": len(created),
            "skipped": len(skipped),
        },
    )
    return {"created": created, "skipped": skipped}


@router.get("/{rule_id}", dependencies=[Depends(require("automations.view"))])
async def get_rule(
    rule_id: str, workspace: CurrentWorkspace, accounts: AccountsRepo, rules: RulesRepo
) -> dict[str, Any]:
    account = await _only_account(accounts, workspace)
    return serialise_required(await _load_rule(rules, rule_id, account))


@router.patch("/{rule_id}", dependencies=[Depends(require("automations.edit"))])
async def update_rule(
    rule_id: str,
    payload: RuleUpdate,
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    rules: RulesRepo,
    stories: StoriesRepo,
    audit: AuditRepo,
    settings: SettingsDep,
) -> dict[str, Any]:
    account = await _only_account(accounts, workspace)
    existing = await _load_rule(rules, rule_id, account)

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise ValidationError("No fields to update.")

    # Swapping or clearing an attachment orphans the old file. Nothing else ever
    # references it, so it would sit on disk forever -- up to 25MB a time.
    if "attachment" in changes:
        old_key = ((existing.get("attachment") or {}) or {}).get("storage_key")
        new_key = (changes.get("attachment") or {}).get("storage_key")
        if old_key and old_key != new_key:
            await _drop_attachment_if_unused(rules, settings, old_key, existing["_id"])
    if "message" in changes:
        validate(changes["message"])
    if changes.get("keywords") == [] and not changes.get("any_word"):
        raise ValidationError("A rule needs at least one keyword, or 'any word'.")

    # Merged over the stored rule: a PATCH that only flips `any_word` still has
    # to be judged against the trigger the rule already has.
    merged = {**existing, **changes}
    _refuse_unsupported_features(str(merged.get("trigger") or "comment"), merged)
    if "instagram_story_ids" in changes:
        await _check_story_targets(
            str(merged.get("trigger") or "comment"),
            changes["instagram_story_ids"] or [],
            account,
            stories,
        )

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


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require("automations.delete"))],
)
async def delete_rule(
    rule_id: str,
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    rules: RulesRepo,
    audit: AuditRepo,
    settings: SettingsDep,
) -> None:
    account = await _only_account(accounts, workspace)
    # Loaded before the delete because the attachment path is only knowable from
    # the document, and `_load_rule` is what refuses another account's rule.
    doomed = await _load_rule(rules, rule_id, account)
    await rules.delete(_object_id(rule_id, "rule_id"))
    await _drop_attachment_if_unused(
        rules, settings, ((doomed.get("attachment") or {}) or {}).get("storage_key"), doomed["_id"]
    )
    await audit.record(
        user_id=user["_id"],
        action="rule.deleted",
        entity_type="rule",
        entity_id=to_object_id(rule_id),
        ip_address=request.client.host if request.client else None,
    )
    logger.info("rule_deleted", extra={"rule_id": rule_id})
