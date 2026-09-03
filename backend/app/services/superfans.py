"""Who keeps turning up.

A superfan is someone who commented **at least twice in a week, in at least two
consecutive weeks**. Both halves matter:

* *twice in a week* filters out the person who replies to everything once;
* *consecutive weeks* is what "week after week" actually means. Two busy weeks
  four months apart is a coincidence, not loyalty, and calling it one would
  flatter the number.

The rule is stated in the UI exactly as implemented, because a leaderboard whose
definition is fuzzy invites people to read whatever they hoped into it.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

MIN_COMMENTS_PER_WEEK = 2
MIN_CONSECUTIVE_WEEKS = 2


@dataclass(frozen=True)
class Superfan:
    instagram_user_id: str
    username: str
    comments: int
    qualifying_weeks: int
    best_streak: int
    last_at: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "instagram_user_id": self.instagram_user_id,
            "username": self.username,
            "comments": self.comments,
            "qualifying_weeks": self.qualifying_weeks,
            "streak_weeks": self.best_streak,
            "last_at": self.last_at.isoformat() if self.last_at else None,
        }


def _monday(week_key: str) -> date | None:
    """ "2026-36" -> the Monday of ISO week 36 of 2026.

    Compared as dates rather than week numbers so a year boundary (week 52 to
    week 1) is still seven days apart rather than a jump of minus fifty-one.
    """
    try:
        year, week = week_key.split("-")
        return date.fromisocalendar(int(year), int(week), 1)
    except (ValueError, AttributeError):
        return None


def find(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Superfans, most consistent first.

    `rows` is one record per person per ISO week, as produced by
    `CommentsRepository.weekly_counts`.
    """
    people: dict[str, dict[str, Any]] = {}
    for row in rows:
        person = people.setdefault(
            row["instagram_user_id"],
            {"username": "", "comments": 0, "weeks": [], "last_at": None},
        )
        person["comments"] += row["count"]
        if row.get("username"):
            person["username"] = row["username"]
        if row["count"] >= MIN_COMMENTS_PER_WEEK:
            monday = _monday(row["week"])
            if monday is not None:
                person["weeks"].append(monday)
        last = row.get("last_at")
        if last is not None and (person["last_at"] is None or last > person["last_at"]):
            person["last_at"] = last

    found: list[Superfan] = []
    for user_id, person in people.items():
        weeks = sorted(set(person["weeks"]))
        if len(weeks) < MIN_CONSECUTIVE_WEEKS:
            continue

        best = run = 1
        for earlier, later in zip(weeks, weeks[1:], strict=False):
            run = run + 1 if (later - earlier).days == 7 else 1
            best = max(best, run)

        if best >= MIN_CONSECUTIVE_WEEKS:
            found.append(
                Superfan(
                    instagram_user_id=user_id,
                    username=person["username"],
                    comments=person["comments"],
                    qualifying_weeks=len(weeks),
                    best_streak=best,
                    last_at=person["last_at"],
                )
            )

    found.sort(key=lambda fan: (-fan.best_streak, -fan.comments))
    return [fan.as_dict() for fan in found]
