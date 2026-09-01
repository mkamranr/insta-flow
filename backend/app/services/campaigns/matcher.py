"""Keyword matching (§27).

Pure functions over plain strings. No I/O, no Meta, no database — that is what
makes the matching rules cheap to test exhaustively.
"""

import re
import unicodedata
from typing import Final

EXACT: Final = "exact"
CONTAINS: Final = "contains"
WORD: Final = "word"

MATCH_MODES: Final = (EXACT, CONTAINS, WORD)

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Canonical form used for both storage and comparison.

    NFKC folds compatibility variants (full-width characters, ligatures) onto
    their plain equivalents, so a comment typed on a different keyboard still
    matches. Whitespace is collapsed so line breaks do not defeat `word`.
    """
    if not text:
        return ""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def normalize_for_search(text: str) -> str:
    """Normalized *and* case-folded, for the stored `normalized_text` field (§13).

    Distinct from `normalize()` on purpose: matching folds per-comparison so a
    case-sensitive trigger still works, whereas the stored field is for display
    and search and should be uniformly lowercase.
    """
    return normalize(text).casefold()


def _fold(text: str, case_sensitive: bool) -> str:
    # casefold() beats lower() for non-ASCII: it maps ß to ss, Σ/ς to σ.
    return text if case_sensitive else text.casefold()


def matches(
    trigger: str,
    text: str,
    *,
    match_mode: str = CONTAINS,
    case_sensitive: bool = False,
) -> bool:
    """Does `text` satisfy `trigger` under `match_mode`?

    Both sides are normalized first, so callers may pass raw comment text.
    """
    if match_mode not in MATCH_MODES:
        raise ValueError(f"unknown match mode: {match_mode!r}")

    needle = _fold(normalize(trigger), case_sensitive)
    haystack = _fold(normalize(text), case_sensitive)

    if not needle:
        return False

    if match_mode == EXACT:
        return haystack == needle
    if match_mode == CONTAINS:
        return needle in haystack

    # WORD: whole-word only, so "tool" must not match "toolbox".
    if not re.search(r"\w", needle):
        # A needle with no word characters (emoji, punctuation) has no word
        # boundary to anchor to; \b would never match. Fall back to substring.
        return needle in haystack
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def first_matching_trigger(
    triggers: list[dict], text: str, *, default_case_sensitive: bool = False
) -> dict | None:
    """Return the highest-priority enabled trigger that matches, or None.

    Triggers are evaluated in priority order (descending), then by insertion
    order, so selection is deterministic for a given set.
    """
    candidates = [t for t in triggers if t.get("enabled", True)]
    candidates.sort(key=lambda t: -int(t.get("priority", 0) or 0))

    for trigger in candidates:
        if matches(
            str(trigger.get("value", "")),
            text,
            match_mode=str(trigger.get("match_mode", CONTAINS)),
            case_sensitive=bool(trigger.get("case_sensitive", default_case_sensitive)),
        ):
            return trigger
    return None
