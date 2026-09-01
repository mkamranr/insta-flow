"""Dashboard metrics. §59: report only what the data supports."""

from datetime import UTC, datetime, timedelta

from bson import ObjectId

ACCOUNT_IG_ID = "17841400000000000"


async def seed(database, owner, *, executions=(), clicks=(), comments=()):
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "user_id": owner["_id"],
            "instagram_user_id": ACCOUNT_IG_ID,
            "username": "myshop",
            "status": "connected",
        }
    )
    now = datetime.now(UTC)
    for status, action in executions:
        await database["executions"].insert_one(
            {
                "instagram_account_id": account_id,
                "comment_id": ObjectId(),
                "rule_id": ObjectId(),
                "action": action,
                "status": status,
                "created_at": now,
            }
        )
    for count in clicks:
        await database["link_clicks"].insert_one(
            {
                "token": f"t{count}{ObjectId()}",
                "instagram_account_id": account_id,
                "rule_id": ObjectId(),
                "target_url": "https://example.com",
                "click_count": count,
                "created_at": now,
            }
        )
    for username in comments:
        await database["comments"].insert_one(
            {
                "instagram_account_id": account_id,
                "instagram_comment_id": str(ObjectId()),
                "username": username,
                "text": "LINK",
                "commented_at": now,
                "created_at": now,
            }
        )
    return account_id


async def test_summary_reports_not_connected_without_an_account(signed_in):
    assert (await signed_in.get("/api/analytics/summary")).json() == {"connected": False}


async def test_summary_counts_sends_and_clicks(signed_in, database, admin):
    await seed(
        database,
        admin,
        executions=[
            ("success", "private_reply"),
            ("success", "private_reply"),
            ("failed", "private_reply"),
        ],
        clicks=[2, 1],
        comments=["a", "b", "c", "d"],
    )
    body = (await signed_in.get("/api/analytics/summary")).json()

    assert body["connected"] is True
    assert body["dms_sent"] == 2
    assert body["failed"] == 1
    assert body["link_clicks"] == 3
    assert body["comments"] == 4


async def test_unmatched_is_comments_minus_executions(signed_in, database, admin):
    await seed(database, admin, executions=[("success", "private_reply")], comments=["a", "b", "c"])
    body = (await signed_in.get("/api/analytics/summary")).json()
    assert body["matched"] == 1
    assert body["unmatched"] == 2


async def test_unmatched_never_goes_negative(signed_in, database, admin):
    """Executions can outlive their comment; the count must not go below zero."""
    await seed(database, admin, executions=[("success", "private_reply")] * 3, comments=["a"])
    assert (await signed_in.get("/api/analytics/summary")).json()["unmatched"] == 0


async def test_click_through_rate_is_clicks_per_dm(signed_in, database, admin):
    await seed(database, admin, executions=[("success", "private_reply")] * 2, clicks=[3])
    assert (await signed_in.get("/api/analytics/summary")).json()["click_through_rate"] == 150.0


async def test_click_through_rate_is_zero_rather_than_dividing_by_zero(signed_in, database, admin):
    await seed(database, admin, clicks=[5])
    assert (await signed_in.get("/api/analytics/summary")).json()["click_through_rate"] == 0.0


async def test_summary_never_reports_delivery_or_read_rates(signed_in, database, admin):
    """Instagram does not tell us either, so we must not invent them (§59)."""
    await seed(database, admin, executions=[("success", "private_reply")])
    body = (await signed_in.get("/api/analytics/summary")).json()
    assert "delivery_rate" not in body
    assert "read_rate" not in body
    assert "conversion_rate" not in body


async def test_the_window_excludes_older_data(signed_in, database, admin):
    account_id = await seed(database, admin, executions=[("success", "private_reply")])
    await database["executions"].insert_one(
        {
            "instagram_account_id": account_id,
            "comment_id": ObjectId(),
            "rule_id": ObjectId(),
            "action": "private_reply",
            "status": "success",
            "created_at": datetime.now(UTC) - timedelta(days=40),
        }
    )
    assert (await signed_in.get("/api/analytics/summary?days=7")).json()["dms_sent"] == 1
    assert (await signed_in.get("/api/analytics/summary?days=90")).json()["dms_sent"] == 2


async def test_activity_joins_the_triggering_comment(signed_in, database, admin):
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "user_id": admin["_id"],
            "instagram_user_id": ACCOUNT_IG_ID,
            "status": "connected",
        }
    )
    comment_id = (
        await database["comments"].insert_one(
            {
                "instagram_account_id": account_id,
                "instagram_comment_id": "c1",
                "username": "john123",
                "text": "LINK please",
                "created_at": datetime.now(UTC),
            }
        )
    ).inserted_id
    await database["executions"].insert_one(
        {
            "instagram_account_id": account_id,
            "comment_id": comment_id,
            "rule_id": ObjectId(),
            "action": "private_reply",
            "status": "dry_run",
            "message_text": "Here you go!",
            "created_at": datetime.now(UTC),
        }
    )

    rows = (await signed_in.get("/api/analytics/activity")).json()
    assert len(rows) == 1
    assert rows[0]["username"] == "john123"
    assert rows[0]["comment_text"] == "LINK please"
    assert rows[0]["status"] == "dry_run"
    assert rows[0]["rule_name"] == "(deleted rule)"


async def test_activity_can_be_filtered_by_status(signed_in, database, admin):
    await seed(
        database,
        admin,
        executions=[("success", "private_reply"), ("failed", "private_reply")],
    )
    rows = (await signed_in.get("/api/analytics/activity?status=failed")).json()
    assert len(rows) == 1 and rows[0]["status"] == "failed"


async def test_top_commenters_ranks_by_count(signed_in, database, admin):
    await seed(database, admin, comments=["amy", "amy", "amy", "bob", "bob", "cat"])
    rows = (await signed_in.get("/api/analytics/top-commenters")).json()
    assert [r["username"] for r in rows] == ["amy", "bob", "cat"]
    assert rows[0]["comments"] == 3


async def test_analytics_requires_a_session(api):
    for path in ("/summary", "/rules", "/activity", "/top-commenters"):
        assert (await api.get(f"/api/analytics{path}")).status_code == 401
