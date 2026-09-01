"""Message payload construction against Instagram's template limits."""

from app.services.meta.messages import (
    SUBTITLE_LIMIT,
    TEMPLATE_TEXT_LIMIT,
    TITLE_LIMIT,
    build_messages,
    postback_button,
    split_for_template,
    web_url_button,
)

OPEN = {"type": "web_url", "title": "Open", "url": "https://example.com"}


def test_plain_text_needs_no_template():
    assert build_messages("Here's the link!") == [{"text": "Here's the link!"}]


def test_empty_text_produces_nothing():
    assert build_messages("") == []


def test_short_text_with_buttons_is_one_template():
    messages = build_messages("Here's the link!", [OPEN])
    assert len(messages) == 1
    payload = messages[0]["attachment"]["payload"]
    assert payload["template_type"] == "generic"
    assert payload["elements"][0]["title"] == "Here's the link!"
    assert payload["elements"][0]["buttons"] == [OPEN]


def test_medium_text_fills_title_then_subtitle():
    text = "A" * 60 + " " + "B" * 60
    element = build_messages(text, [OPEN])[0]["attachment"]["payload"]["elements"][0]
    assert len(element["title"]) <= TITLE_LIMIT
    assert len(element["subtitle"]) <= SUBTITLE_LIMIT


def test_long_text_with_buttons_is_split_into_two_messages():
    """The template holds 160 characters; a longer message must not be truncated."""
    text = "x" * (TEMPLATE_TEXT_LIMIT + 50)
    messages = build_messages(text, [OPEN])
    assert len(messages) == 2
    assert messages[0] == {"text": text}
    assert messages[1]["attachment"]["payload"]["elements"][0]["buttons"] == [OPEN]


def test_long_text_without_buttons_is_never_split():
    text = "x" * 900
    assert build_messages(text) == [{"text": text}]


def test_split_prefers_a_word_boundary():
    title, subtitle = split_for_template("hello " + "word " * 30)
    assert not title.endswith("wor")
    assert title.split()[-1] == "word"


def test_split_falls_back_to_a_hard_cut_for_one_long_token():
    title, _ = split_for_template("x" * 200)
    assert len(title) == TITLE_LIMIT


def test_button_titles_are_truncated_to_metas_limit():
    button = web_url_button("A really quite long button title", "https://example.com")
    assert len(button["title"]) == 20


def test_postback_button_carries_its_payload():
    button = postback_button("Send me the link", "RULE:abc123")
    assert button == {"type": "postback", "title": "Send me the link", "payload": "RULE:abc123"}


def test_at_most_three_buttons_are_sent():
    many = [OPEN, OPEN, OPEN, OPEN, OPEN]
    element = build_messages("hi", many)[0]["attachment"]["payload"]["elements"][0]
    assert len(element["buttons"]) == 3


def test_a_template_always_has_a_non_empty_title():
    """Meta rejects an element with an empty title."""
    element = build_messages(" ", [OPEN])[0]["attachment"]["payload"]["elements"][0]
    assert element["title"]
