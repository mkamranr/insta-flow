"""Meta-documented constraints, as plain data.

These are facts about Meta's platform, not Meta code. They live here so the rule
and policy layers can enforce them without importing `services.meta`, which would
break the boundary that keeps rule logic testable with no Meta dependency.

Source: docs/meta-api.md, verified 2026-08-31.
"""

from typing import Final

# One private reply per commenter, within this many days of the comment.
PRIVATE_REPLY_WINDOW_DAYS: Final = 7

# Private replies to comments on posts and reels, per IG professional account.
PRIVATE_REPLY_CALLS_PER_HOUR: Final = 750

# Long-lived token lifetime, and the minimum age before it may be refreshed.
TOKEN_LIFETIME_DAYS: Final = 60
TOKEN_MIN_REFRESH_AGE_HOURS: Final = 24

# Meta gives 24 hours to reply to a message a user sent us. Distinct from the
# seven-day private-reply window above: that one starts at a comment, this one
# at the person's own message, and only the latter applies to story replies and
# inbound DMs (docs/meta-api.md).
MESSAGE_REPLY_WINDOW_HOURS: Final = 24
