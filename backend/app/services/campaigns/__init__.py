"""Rule matching, rendering, and policy.

Pure functions over plain data — must not import Meta code.
"""

from app.services.campaigns.engine import (
    CommentContext,
    build_render_context,
    rule_matches,
    rules_wanting_ai,
    select_rule,
    select_rule_by_intent,
)
from app.services.campaigns.matcher import (
    CONTAINS,
    EXACT,
    MATCH_MODES,
    WORD,
    matches,
    normalize,
    normalize_for_search,
)
from app.services.campaigns.policy import (
    ACTION_PRIVATE_REPLY,
    ACTION_PUBLIC_REPLY,
    PolicyContext,
    PolicyDecision,
    evaluate,
    within_reply_window,
)
from app.services.campaigns.renderer import (
    ALLOWED_VARIABLES,
    MAX_MESSAGE_LENGTH,
    extract_variables,
    render,
    validate,
)

__all__ = [
    "ACTION_PRIVATE_REPLY",
    "ACTION_PUBLIC_REPLY",
    "ALLOWED_VARIABLES",
    "CONTAINS",
    "EXACT",
    "MATCH_MODES",
    "MAX_MESSAGE_LENGTH",
    "WORD",
    "CommentContext",
    "PolicyContext",
    "PolicyDecision",
    "build_render_context",
    "evaluate",
    "extract_variables",
    "matches",
    "normalize",
    "normalize_for_search",
    "render",
    "rule_matches",
    "rules_wanting_ai",
    "select_rule",
    "select_rule_by_intent",
    "validate",
    "within_reply_window",
]
