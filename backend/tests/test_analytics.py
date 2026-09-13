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


async def test_click_through_rate_is_clicks_per_link_delivered(signed_in, database, admin):
    """Repeat taps still count, so the rate can exceed 100%."""
    await seed(database, admin, executions=[("success", "private_reply")] * 2, clicks=[3])
    assert (await signed_in.get("/api/analytics/summary")).json()["click_through_rate"] == 300.0


async def test_click_through_rate_is_zero_rather_than_dividing_by_zero(signed_in, database, admin):
    """A rule with no link buttons delivers no links, so there is nothing to divide by."""
    await seed(database, admin, executions=[("success", "private_reply")] * 2)
    assert (await signed_in.get("/api/analytics/summary")).json()["click_through_rate"] == 0.0


async def test_the_two_step_flow_does_not_halve_the_click_through_rate(signed_in, database, admin):
    """One comment answered by opener + payoff is two DMs but one link.

    Dividing clicks by DMs sent read 50% for a commenter who was sent exactly
    one link and clicked it. The opener carries a postback button, not a
    `web_url`, so it mints no trackable link and must not sit in the
    denominator.
    """
    await seed(
        database,
        admin,
        executions=[("success", "opening_dm"), ("success", "private_reply")],
        clicks=[1],
    )
    body = (await signed_in.get("/api/analytics/summary")).json()

    assert body["dms_sent"] == 2, "both sends really happened; that count is honest"
    assert body["link_clicks"] == 1
    assert body["click_through_rate"] == 100.0


async def _rule_with(database, account_id, *, name, actions=(), links=(), priority=100):
    """A rule plus the executions it sent and the tracked links it minted.

    `priority` is the sort key every listing reads, so a test that cares where
    a rule lands in the list sets it rather than relying on insertion order.
    """
    rule_id = ObjectId()
    now = datetime.now(UTC)
    await database["rules"].insert_one(
        {
            "_id": rule_id,
            "instagram_account_id": account_id,
            "name": name,
            "priority": priority,
            "enabled": True,
        }
    )
    for action in actions:
        await database["executions"].insert_one(
            {
                "instagram_account_id": account_id,
                "comment_id": ObjectId(),
                "rule_id": rule_id,
                "action": action,
                "status": "success",
                "created_at": now,
            }
        )
    for target_url, click_count in links:
        await database["link_clicks"].insert_one(
            {
                "token": str(ObjectId()),
                "instagram_account_id": account_id,
                "rule_id": rule_id,
                "execution_id": ObjectId(),
                "target_url": target_url,
                "button_title": "Open",
                "click_count": click_count,
                "created_at": now,
            }
        )
    return rule_id


async def test_rule_click_through_rate_counts_links_not_dms(signed_in, database, admin):
    """Two DMs, one link, one click is 100% -- the opener carries no link."""
    account_id = await seed(database, admin)
    await _rule_with(
        database,
        account_id,
        name="Two-step link",
        actions=("opening_dm", "private_reply"),
        links=(("https://example.com", 1),),
    )

    row = (await signed_in.get("/api/analytics/rules")).json()[0]

    assert row["dms_sent"] == 2
    assert row["link_clicks"] == 1
    assert row["click_through_rate"] == 100.0


async def test_a_rule_that_sends_no_links_has_no_rate_to_report(signed_in, database, admin):
    account_id = await seed(database, admin)
    await _rule_with(database, account_id, name="Thank commenters", actions=("private_reply",))

    row = (await signed_in.get("/api/analytics/rules")).json()[0]

    assert row["dms_sent"] == 1
    assert row["click_through_rate"] == 0.0


async def test_each_destination_is_rated_against_its_own_deliveries(signed_in, database, admin):
    """Two buttons go out together, so each was delivered twice, not four times.

    Sharing one rule-wide denominator made every destination look half as
    effective as it was the moment a message carried more than one button.
    """
    account_id = await seed(database, admin)
    await _rule_with(
        database,
        account_id,
        name="Two buttons",
        actions=("private_reply", "private_reply"),
        links=(
            ("https://example.com/a", 2),
            ("https://example.com/a", 0),
            ("https://example.com/b", 1),
            ("https://example.com/b", 1),
        ),
    )

    row = (await signed_in.get("/api/analytics/rules")).json()[0]
    rates = {link["target_url"]: link for link in row["links"]}

    assert rates["https://example.com/a"]["delivered"] == 2
    assert rates["https://example.com/a"]["click_through_rate"] == 100.0
    assert rates["https://example.com/b"]["click_through_rate"] == 100.0
    # Four links delivered, four clicks -- the rule agrees with its destinations.
    assert row["click_through_rate"] == 100.0


async def test_rule_performance_is_not_capped_at_fifty(signed_in, database, admin):
    """`BaseRepository.list` stops at 50, which silently truncated the table.

    It only ever hid rules from the screen -- the worker matches through
    `list_for_media`, which reads an uncapped cursor -- so nothing looked
    broken. The dashboard pages this list now, and a pager that says "50 of
    50" over an account with 60 automations is asserting something false.
    """
    account_id = await seed(database, admin)
    for n in range(60):
        await _rule_with(database, account_id, name=f"Rule {n}", actions=("private_reply",))

    assert len((await signed_in.get("/api/analytics/rules")).json()) == 60


async def test_listing_automations_is_not_capped_at_fifty(signed_in, database, admin):
    """The Automations page reads this one, and pages it the same way."""
    account_id = await seed(database, admin)
    for n in range(60):
        await _rule_with(database, account_id, name=f"Rule {n}")

    assert len((await signed_in.get("/api/rules")).json()) == 60


async def test_activity_names_a_rule_past_the_fiftieth(signed_in, database, admin):
    """The cap reached further than the two lists that paged.

    `activity` builds its rule-name map from the same call, so a row belonging
    to a rule outside the first 50 read as "(deleted rule)" -- a live
    automation, reported as deleted, in the log an operator checks first.

    The needle is pinned last by `priority`, not by being inserted first: both
    listings sort on `priority` descending, and with the field tied across
    every rule the order is Mongo's to choose.
    """
    account_id = await seed(database, admin)
    for n in range(59):
        await _rule_with(database, account_id, name=f"Rule {n}")
    await _rule_with(
        database, account_id, name="Lowest priority", actions=("private_reply",), priority=0
    )

    rows = (await signed_in.get("/api/analytics/activity")).json()

    assert [row["rule_name"] for row in rows] == ["Lowest priority"]


async def test_the_best_automation_is_ranked_on_links_delivered(signed_in, database, admin):
    """A one-step rule and a two-step rule with identical results must tie."""
    account_id = await seed(database, admin)
    await _rule_with(
        database,
        account_id,
        name="One step",
        actions=("private_reply",),
        links=(("https://example.com", 1),),
    )
    await _rule_with(
        database,
        account_id,
        name="Two step",
        actions=("opening_dm", "private_reply"),
        links=(("https://example.com", 1),),
    )

    best = (await signed_in.get("/api/analytics/highlights")).json()["best_automation"]

    assert best["click_through_rate"] == 100.0


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


# -- content performance ----------------------------------------------------


async def _post_with_comments(database, account_id, media_id, *, total, triggered):
    """A post with `total` comments, `triggered` of which fired a rule."""
    now = datetime.now(UTC)
    await database["media"].insert_one(
        {
            "instagram_account_id": account_id,
            "instagram_media_id": media_id,
            "caption": f"post {media_id}",
            "published_at": now,
        }
    )
    for i in range(total):
        comment_id = (
            await database["comments"].insert_one(
                {
                    "instagram_account_id": account_id,
                    "instagram_media_id": media_id,
                    "instagram_comment_id": str(ObjectId()),
                    "username": f"u{i}",
                    "text": "link",
                    "commented_at": now,
                    "created_at": now,
                }
            )
        ).inserted_id
        if i < triggered:
            await database["executions"].insert_one(
                {
                    "instagram_account_id": account_id,
                    "comment_id": comment_id,
                    "rule_id": ObjectId(),
                    "action": "private_reply",
                    "status": "success",
                    "created_at": now,
                }
            )


async def test_post_performance_reports_trigger_rate(signed_in, database, admin):
    account_id = await seed(database, admin)
    await _post_with_comments(database, account_id, "media_a", total=4, triggered=1)

    body = (await signed_in.get("/api/analytics/posts")).json()
    post = next(p for p in body if p["instagram_media_id"] == "media_a")

    assert post["comments"] == 4
    assert post["keyword_comments"] == 1
    assert post["trigger_rate"] == 25.0


async def test_post_with_no_comments_has_a_zero_trigger_rate(signed_in, database, admin):
    """Not a division by zero, and not "100% of nothing"."""
    account_id = await seed(database, admin)
    await _post_with_comments(database, account_id, "media_b", total=0, triggered=0)

    body = (await signed_in.get("/api/analytics/posts")).json()
    post = next(p for p in body if p["instagram_media_id"] == "media_b")

    assert post["comments"] == 0
    assert post["trigger_rate"] == 0.0


async def test_the_two_step_flow_counts_a_comment_once(signed_in, database, admin):
    """opening_dm + private_reply are two executions for one comment.

    Counting executions instead of distinct comments would report a 200% trigger
    rate on a post where every commenter was answered exactly once.
    """
    account_id = await seed(database, admin)
    await _post_with_comments(database, account_id, "media_c", total=2, triggered=2)
    comment = await database["comments"].find_one({"instagram_media_id": "media_c"})
    await database["executions"].insert_one(
        {
            "instagram_account_id": account_id,
            "comment_id": comment["_id"],
            "rule_id": ObjectId(),
            "action": "opening_dm",
            "status": "success",
            "created_at": datetime.now(UTC),
        }
    )

    body = (await signed_in.get("/api/analytics/posts")).json()
    post = next(p for p in body if p["instagram_media_id"] == "media_c")

    assert post["keyword_comments"] == 2
    assert post["trigger_rate"] == 100.0


# -- trend buckets -----------------------------------------------------------


def test_the_trend_series_fills_in_empty_buckets():
    """Six busy hours across two days must not render as six adjacent bars.

    `$group` only emits buckets that have data. Charted raw, scattered activity
    looks like one continuous run — the axis stops meaning anything.
    """
    from datetime import UTC, datetime, timedelta

    from app.api.analytics import _densify

    since = datetime.now(UTC) - timedelta(hours=5)
    sparse = [{"bucket": since.strftime("%Y-%m-%dT%H:00"), "count": 3}]

    filled = _densify(sparse, since, by_hour=True)

    assert len(filled) >= 5, "every hour in the window is present"
    assert filled[0]["count"] == 3
    assert all(row["count"] == 0 for row in filled[1:]), "quiet hours are zeros, not gaps"
    assert [row["bucket"] for row in filled] == sorted(row["bucket"] for row in filled)


def test_densify_handles_a_completely_empty_series():
    from datetime import UTC, datetime, timedelta

    from app.api.analytics import _densify

    since = datetime.now(UTC) - timedelta(days=2)
    filled = _densify([], since, by_hour=False)

    assert len(filled) >= 2
    assert all(row["count"] == 0 for row in filled)


# --- contacts, grouped by the post that earned them --------------------------


MEDIA_A = "17890000000000001"
MEDIA_B = "17890000000000002"


async def seed_contacts(database, owner, *, posts=(), contacts=()):
    """`posts` are (media_id, caption, published_at_offset_days)."""
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
    for media_id, caption, days in posts:
        await database["media"].insert_one(
            {
                "instagram_account_id": account_id,
                "instagram_media_id": media_id,
                "caption": caption,
                "media_type": "IMAGE",
                "media_url": "https://example.com/a.jpg",
                "permalink": "https://instagram.com/p/a/",
                "published_at": now - timedelta(days=days),
            }
        )
    for email, sources in contacts:
        await database["contacts"].insert_one(
            {
                "instagram_account_id": account_id,
                "instagram_user_id": f"u-{email}",
                "email": email,
                "created_at": now,
                "sources": [
                    {
                        "instagram_media_id": media_id,
                        "rule_id": ObjectId(),
                        "rule_name": rule_name,
                        "captured_at": now,
                        "backfilled": False,
                    }
                    for media_id, rule_name in sources
                ],
            }
        )
    return account_id


async def test_contacts_are_grouped_under_the_post_that_earned_them(signed_in, database, admin):
    await seed_contacts(
        database,
        admin,
        posts=[(MEDIA_A, "Guide post", 1)],
        contacts=[("a@x.com", [(MEDIA_A, "Email gate")])],
    )
    (group,) = (await signed_in.get("/api/analytics/contacts")).json()

    assert group["instagram_media_id"] == MEDIA_A
    assert group["post"]["caption"] == "Guide post"
    assert group["count"] == 1
    assert group["contacts"][0]["email"] == "a@x.com"
    assert group["contacts"][0]["rule_name"] == "Email gate"


async def test_one_person_captured_twice_appears_under_both_posts(signed_in, database, admin):
    """The reason a source is a list. Neither post's CSV shrinks because the
    other captured the same person later."""
    await seed_contacts(
        database,
        admin,
        posts=[(MEDIA_A, "First", 2), (MEDIA_B, "Second", 1)],
        contacts=[("a@x.com", [(MEDIA_A, "Gate"), (MEDIA_B, "Gate")])],
    )
    groups = (await signed_in.get("/api/analytics/contacts")).json()

    assert [g["instagram_media_id"] for g in groups] == [MEDIA_B, MEDIA_A]
    assert all(g["contacts"][0]["email"] == "a@x.com" for g in groups)


async def test_a_post_we_hold_no_details_for_keeps_its_id(signed_in, database, admin):
    """`post: null` means two different things and the page must tell them apart:
    an unsynced or deleted post, and no post at all."""
    await seed_contacts(
        database, admin, contacts=[("a@x.com", [("17890000000000999", "Gate")])]
    )
    (group,) = (await signed_in.get("/api/analytics/contacts")).json()

    assert group["post"] is None
    assert group["instagram_media_id"] == "17890000000000999"


async def test_a_contact_with_no_sources_still_appears(signed_in, database, admin):
    """A lead the backfill could not place must not vanish silently."""
    account_id = await seed_contacts(database, admin)
    await database["contacts"].insert_one(
        {
            "instagram_account_id": account_id,
            "instagram_user_id": "u-1",
            "email": "orphan@x.com",
            "created_at": datetime.now(UTC),
            "sources": [],
        }
    )
    (group,) = (await signed_in.get("/api/analytics/contacts")).json()

    assert group["instagram_media_id"] is None
    assert group["contacts"][0]["email"] == "orphan@x.com"


async def test_the_no_post_group_sorts_last(signed_in, database, admin):
    await seed_contacts(
        database,
        admin,
        posts=[(MEDIA_A, "First", 1)],
        contacts=[("a@x.com", [(MEDIA_A, "Gate")]), ("b@x.com", [(None, "DM keyword")])],
    )
    groups = (await signed_in.get("/api/analytics/contacts")).json()

    assert [g["instagram_media_id"] for g in groups] == [MEDIA_A, None]


async def test_the_grouped_list_is_not_capped_at_fifty(signed_in, database, admin):
    """`BaseRepository.list` stops at 50, which silently truncated the page."""
    await seed_contacts(
        database,
        admin,
        posts=[(MEDIA_A, "Guide", 1)],
        contacts=[(f"{n}@x.com", [(MEDIA_A, "Gate")]) for n in range(60)],
    )
    (group,) = (await signed_in.get("/api/analytics/contacts")).json()
    assert group["count"] == 60


# --- the CSV -----------------------------------------------------------------


async def test_the_csv_is_a_spreadsheet_download(signed_in, database, admin):
    await seed_contacts(
        database,
        admin,
        posts=[(MEDIA_A, "Guide post", 1)],
        contacts=[("a@x.com", [(MEDIA_A, "Email gate")])],
    )
    response = await signed_in.get("/api/analytics/contacts.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.csv"')

    body = response.content.decode("utf-8-sig")
    assert body.startswith(
        "email,instagram_user_id,captured_at,post_caption,post_media_id,automation\r\n"
    )
    assert "a@x.com" in body
    assert "Guide post" in body
    assert "Email gate" in body


async def test_the_csv_carries_a_bom_and_crlf(signed_in, database, admin):
    """Excel on Windows ignores the HTTP charset and reads a UTF-8 file as
    cp1252 without a BOM, which mangles every emoji in a caption."""
    await seed_contacts(
        database,
        admin,
        posts=[(MEDIA_A, "Guide ✨", 1)],
        contacts=[("a@x.com", [(MEDIA_A, "Gate")])],
    )
    body = (await signed_in.get("/api/analytics/contacts.csv")).content

    assert body.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in body
    assert "✨" in body.decode("utf-8-sig")


async def test_a_caption_with_separators_stays_one_field(signed_in, database, admin):
    await seed_contacts(
        database,
        admin,
        posts=[(MEDIA_A, 'He said "hi", then\nleft', 1)],
        contacts=[("a@x.com", [(MEDIA_A, "Gate")])],
    )
    body = (await signed_in.get("/api/analytics/contacts.csv")).content.decode("utf-8-sig")

    assert '"He said ""hi"", then left"' in body
    assert len(body.strip().split("\r\n")) == 2


async def test_a_caption_that_looks_like_a_formula_is_defused(signed_in, database, admin):
    """Comment text and captions are hostile input; a cell opening with `=` is
    executed by Excel and Sheets."""
    await seed_contacts(
        database,
        admin,
        posts=[(MEDIA_A, '=HYPERLINK("http://evil","clickme")', 1)],
        contacts=[("a@x.com", [(MEDIA_A, "Gate")])],
    )
    body = (await signed_in.get("/api/analytics/contacts.csv")).content.decode("utf-8-sig")

    assert "'=HYPERLINK" in body


async def test_the_csv_can_be_narrowed_to_one_post(signed_in, database, admin):
    await seed_contacts(
        database,
        admin,
        posts=[(MEDIA_A, "First", 2), (MEDIA_B, "Second", 1)],
        contacts=[("a@x.com", [(MEDIA_A, "Gate")]), ("b@x.com", [(MEDIA_B, "Gate")])],
    )
    body = (
        await signed_in.get(f"/api/analytics/contacts.csv?post={MEDIA_A}")
    ).content.decode("utf-8-sig")

    assert "a@x.com" in body
    assert "b@x.com" not in body
    assert MEDIA_A in (await signed_in.get(f"/api/analytics/contacts.csv?post={MEDIA_A}")).headers[
        "content-disposition"
    ]


async def test_post_none_means_the_no_post_group_not_everything(signed_in, database, admin):
    """An empty `?post=` would be indistinguishable from asking for the lot, and
    a client that drops empty params would export it by accident."""
    await seed_contacts(
        database,
        admin,
        posts=[(MEDIA_A, "First", 1)],
        contacts=[("a@x.com", [(MEDIA_A, "Gate")]), ("b@x.com", [(None, "DM keyword")])],
    )
    body = (await signed_in.get("/api/analytics/contacts.csv?post=none")).content.decode(
        "utf-8-sig"
    )

    assert "b@x.com" in body
    assert "a@x.com" not in body


async def test_an_unknown_post_yields_a_header_only_file(signed_in, database, admin):
    """Not a 404, which would confirm the id exists in some other workspace."""
    await seed_contacts(
        database,
        admin,
        posts=[(MEDIA_A, "First", 1)],
        contacts=[("a@x.com", [(MEDIA_A, "Gate")])],
    )
    response = await signed_in.get("/api/analytics/contacts.csv?post=99999999999999999")

    assert response.status_code == 200
    assert response.content.decode("utf-8-sig").strip().split("\r\n") == [
        "email,instagram_user_id,captured_at,post_caption,post_media_id,automation"
    ]


async def test_the_csv_exports_more_than_the_page_size(signed_in, database, admin):
    """The bug that prompted this: the old CSV was built in the browser from a
    list the API had already truncated to 50."""
    await seed_contacts(
        database,
        admin,
        posts=[(MEDIA_A, "Guide", 1)],
        contacts=[(f"{n}@x.com", [(MEDIA_A, "Gate")]) for n in range(60)],
    )
    body = (await signed_in.get("/api/analytics/contacts.csv")).content.decode("utf-8-sig")

    assert len(body.strip().split("\r\n")) == 61
