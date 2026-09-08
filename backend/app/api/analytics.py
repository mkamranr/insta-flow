"""Dashboard metrics and the activity log.

§59: report only what the data actually supports. Instagram does not tell us
whether a DM was delivered or read, so there is no delivery or read rate here —
only what we sent, and what was clicked through our own redirect.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import (
    AccountsRepo,
    ClickEventsRepo,
    CommentsRepo,
    ContactsRepo,
    CurrentWorkspace,
    ExecutionsRepo,
    InvitesRepo,
    LinkClicksRepo,
    MediaRepo,
    RulesRepo,
    SnapshotsRepo,
    UsersRepo,
    Workspace,
    require,
)
from app.db.repositories import serialise_required
from app.errors import NotFoundError
from app.services import exports
from app.services.superfans import (
    MIN_COMMENTS_PER_WEEK,
    MIN_CONSECUTIVE_WEEKS,
)
from app.services.superfans import find as find_superfans

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# `direct_message` is what story-reply and DM-keyword rules send. Omitting it
# would make every such rule report 0 DMs sent and 0% CTR forever.
SENT_ACTIONS = ("private_reply", "opening_dm", "direct_message")

_SUPERFAN_RULE = (
    f"{MIN_COMMENTS_PER_WEEK}+ comments in a week, in at least "
    f"{MIN_CONSECUTIVE_WEEKS} consecutive weeks"
)


async def _account_id(accounts: Any, workspace: Workspace) -> Any:
    """This workspace's account. Scoped so metrics cannot leak across workspaces."""
    found = await accounts.live_for_user(workspace.user_id)
    return found[0]["_id"] if found else None


@router.get("/summary", dependencies=[Depends(require("analytics.view"))])
async def summary(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    comments: CommentsRepo,
    executions: ExecutionsRepo,
    links: LinkClicksRepo,
    rules: RulesRepo,
    contacts: ContactsRepo,
    days: int = Query(default=7, ge=1, le=365),
) -> dict[str, Any]:
    account_id = await _account_id(accounts, workspace)
    if account_id is None:
        return {"connected": False}

    since = datetime.now(UTC) - timedelta(days=days)
    window = {"instagram_account_id": account_id, "created_at": {"$gte": since}}

    # Comment-triggered rows only: `unmatched` below subtracts this from the
    # comment count, and message-triggered executions would silently deflate it.
    by_status = await executions.count_by_status({**window, "comment_id": {"$exists": True}})
    sent = by_status.get("success", 0)
    window_links = {"instagram_account_id": account_id, "created_at": {"$gte": since}}
    clicks = await links.count_clicks(window_links)
    # Both sides of the rate share one filter, so this reads as a cohort: of the
    # links sent in the window, how many taps did they draw.
    delivered = await links.count(window_links)
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
        # Clicks per link *delivered*, not per DM sent. A two-step automation
        # sends two DMs for one comment and only the second carries a link, so
        # dividing by DMs read 50% for a commenter who clicked the one link they
        # were sent. Can still exceed 100%: one person may click twice.
        "click_through_rate": round(clicks / delivered * 100, 1) if delivered else 0.0,
        "leads_captured": await contacts.count(
            {"instagram_account_id": account_id, "created_at": {"$gte": since}}
        ),
        "active_rules": await rules.count({"instagram_account_id": account_id, "enabled": True}),
        "total_rules": await rules.count({"instagram_account_id": account_id}),
    }


@router.get("/rules", dependencies=[Depends(require("analytics.view"))])
async def rule_performance(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    executions: ExecutionsRepo,
    links: LinkClicksRepo,
    rules: RulesRepo,
) -> list[dict[str, Any]]:
    """Per-rule DMs and clicks, so a rule that is not working is visible."""
    account_id = await _account_id(accounts, workspace)
    if account_id is None:
        return []

    clicks_by_rule = await links.clicks_by_rule(account_id)
    delivered_by_rule = await links.delivered_by_rule(account_id)
    out: list[dict[str, Any]] = []

    for rule in await rules.list_for_account(account_id):
        rule_id = str(rule["_id"])
        sent = await executions.count(
            {"rule_id": rule["_id"], "status": "success", "action": {"$in": list(SENT_ACTIONS)}}
        )
        clicks = clicks_by_rule.get(rule_id, 0)
        delivered = delivered_by_rule.get(rule_id, 0)
        destinations = await links.by_destination_for_rule(rule["_id"])
        out.append(
            {
                "id": rule_id,
                "name": rule.get("name") or "Comment → DM",
                "keywords": rule.get("keywords") or [],
                "enabled": rule.get("enabled", True),
                "instagram_media_ids": rule.get("instagram_media_ids") or [],
                "dms_sent": sent,
                "link_clicks": clicks,
                # `sent` counts every DM, including the opener that carries a
                # postback button rather than a link. Only a delivered link can
                # be clicked, so only a delivered link belongs in the denominator.
                "click_through_rate": round(clicks / delivered * 100, 1) if delivered else 0.0,
                "links": [
                    {
                        **dest,
                        "last_clicked_at": (
                            dest["last_clicked_at"].isoformat()
                            if dest.get("last_clicked_at")
                            else None
                        ),
                        # Each destination is rated on its own deliveries. It
                        # used to share the rule's, which halved every button on
                        # a message that carried two.
                        "click_through_rate": (
                            round(dest["clicks"] / dest["delivered"] * 100, 1)
                            if dest["delivered"]
                            else 0.0
                        ),
                    }
                    for dest in destinations
                ],
            }
        )

    out.sort(key=lambda r: -r["dms_sent"])
    return out


@router.get("/activity", dependencies=[Depends(require("analytics.view"))])
async def activity(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    executions: ExecutionsRepo,
    comments: CommentsRepo,
    rules: RulesRepo,
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """Recent executions, joined to the comment that triggered them."""
    account_id = await _account_id(accounts, workspace)
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


@router.get("/superfans", dependencies=[Depends(require("analytics.view"))])
async def superfans(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    comments: CommentsRepo,
    weeks: int = Query(12, ge=2, le=52),
) -> dict[str, Any]:
    """People who comment repeatedly, week after week.

    The rule is returned alongside the rows so the UI states the same definition
    the server applied, rather than a paraphrase that can drift from it.
    """
    account_id = await _account_id(accounts, workspace)
    if account_id is None:
        return {"rows": [], "rule": _SUPERFAN_RULE}

    since = datetime.now(UTC) - timedelta(weeks=weeks)
    rows = await comments.weekly_counts(account_id, since)
    return {
        "rows": find_superfans(rows),
        "weeks_considered": weeks,
        "rule": _SUPERFAN_RULE,
    }


@router.get("/top-commenters", dependencies=[Depends(require("analytics.view"))])
async def top_commenters(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    comments: CommentsRepo,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[dict[str, Any]]:
    account_id = await _account_id(accounts, workspace)
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


def _densify(rows: list[dict[str, Any]], since: datetime, by_hour: bool) -> list[dict[str, Any]]:
    """Fill in the buckets that had nothing in them.

    `$group` only emits buckets that exist, so six busy hours spread across two
    days come back as six rows. Charted as-is they render side by side and read
    as six continuous periods of activity, which is not what happened. Zero-fill
    makes the x-axis mean something.
    """
    found = {row["bucket"]: row["count"] for row in rows}
    step = timedelta(hours=1) if by_hour else timedelta(days=1)
    fmt = "%Y-%m-%dT%H:00" if by_hour else "%Y-%m-%d"

    cursor = since.replace(minute=0, second=0, microsecond=0)
    if not by_hour:
        cursor = cursor.replace(hour=0)

    now = datetime.now(UTC)
    out: list[dict[str, Any]] = []
    while cursor <= now:
        key = cursor.strftime(fmt)
        out.append({"bucket": key, "count": found.get(key, 0)})
        cursor += step
    return out


@router.get("/trends", dependencies=[Depends(require("analytics.view"))])
async def trends(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    executions: ExecutionsRepo,
    events: ClickEventsRepo,
    days: int = Query(7, ge=1, le=90),
    bucket: str = Query("hour", pattern="^(hour|day)$"),
) -> dict[str, Any]:
    """DMs sent and link clicks over time.

    Clicks come from `click_events`, one row per click. The counter on
    `link_clicks` cannot answer this: it knows how many, never when.
    """
    account_id = await _account_id(accounts, workspace)
    if account_id is None:
        return {"connected": False, "dms": [], "clicks": []}

    since = datetime.now(UTC) - timedelta(days=days)
    by_hour = bucket == "hour"
    return {
        "connected": True,
        "days": days,
        "bucket": bucket,
        "dms": _densify(
            await executions.series(account_id, since, SENT_ACTIONS, by_hour=by_hour),
            since,
            by_hour,
        ),
        "clicks": _densify(await events.series(account_id, since, by_hour=by_hour), since, by_hour),
    }


@router.get("/places", dependencies=[Depends(require("analytics.view"))])
async def account_places(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    events: ClickEventsRepo,
    links: LinkClicksRepo,
    days: int = Query(7, ge=1, le=90),
    by: str = Query("country", pattern="^(country|city)$"),
) -> dict[str, Any]:
    """Where this account's link clicks came from, across every automation.

    `located` vs `total_clicks` is the whole diagnostic: equal means every click
    was placed, `located == 0` with clicks present means locations are not being
    derived, and both zero means nobody has clicked. The UI needs all three to
    say anything true about an empty chart, so the shape is identical whether or
    not an account is connected.
    """
    empty = {"by": by, "rows": [], "located": 0, "total_clicks": 0, "countries": 0, "links": 0}
    account_id = await _account_id(accounts, workspace)
    if account_id is None:
        return empty

    since = datetime.now(UTC) - timedelta(days=days)
    rows = await events.by_place(account_id, since, by_city=(by == "city"))
    total = await events.count({"instagram_account_id": account_id, "clicked_at": {"$gte": since}})
    return {
        "by": by,
        "rows": rows,
        "located": sum(row["clicks"] for row in rows),
        "total_clicks": total,
        "countries": len({row["country_code"] for row in rows if row["country_code"]}),
        # Whether any trackable link exists at all. Without one there is nothing
        # to click, which is a different problem from "nobody clicked" and needs
        # a different sentence.
        "links": await links.count({"instagram_account_id": account_id}),
    }


@router.get("/highlights", dependencies=[Depends(require("analytics.view"))])
async def highlights(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    executions: ExecutionsRepo,
    events: ClickEventsRepo,
    links: LinkClicksRepo,
    rules: RulesRepo,
    days: int = Query(7, ge=1, le=90),
) -> dict[str, Any]:
    """The standout result from each section, or null where there is not one.

    Every field is nullable on purpose. "No clicks yet" is a real state, and a
    highlight card inventing a winner from an empty set would be worse than an
    honest blank.
    """
    account_id = await _account_id(accounts, workspace)
    if account_id is None:
        return {"connected": False}

    since = datetime.now(UTC) - timedelta(days=days)
    clicks_by_rule = await links.clicks_by_rule(account_id)
    delivered_by_rule = await links.delivered_by_rule(account_id)

    best = None
    for rule in await rules.list_for_account(account_id):
        sent = await executions.count(
            {"rule_id": rule["_id"], "status": "success", "action": {"$in": list(SENT_ACTIONS)}}
        )
        clicks = clicks_by_rule.get(str(rule["_id"]), 0)
        delivered = delivered_by_rule.get(str(rule["_id"]), 0)
        # Ranked on links delivered, like every other rate here, so a two-step
        # automation is not scored against twice the denominator a one-step one
        # gets for the same result. A rule that sends no links has no rate to
        # compare and is skipped rather than entered as a 0%.
        if not delivered:
            continue
        rate = round(clicks / delivered * 100, 1)
        if best is None or rate > best["click_through_rate"]:
            best = {
                "id": str(rule["_id"]),
                "name": rule.get("name") or "Comment → DM",
                "dms_sent": sent,
                "link_clicks": clicks,
                "click_through_rate": rate,
            }

    dm_days = await executions.series(account_id, since, SENT_ACTIONS, by_hour=False)
    click_days = await events.series(account_id, since, by_hour=False)
    places = await events.by_place(account_id, since)

    def peak(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        return max(rows, key=lambda row: row["count"]) if rows else None

    return {
        "connected": True,
        "days": days,
        "best_automation": best,
        "top_dm_day": peak(dm_days),
        "top_click_day": peak(click_days),
        "top_country": places[0] if places else None,
    }


@router.get("/rules/{rule_id}/places", dependencies=[Depends(require("analytics.view"))])
async def rule_places(
    rule_id: str,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    rules: RulesRepo,
    links: LinkClicksRepo,
    by: str = Query("country", pattern="^(country|city)$"),
) -> dict[str, Any]:
    """Where a rule's link clicks came from.

    Locations are derived at click time from a local GeoLite2 database; no IP is
    stored. Rows predating the database, or from addresses it cannot place, carry
    no location and are simply absent -- see `located` versus `total_clicks`.
    """
    account_id = await _account_id(accounts, workspace)
    if account_id is None:
        return {"rows": [], "located": 0, "total_clicks": 0}

    rule = await rules.get(rule_id)
    if rule is None or rule.get("instagram_account_id") != account_id:
        raise NotFoundError("Automation not found.")

    rows = await links.clicks_by_place(rule["_id"], by_city=(by == "city"))
    located = sum(row["clicks"] for row in rows)
    total = (await links.clicks_by_rule(account_id)).get(rule_id, 0)
    return {
        "by": by,
        "rows": rows,
        "located": located,
        "total_clicks": total,
        "countries": len({row["country_code"] for row in rows if row["country_code"]}),
    }


@router.get("/account", dependencies=[Depends(require("analytics.view"))])
async def account_performance(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    snapshots: SnapshotsRepo,
    days: int = Query(7, ge=1, le=90),
) -> dict[str, Any]:
    """Follower totals and growth, from snapshots this app records itself.

    §59 governs what is reportable here. Meta returns `followers_count` as a
    single total and no history (docs/meta-api.md §2b), so:

    * **net growth** is derivable -- it is the difference between two snapshots;
    * **new followers and unfollowers are not.** A day that gained 5 and lost 3
      is indistinguishable from one that gained 2 and lost none. Reporting a
      gross number would be inventing it, so this endpoint does not.
    """
    found = await accounts.live_for_user(workspace.user_id)
    if not found:
        return {"connected": False}
    account = found[0]

    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = await snapshots.history(account["_id"], since)
    # (day, followers) pairs keep the arithmetic on plain ints; the wire shape is
    # built from them afterwards.
    points: list[tuple[str, int]] = [
        (str(r["day"]), int(r["followers_count"]))
        for r in rows
        if r.get("followers_count") is not None
    ]
    series = [{"day": day, "followers_count": count} for day, count in points]

    net_growth: int | None = None
    best_day: tuple[str, int] | None = None
    worst_day: tuple[str, int] | None = None
    if len(points) >= 2:
        net_growth = points[-1][1] - points[0][1]
        deltas = [(points[i][0], points[i][1] - points[i - 1][1]) for i in range(1, len(points))]
        best_day = max(deltas, key=lambda d: d[1])
        worst_day = min(deltas, key=lambda d: d[1])

    return {
        "connected": True,
        "username": account.get("username"),
        "account_type": account.get("account_type"),
        "followers_count": account.get("followers_count"),
        "follows_count": account.get("follows_count"),
        "media_count": account.get("media_count"),
        "days": days,
        "collecting_since": await snapshots.earliest_day(account["_id"]),
        "snapshot_count": len(series),
        # None, not 0 -- one snapshot cannot express a change, and a zero here
        # would read as "flat" rather than "not known yet".
        "net_growth": net_growth,
        "best_day": {"day": best_day[0], "change": best_day[1]} if best_day else None,
        "worst_day": {"day": worst_day[0], "change": worst_day[1]} if worst_day else None,
        "series": series,
        "gross_growth_available": False,
        "gross_growth_note": (
            "Meta reports a follower total only, with no history. Net change between "
            "days is exact; new followers and unfollowers cannot be separated from it."
        ),
        "webhook_subscribed": account.get("webhook_subscribed", False),
        "subscribed_fields": account.get("subscribed_fields") or [],
    }


@router.get("/posts", dependencies=[Depends(require("analytics.view"))])
async def post_performance(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    media: MediaRepo,
    comments: CommentsRepo,
    executions: ExecutionsRepo,
) -> list[dict[str, Any]]:
    """Per-post comments and how many of them actually triggered a rule.

    "Triggered" is measured from executions rather than by re-running the keyword
    matcher here: what fired is a fact on record, whereas re-matching would only
    report what *would* fire under today's rules, which is a different question
    and would silently change history when a rule is edited.
    """
    account_id = await _account_id(accounts, workspace)
    if account_id is None:
        return []

    triggered_ids = await executions.triggered_comment_ids(account_id)

    out = []
    for post in await media.list({"instagram_account_id": account_id}, sort_field="published_at"):
        media_id = post.get("instagram_media_id")
        total = await comments.count({"instagram_media_id": media_id})
        triggered = (
            await comments.count({"instagram_media_id": media_id, "_id": {"$in": triggered_ids}})
            if triggered_ids
            else 0
        )
        out.append(
            {
                **serialise_required(post),
                "comments": total,
                "keyword_comments": triggered,
                "trigger_rate": round(triggered / total * 100, 1) if total else 0.0,
            }
        )
    return out


# The Mongo client is tz_aware, so a missing date has to be an aware sentinel:
# sorting an aware datetime against a naive `datetime.min` raises TypeError, and
# only for the one account that happens to hold an unsynced post.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


async def _invite_states(
    rows: list[dict[str, Any]], users: Any, invites: Any
) -> dict[str, str]:
    """Which of these addresses already has an account or a live invitation.

    Computed here and returned inline rather than left to the Contacts page to
    fetch, because `GET /api/invites` and `GET /api/users` both need permissions
    a `contacts.view`-only role does not hold -- so a second request would 403
    for exactly the person the page is built for.
    """
    addresses = sorted({str(row["email"]).lower() for row in rows if row.get("email")})
    if not addresses:
        return {}

    registered = await users.emails_in(addresses)
    invited = await invites.emails_with_live_invites(addresses)
    return {
        address: (
            "accepted" if address in registered else "invited" if address in invited else "none"
        )
        for address in addresses
    }


async def _contact_groups(
    account_id: Any,
    contacts: ContactsRepo,
    media: MediaRepo,
    *,
    invite_states: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Captured leads, gathered under the post that earned each one.

    Assembled in Python over two round trips rather than an aggregation
    pipeline: one uncapped read of the contacts, one batch of the posts they
    name. `$unwind`/`$lookup` would be one query and considerably harder to
    reason about for a collection this size.

    A person appears once per source, so someone captured on two posts is in
    both groups. A contact whose `sources` is missing or empty still appears,
    under "not from a post" -- a lead the backfill could not place must not
    vanish from the page without a word.
    """
    buckets: dict[str | None, list[dict[str, Any]]] = {}
    for contact in await contacts.all_for_account(account_id):
        sources = contact.get("sources") or [{}]
        for source in sources:
            buckets.setdefault(source.get("instagram_media_id"), []).append(
                {
                    "id": str(contact["_id"]),
                    "email": contact.get("email"),
                    "instagram_user_id": contact.get("instagram_user_id"),
                    "captured_at": source.get("captured_at") or contact.get("created_at"),
                    "rule_name": source.get("rule_name") or "",
                    "backfilled": bool(source.get("backfilled")),
                    "invite_state": (invite_states or {}).get(
                        str(contact.get("email") or "").lower(), "none"
                    ),
                }
            )

    posts = await media.by_instagram_media_ids(account_id, [m for m in buckets if m is not None])

    def order(item: tuple[str | None, list[dict[str, Any]]]) -> tuple[int, Any]:
        """Posts newest first, then posts we hold no details for, then the
        no-post bucket. Sorted here rather than in the page so the CSV and the
        screen can never disagree about what belongs where."""
        media_id, _ = item
        if media_id is None:
            return (2, "")
        post = posts.get(media_id)
        if post is None:
            return (1, media_id)
        return (0, -(post.get("published_at") or _EPOCH).timestamp())

    groups = []
    for media_id, rows in sorted(buckets.items(), key=order):
        rows.sort(key=lambda r: r["captured_at"] or _EPOCH, reverse=True)
        post = posts.get(media_id) if media_id else None
        groups.append(
            {
                "instagram_media_id": media_id,
                "post": serialise_required(post) if post else None,
                "count": len(rows),
                "contacts": rows,
            }
        )
    return groups


@router.get("/contacts", dependencies=[Depends(require("contacts.view"))])
async def contacts_list(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    contacts: ContactsRepo,
    media: MediaRepo,
    users: UsersRepo,
    invites: InvitesRepo,
) -> list[dict[str, Any]]:
    """Captured leads, grouped by post. Emails are shown to the operator who
    collected them."""
    account_id = await _account_id(accounts, workspace)
    if account_id is None:
        return []
    rows = await contacts.all_for_account(account_id)
    states = await _invite_states(rows, users, invites)
    return await _contact_groups(account_id, contacts, media, invite_states=states)


@router.get(
    "/contacts.csv",
    response_class=Response,
    dependencies=[Depends(require("contacts.view"))],
)
async def contacts_csv(
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    contacts: ContactsRepo,
    media: MediaRepo,
    post: str | None = Query(
        default=None,
        description="A media id for one post's leads, or 'none' for those with no post.",
    ),
) -> Response:
    """The same leads as a spreadsheet.

    One route rather than two, so there is a single writer and the per-post file
    is literally a filtered aggregate. `none` is the sentinel for the no-post
    group because a media id is always digits -- an empty `?post=` would be
    indistinguishable from asking for everything, and a client that drops empty
    query params would silently export the lot.

    A media id belonging to another workspace matches no group and yields a
    header-only file. Deliberately not a 404, which would confirm that the id
    exists somewhere.

    Built whole rather than streamed: at this size streaming only buys a cursor
    held open past dependency teardown, and an error part-way through a stream is
    a truncated file delivered with a 200 -- silent data loss in an export.
    """
    account_id = await _account_id(accounts, workspace)
    groups = await _contact_groups(account_id, contacts, media) if account_id else []

    if post == exports.NO_POST:
        groups = [g for g in groups if g["instagram_media_id"] is None]
    elif post:
        groups = [g for g in groups if g["instagram_media_id"] == post]

    name = exports.filename(post, datetime.now(UTC).date())
    return Response(
        content=exports.render(exports.rows_from_groups(groups)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
