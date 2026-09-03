"""Who counts as a superfan.

The definition is the whole feature: "2+ comments in a week, in at least 2
consecutive weeks". A looser rule would quietly promote coincidence to loyalty,
so these tests pin both halves of it.
"""

from datetime import UTC, datetime

from app.services.superfans import find


def row(user: str, week: str, count: int, username: str = "") -> dict:
    return {
        "instagram_user_id": user,
        "week": week,
        "count": count,
        "username": username or f"user{user}",
        "last_at": datetime(2026, 9, 3, tzinfo=UTC),
    }


def test_two_consecutive_qualifying_weeks_is_a_superfan():
    found = find([row("1", "2026-35", 2), row("1", "2026-36", 3)])

    assert len(found) == 1
    assert found[0]["streak_weeks"] == 2
    assert found[0]["comments"] == 5


def test_one_comment_a_week_is_not_enough():
    """Someone who replies to everything once is engaged, not a superfan."""
    assert find([row("1", "2026-35", 1), row("1", "2026-36", 1)]) == []


def test_a_single_busy_week_is_not_enough():
    assert find([row("1", "2026-36", 9)]) == []


def test_non_consecutive_weeks_do_not_count():
    """Two busy weeks months apart is a coincidence, not "week after week"."""
    assert find([row("1", "2026-20", 4), row("1", "2026-36", 4)]) == []


def test_a_streak_across_a_year_boundary_is_recognised():
    """Week 53 to week 1 is seven days apart, not a jump of minus fifty-two.

    Comparing week *numbers* would break here; comparing the Monday of each ISO
    week does not.
    """
    found = find([row("1", "2026-53", 2), row("1", "2027-01", 2)])

    assert len(found) == 1
    assert found[0]["streak_weeks"] == 2


def test_the_longest_streak_is_reported_not_the_week_count():
    found = find(
        [
            row("1", "2026-30", 2),  # an isolated qualifying week
            row("1", "2026-34", 2),
            row("1", "2026-35", 2),
            row("1", "2026-36", 2),  # a run of three
        ]
    )

    assert found[0]["streak_weeks"] == 3
    assert found[0]["qualifying_weeks"] == 4


def test_quiet_weeks_break_a_streak_but_still_count_toward_comments():
    found = find([row("1", "2026-34", 2), row("1", "2026-35", 1), row("1", "2026-36", 2)])

    assert found == [], "a one-comment week interrupts the run"


def test_the_most_consistent_person_is_listed_first():
    found = find(
        [
            row("1", "2026-35", 2),
            row("1", "2026-36", 2),
            row("2", "2026-34", 5),
            row("2", "2026-35", 5),
            row("2", "2026-36", 5),
        ]
    )

    assert [fan["instagram_user_id"] for fan in found] == ["2", "1"]


def test_a_malformed_week_key_is_skipped_rather_than_raising():
    assert find([row("1", "not-a-week", 4), row("1", "2026-36", 4)]) == []
