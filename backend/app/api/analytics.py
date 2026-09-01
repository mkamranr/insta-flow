"""Dashboard metrics and the activity log.

§59: report only what the data actually supports. Instagram does not tell us
whether a DM was delivered or read, so there is no delivery or read rate here —
only what we sent, and what was clicked through our own redirect.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import (
    AccountsRepo,
    CommentsRepo,
    ContactsRepo,
    CurrentUser,
    ExecutionsRepo,
    LinkClicksRepo,
    MediaRepo,
    RulesRepo,
)
from app.db.repositories import serialise_required

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])

SENT_ACTIONS = ("private_reply", "opening_dm")


async def _account_id(accounts: Any, user: dict[str, Any]) -> Any:
    """The signed-in user's account. Scoped so metrics cannot leak across users."""
    found = await accounts.list({"user_id": user["_id"]})
    return found[0]["_id"] if found else None


@router.get("/summary")
async def summary(
    user: CurrentUser,
    accounts: AccountsRepo,
    comments: CommentsRepo,
    executions: ExecutionsRepo,
    links: LinkClicksRepo,
    rules: RulesRepo,
    contacts: ContactsRepo,
    days: int = Query(default=7, ge=1, le=365),
) -> dict[str, Any]:
    account_id = await _account_id(accounts, user)
    if account_id is None:
        return {"connected": False}

    since = datetime.now(UTC) - timedelta(days=days)
    window = {"instagram_account_id": account_id, "created_at": {"$gte": since}}

    by_status = await executions.count_by_status(window)
    sent = by_status.get("success", 0)
    clicks = await links.count_clicks(
        {"instagram_account_id": account_id, "created_at": {"$gte": since}}
    )
    total_comments = await comments.count(window)
    matched = sum(by_status.values())

    return {
        "connected": True,
        "days": days,
        "comments": total_comments,
        "matched": matched,
        # A comment with no execution matched no rule.
        "unmatched": max(total_comments - matched, 0),
        "dms_sent": sent,
        "failed": by_status.get("failed", 0),
        "skipped": by_status.get("skipped", 0),
        "dry_runs": by_status.get("dry_run", 0),
        "link_clicks": clicks,
        # Clicks per DM sent. Can exceed 100%: one person may click twice.
        "click_through_rate": round(clicks / sent * 100, 1) if sent else 0.0,
        "leads_captured": await contacts.count(
            {"instagram_account_id": account_id, "created_at": {"$gte": since}}
        ),
        "active_rules": await rules.count({"instagram_account_id": account_id, "enabled": True}),
        "total_rules": await rules.count({"instagram_account_id": account_id}),
    }


@router.get("/rules")
async def rule_performance(
    user: CurrentUser,
    accounts: AccountsRepo,
    executions: ExecutionsRepo,
    links: LinkClicksRepo,
    rules: RulesRepo,
) -> list[dict[str, Any]]:
    """Per-rule DMs and clicks, so a rule that is not working is visible."""
    account_id = await _account_id(accounts, user)
    if account_id is None:
        return []

    clicks_by_rule = await links.clicks_by_rule(account_id)
    out: list[dict[str, Any]] = []

    for rule in await rules.list_for_account(account_id):
        rule_id = str(rule["_id"])
        sent = await executions.count(
            {"rule_id": rule["_id"], "status": "success", "action": {"$in": list(SENT_ACTIONS)}}
        )
        clicks = clicks_by_rule.get(rule_id, 0)
        out.append(
            {
                "id": rule_id,
                "name": rule.get("name") or "Comment → DM",
                "keywords": rule.get("keywords") or [],
                "enabled": rule.get("enabled", True),
                "dms_sent": sent,
                "link_clicks": clicks,
                "click_through_rate": round(clicks / sent * 100, 1) if sent else 0.0,
            }
        )

    out.sort(key=lambda r: -r["dms_sent"])
    return out


@router.get("/activity")
async def activity(
    user: CurrentUser,
    accounts: AccountsRepo,
    executions: ExecutionsRepo,
    comments: CommentsRepo,
    rules: RulesRepo,
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """Recent executions, joined to the comment that triggered them."""
    account_id = await _account_id(accounts, user)
    if account_id is None:
        return []

    query: dict[str, Any] = {"instagram_account_id": account_id}
    if status:
        query["status"] = status

    rows = await executions.list(query, limit=limit)
    rule_names = {
        str(r["_id"]): r.get("name") or "Comment → DM"
        for r in await rules.list_for_account(account_id)
    }

    out: list[dict[str, Any]] = []
    for row in rows:
        comment = await comments.get(row["comment_id"]) if row.get("comment_id") else None
        out.append(
            {
                "id": str(row["_id"]),
                "status": row.get("status"),
                "action": row.get("action"),
                "dry_run": row.get("dry_run", False),
                "username": (comment or {}).get("username", ""),
                "comment_text": (comment or {}).get("text", ""),
                "message_text": row.get("message_text"),
                "rule_name": rule_names.get(str(row.get("rule_id")), "(deleted rule)"),
                "policy_decision": row.get("policy_decision"),
                "error": row.get("error"),
                "attempts": row.get("attempts", 0),
                "created_at": row.get("created_at"),
            }
        )
    return out


@router.get("/top-commenters")
async def top_commenters(
    user: CurrentUser,
    accounts: AccountsRepo,
    comments: CommentsRepo,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[dict[str, Any]]:
    account_id = await _account_id(accounts, user)
    if account_id is None:
        return []

    pipeline: list[dict[str, Any]] = [
        {"$match": {"instagram_account_id": account_id, "username": {"$ne": ""}}},
        {
            "$group": {
                "_id": "$username",
                "comments": {"$sum": 1},
                "last_at": {"$max": "$commented_at"},
            }
        },
        {"$sort": {"comments": -1, "_id": 1}},
        {"$limit": limit},
    ]
    return [
        {"username": doc["_id"], "comments": doc["comments"], "last_at": doc.get("last_at")}
        async for doc in await comments.collection.aggregate(pipeline)
    ]


@router.get("/posts")
async def post_performance(
    user: CurrentUser, accounts: AccountsRepo, media: MediaRepo, comments: CommentsRepo
) -> list[dict[str, Any]]:
    account_id = await _account_id(accounts, user)
    if account_id is None:
        return []

    out = []
    for post in await media.list({"instagram_account_id": account_id}, sort_field="published_at"):
        out.append(
            {
                **serialise_required(post),
                "comments": await comments.count(
                    {"instagram_media_id": post.get("instagram_media_id")}
                ),
            }
        )
    return out


@router.get("/contacts")
async def contacts_list(
    user: CurrentUser, accounts: AccountsRepo, contacts: ContactsRepo
) -> list[dict[str, Any]]:
    """Captured leads. Emails are shown to the operator who collected them."""
    account_id = await _account_id(accounts, user)
    if account_id is None:
        return []
    return [serialise_required(c) for c in await contacts.list_for_account(account_id)]
