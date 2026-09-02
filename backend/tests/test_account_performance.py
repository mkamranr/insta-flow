"""Follower snapshots and the Account Performance endpoint.

§59 is the point of most of these: Meta hands over a follower *total* and no
history, so net change is reportable and gross new/unfollowed is not. The tests
pin that boundary, because it is exactly the kind of number a dashboard invents
by accident.
"""

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.config import Settings
from app.db.repositories import AccountSnapshotsRepository
from app.services.meta import MockInstagramClient
from app.services.meta.client import MetaAccount
from app.services.security.encryption import encrypt
from app.workers.tasks import snapshot_account_metrics

ACCOUNT_IG_ID = "17841400000000000"
KEY = "k" * 64


def settings_for() -> Settings:
    return Settings(
        meta_app_secret="s",
        meta_webhook_verify_token="verify",
        session_secret="s" * 64,
        encryption_key=KEY,
        meta_app_id="1",
    )


async def make_account(database, owner, **extra):
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "user_id": owner["_id"],
            "instagram_user_id": ACCOUNT_IG_ID,
            "username": "myshop",
            "status": "connected",
            "access_token_encrypted": encrypt("test-access-token", KEY),
            **extra,
        }
    )
    return account_id


async def add_snapshots(database, account_id, counts):
    """counts: [(days_ago, followers)] -- oldest first is not required."""
    repo = AccountSnapshotsRepository(database)
    for days_ago, followers in counts:
        day = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        await repo.record(account_id, day, followers_count=followers)


# -- endpoint ---------------------------------------------------------------


async def test_reports_not_connected_without_an_account(signed_in):
    assert (await signed_in.get("/api/analytics/account")).json() == {"connected": False}


async def test_reports_the_stored_counters(signed_in, database, admin):
    await make_account(database, admin, followers_count=446, follows_count=51, media_count=51)
    body = (await signed_in.get("/api/analytics/account")).json()

    assert body["connected"] is True
    assert body["followers_count"] == 446
    assert body["follows_count"] == 51
    assert body["media_count"] == 51


async def test_net_growth_is_none_until_two_snapshots_exist(signed_in, database, admin):
    """One snapshot cannot express a change.

    Zero would read as "flat", which is a different and false claim.
    """
    account_id = await make_account(database, admin, followers_count=446)
    await add_snapshots(database, account_id, [(0, 446)])
    body = (await signed_in.get("/api/analytics/account")).json()

    assert body["net_growth"] is None
    assert body["snapshot_count"] == 1
    assert body["collecting_since"] is not None


async def test_net_growth_is_last_minus_first(signed_in, database, admin):
    account_id = await make_account(database, admin, followers_count=452)
    await add_snapshots(database, account_id, [(3, 440), (2, 445), (1, 444), (0, 452)])
    body = (await signed_in.get("/api/analytics/account")).json()

    assert body["net_growth"] == 12
    assert body["snapshot_count"] == 4


async def test_best_and_worst_day_come_from_daily_deltas(signed_in, database, admin):
    account_id = await make_account(database, admin)
    await add_snapshots(database, account_id, [(3, 440), (2, 445), (1, 444), (0, 452)])
    body = (await signed_in.get("/api/analytics/account")).json()

    assert body["best_day"]["change"] == 8
    assert body["worst_day"]["change"] == -1


async def test_gross_growth_is_reported_as_unavailable(signed_in, database, admin):
    """The honesty guarantee.

    A day that gained 5 and lost 3 is indistinguishable from one that gained 2
    and lost none, because Meta only ever gives a total. No endpoint may claim a
    gross figure, and no `new_followers`/`unfollowers` key may appear.
    """
    account_id = await make_account(database, admin)
    await add_snapshots(database, account_id, [(1, 440), (0, 452)])
    body = (await signed_in.get("/api/analytics/account")).json()

    assert body["gross_growth_available"] is False
    assert "new_followers" not in body
    assert "unfollowers" not in body


async def test_window_excludes_snapshots_outside_the_range(signed_in, database, admin):
    account_id = await make_account(database, admin)
    await add_snapshots(database, account_id, [(60, 100), (1, 440), (0, 452)])
    body = (await signed_in.get("/api/analytics/account?days=7")).json()

    assert body["snapshot_count"] == 2
    assert body["net_growth"] == 12


# -- the daily job ----------------------------------------------------------


@pytest.fixture
def ctx(database):
    return {
        "db": database,
        "settings": settings_for(),
        "client": MockInstagramClient(
            account=MetaAccount(
                instagram_user_id=ACCOUNT_IG_ID,
                username="myshop",
                followers_count=446,
                follows_count=51,
                media_count=51,
            )
        ),
    }


async def test_job_records_todays_snapshot(ctx, database, admin):
    account_id = await make_account(database, admin)

    assert await snapshot_account_metrics(ctx) == {"recorded": 1, "failed": 0}

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    row = await database["account_snapshots"].find_one({"instagram_account_id": account_id})
    assert row["day"] == day
    assert row["followers_count"] == 446


async def test_job_is_idempotent_within_a_day(ctx, database, admin):
    """A retry or a restart must converge on one row, not two (§5)."""
    account_id = await make_account(database, admin)

    await snapshot_account_metrics(ctx)
    await snapshot_account_metrics(ctx)

    assert await database["account_snapshots"].count_documents(
        {"instagram_account_id": account_id}
    ) == 1


async def test_job_refreshes_the_counters_on_the_account(ctx, database, admin):
    """So the UI has a live number without waiting for a reconnect."""
    account_id = await make_account(database, admin, followers_count=1)

    await snapshot_account_metrics(ctx)

    account = await database["instagram_accounts"].find_one({"_id": account_id})
    assert account["followers_count"] == 446
    assert account["media_count"] == 51


async def test_job_skips_accounts_that_are_not_connected(ctx, database, admin):
    await make_account(database, admin, status="token_error")
    assert await snapshot_account_metrics(ctx) == {"recorded": 0, "failed": 0}


async def test_job_survives_a_meta_failure_on_one_account(ctx, database, admin):
    await make_account(database, admin)
    ctx["client"].fail("get_account", 500)

    assert await snapshot_account_metrics(ctx) == {"recorded": 0, "failed": 1}
