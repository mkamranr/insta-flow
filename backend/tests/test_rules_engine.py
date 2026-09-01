"""Phase 5. Covers the ten cases §78 requires, plus the edge cases that make
matching safe on real comment text.

Each required case is labelled REQUIRED-n so the mapping to the spec is checkable.
"""

import pytest

from app.errors import TemplateError
from app.services.campaigns import (
    ALLOWED_VARIABLES,
    CONTAINS,
    EXACT,
    WORD,
    CommentContext,
    build_render_context,
    extract_variables,
    matches,
    normalize,
    render,
    rule_matches,
    select_rule,
    validate,
)


def rule(rid="r1", *, keywords=("tools",), mode=CONTAINS, case_sensitive=False,
         enabled=True, priority=100, any_word=False, name="", message="hi", buttons=None):
    return {
        "_id": rid,
        "name": name,
        "keywords": list(keywords),
        "any_word": any_word,
        "match_mode": mode,
        "case_sensitive": case_sensitive,
        "enabled": enabled,
        "priority": priority,
        "message": message,
        "buttons": buttons or [],
    }


# --- normalization ----------------------------------------------------------


def test_normalize_collapses_whitespace_and_newlines():
    assert normalize("send   me\n\nthe  tools") == "send me the tools"


def test_normalize_strips_surrounding_space():
    assert normalize("  tools  ") == "tools"


def test_normalize_folds_fullwidth_characters():
    """A comment typed on a CJK keyboard must still match an ASCII trigger."""
    assert normalize("ｔｏｏｌｓ") == "tools"


def test_normalize_handles_empty():
    assert normalize("") == ""


# --- REQUIRED-1: exact ------------------------------------------------------


def test_exact_matches_the_whole_comment():
    assert matches("tools", "tools", match_mode=EXACT) is True


def test_exact_rejects_a_longer_comment():
    assert matches("tools", "send tools", match_mode=EXACT) is False


def test_exact_ignores_surrounding_whitespace():
    assert matches("tools", "  tools  ", match_mode=EXACT) is True


# --- REQUIRED-2: contains ---------------------------------------------------


def test_contains_finds_the_trigger_anywhere():
    assert matches("tools", "send me the tools please", match_mode=CONTAINS) is True


def test_contains_matches_inside_a_larger_word():
    """This is the documented difference from `word`."""
    assert matches("tool", "toolbox", match_mode=CONTAINS) is True


def test_contains_rejects_an_absent_trigger():
    assert matches("tools", "great post", match_mode=CONTAINS) is False


# --- REQUIRED-3: word -------------------------------------------------------


def test_word_matches_a_standalone_word():
    assert matches("tool", "send me the tool", match_mode=WORD) is True


def test_word_rejects_a_substring_of_a_longer_word():
    """The headline case from §27: `tool` must not fire on `toolbox`."""
    assert matches("tool", "toolbox", match_mode=WORD) is False


def test_word_matches_next_to_punctuation():
    assert matches("tool", "can I get the tool?", match_mode=WORD) is True


def test_word_matches_at_the_start_and_end():
    assert matches("tool", "tool please", match_mode=WORD) is True
    assert matches("tool", "please tool", match_mode=WORD) is True


def test_word_handles_a_multiword_trigger():
    assert matches("ai tools", "the best ai tools around", match_mode=WORD) is True


def test_word_falls_back_to_contains_for_an_emoji_trigger():
    """An emoji has no word boundary, so \\b anchoring would never match."""
    assert matches("🔥", "this is 🔥 amazing", match_mode=WORD) is True


# --- REQUIRED-4: case sensitivity -------------------------------------------


def test_matching_is_case_insensitive_by_default():
    assert matches("TOOLS", "send me the tools") is True
    assert matches("tools", "send me the TOOLS") is True


def test_case_sensitive_trigger_respects_case():
    assert matches("Tools", "send me the tools", case_sensitive=True) is False
    assert matches("Tools", "send me the Tools", case_sensitive=True) is True


def test_case_folding_handles_non_ascii():
    """casefold() maps ß to ss; lower() would not."""
    assert matches("STRASSE", "die straße", case_sensitive=False) is True


# --- REQUIRED-9: no match ---------------------------------------------------


def test_no_match_returns_false():
    assert matches("tools", "love this post") is False


def test_empty_trigger_never_matches():
    """An empty trigger matching everything would DM every commenter."""
    assert matches("", "anything at all") is False
    assert matches("   ", "anything at all") is False


def test_unknown_match_mode_raises():
    with pytest.raises(ValueError):
        matches("tools", "tools", match_mode="fuzzy")


# --- rule matching ----------------------------------------------------------


def test_any_one_keyword_matching_is_enough():
    """Keywords are alternatives, not all-of."""
    r = rule(keywords=("link", "LINK", "Link"))
    assert rule_matches(r, "send me the link") is True
    assert rule_matches(r, "nothing relevant") is False


def test_case_insensitive_matching_makes_case_variants_redundant():
    """link / LINK / Link collapse to one rule unless case_sensitive is on."""
    r = rule(keywords=("link",))
    for text in ("LINK", "Link", "link", "LiNk"):
        assert rule_matches(r, f"send me the {text}") is True


def test_case_sensitive_keywords_are_distinguished():
    r = rule(keywords=("LINK",), case_sensitive=True)
    assert rule_matches(r, "send LINK") is True
    assert rule_matches(r, "send link") is False


def test_any_word_fires_on_any_non_empty_comment():
    r = rule(any_word=True, keywords=())
    assert rule_matches(r, "literally anything") is True
    assert rule_matches(r, "   ") is False


def test_a_rule_with_no_keywords_and_no_any_word_never_matches():
    """Otherwise it would silently DM nobody, or worse, everybody."""
    assert rule_matches(rule(keywords=()), "send me the link") is False


def test_blank_keywords_do_not_match_everything():
    assert rule_matches(rule(keywords=("", "   ")), "anything") is False


# --- rule selection ---------------------------------------------------------


def test_the_first_matching_rule_wins():
    comment = CommentContext(text="send me the LINK")
    rules = [rule("a", keywords=("link",)), rule("b", keywords=("link",))]
    assert select_rule(comment, rules)["_id"] == "a"


def test_a_disabled_rule_is_skipped():
    comment = CommentContext(text="link")
    rules = [rule("a", keywords=("link",), enabled=False), rule("b", keywords=("link",))]
    assert select_rule(comment, rules)["_id"] == "b"


def test_a_non_matching_rule_is_passed_over():
    comment = CommentContext(text="what is the price?")
    rules = [rule("a", keywords=("link",)), rule("b", keywords=("price",))]
    assert select_rule(comment, rules)["_id"] == "b"


def test_no_match_returns_none():
    assert select_rule(CommentContext(text="lovely post"), [rule()]) is None


def test_no_rules_returns_none():
    assert select_rule(CommentContext(text="link"), []) is None


def test_only_one_rule_is_ever_selected():
    """At most one DM per comment."""
    comment = CommentContext(text="link")
    rules = [rule("a", keywords=("link",)), rule("b", keywords=("link",))]
    assert select_rule(comment, rules)["_id"] == "a"


def test_selection_is_stable_for_the_same_comment():
    comment = CommentContext(text="link")
    rules = [rule("a", keywords=("link",)), rule("b", keywords=("link",))]
    assert select_rule(comment, rules) == select_rule(comment, rules)


# --- REQUIRED-7: template rendering -----------------------------------------


def test_renders_the_spec_example():
    body = "Hey {{username}}! 👋\n\nHere's the AI tools list:\n{{link}}"
    out = render(body, {"username": "john123", "link": "https://example.com/tools"})
    assert out == "Hey john123! 👋\n\nHere's the AI tools list:\nhttps://example.com/tools"


def test_whitespace_inside_braces_is_tolerated():
    assert render("Hi {{ username }}", {"username": "amy"}) == "Hi amy"


def test_a_variable_may_repeat():
    assert render("{{username}} {{username}}", {"username": "amy"}) == "amy amy"


def test_every_allowed_variable_renders():
    body = " ".join(f"{{{{{name}}}}}" for name in sorted(ALLOWED_VARIABLES))
    out = render(body, dict.fromkeys(ALLOWED_VARIABLES, "x"))
    assert out == " ".join(["x"] * len(ALLOWED_VARIABLES))


def test_known_variable_with_no_value_renders_empty():
    assert render("Hi {{username}}{{post_caption}}", {"username": "amy"}) == "Hi amy"


def test_substituted_values_are_not_rescanned():
    """A comment containing {{link}} must not be able to inject another variable."""
    out = render("You said: {{comment}}", {"comment": "{{link}}", "link": "http://evil"})
    assert out == "You said: {{link}}"


def test_extract_variables_finds_them_all():
    assert extract_variables("{{username}} and {{link}}") == {"username", "link"}


# --- REQUIRED-8: invalid variable -------------------------------------------


def test_unknown_variable_is_rejected():
    with pytest.raises(TemplateError) as excinfo:
        validate("Hi {{first_name}}")
    assert "first_name" in str(excinfo.value)


def test_error_message_lists_the_allowed_variables():
    with pytest.raises(TemplateError) as excinfo:
        validate("{{nope}}")
    assert "{{username}}" in str(excinfo.value)


def test_render_also_rejects_an_unknown_variable():
    """Validation must not be skippable by going straight to render()."""
    with pytest.raises(TemplateError):
        render("Hi {{admin_token}}", {"admin_token": "secret"})


def test_empty_template_is_rejected():
    with pytest.raises(TemplateError):
        validate("   ")


def test_malformed_placeholder_is_rejected():
    with pytest.raises(TemplateError):
        validate("Hi {{username}")


def test_message_over_the_length_limit_is_rejected():
    with pytest.raises(TemplateError) as excinfo:
        render("{{comment}}", {"comment": "x" * 1001})
    assert "limit" in str(excinfo.value)


def test_template_rendering_to_nothing_is_rejected():
    """An all-empty render would send a blank DM."""
    with pytest.raises(TemplateError):
        render("{{username}}", {"username": ""})


# --- render context ---------------------------------------------------------


def test_build_render_context_only_exposes_allowed_variables():
    comment = CommentContext(text="LINK", username="john123", post_caption="caption")
    context = build_render_context(comment, rule(name="Link rule"))
    assert set(context) == ALLOWED_VARIABLES
    assert context["username"] == "john123"


def test_link_variable_comes_from_the_first_button():
    comment = CommentContext(text="LINK", username="john123")
    r = rule(buttons=[{"title": "Open", "url": "https://example.com/x"}])
    assert build_render_context(comment, r)["link"] == "https://example.com/x"


def test_link_is_empty_when_there_are_no_buttons():
    assert build_render_context(CommentContext(text="x"), rule())["link"] == ""


def test_end_to_end_match_then_render():
    """The worked example: comment LINK, get the DM."""
    comment = CommentContext(text="LINK please!", username="john123")
    r = rule(
        keywords=("link",),
        message="Leave a star on the repo if you find it helpful. Cheers 😊",
        buttons=[{"title": "Open", "url": "https://github.com/mkamranr/reelforge"}],
    )
    selected = select_rule(comment, [r])
    assert selected is not None
    assert render(selected["message"], build_render_context(comment, selected)).startswith(
        "Leave a star"
    )
