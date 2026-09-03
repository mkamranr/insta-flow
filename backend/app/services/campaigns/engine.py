"""Rule selection.

Pure: takes a comment and the rules already loaded for that post, returns which
rule should act. No I/O, no Meta imports.
"""

from dataclasses import dataclass
from typing import Any

from app.services.campaigns.matcher import CONTAINS, matches, normalize


@dataclass(frozen=True)
class CommentContext:
    """Everything rule selection is allowed to see about a comment."""

    text: str
    username: str = ""
    instagram_comment_id: str = ""
    instagram_user_id: str = ""
    media_id: str | None = None
    instagram_media_id: str | None = None
    post_caption: str | None = None

    @property
    def normalized_text(self) -> str:
        return normalize(self.text)


def rule_matches(rule: dict[str, Any], text: str) -> bool:
    """Does this comment trigger the rule?

    `any_word` fires on any non-empty comment. Otherwise any one of the rule's
    keywords matching is enough — they are alternatives, not all-of.
    """
    if rule.get("any_word"):
        return bool(normalize(text))

    match_mode = str(rule.get("match_mode", CONTAINS))
    case_sensitive = bool(rule.get("case_sensitive", False))
    return any(
        matches(str(keyword), text, match_mode=match_mode, case_sensitive=case_sensitive)
        for keyword in (rule.get("keywords") or [])
    )


def select_rule(comment: CommentContext, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the one rule that should act on this comment, or None.

    `rules` arrives already filtered to this post and sorted by priority, so the
    first match wins. At most one DM is sent per comment, so the rest are ignored
    even if they would also match.
    """
    for rule in rules:
        if rule.get("enabled", True) and rule_matches(rule, comment.text):
            return rule
    return None


def rules_wanting_ai(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rules that opted into intent matching. Empty means do not call the model
    at all — classification costs time and should not run speculatively."""
    return [r for r in rules if (r.get("ai") or {}).get("enabled") and r.get("enabled", True)]


def select_rule_by_intent(
    intent: str, confidence: float, rules: list[dict[str, Any]], default_min_confidence: float
) -> dict[str, Any] | None:
    """Pick a rule matching a classified intent, or None.

    Confidence is checked per rule, so a cautious rule can demand more certainty
    than a permissive one. `rules` arrives priority-sorted, so the first match
    wins exactly as in the keyword path.
    """
    if not intent or intent == "unknown":
        return None

    for rule in rules_wanting_ai(rules):
        ai = rule.get("ai") or {}
        if intent not in (ai.get("intents") or []):
            continue
        threshold = ai.get("min_confidence")
        if threshold is None:
            threshold = default_min_confidence
        if confidence >= float(threshold):
            return rule
    return None


def build_render_context(comment: CommentContext, rule: dict[str, Any]) -> dict[str, str | None]:
    """The only values a message may interpolate (§29)."""
    return {
        "username": comment.username,
        "comment": comment.text,
        # First button url, so {{link}} still works in message text.
        "link": (rule.get("buttons") or [{}])[0].get("url", "") if rule.get("buttons") else "",
        "campaign_name": rule.get("name") or "",
        "post_caption": comment.post_caption or "",
    }
