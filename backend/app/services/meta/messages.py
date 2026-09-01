"""Building Instagram message payloads.

Instagram supports only the `generic` template, whose title and subtitle are
capped at 80 characters each, and only `web_url` and `postback` buttons
(docs/meta-api.md §4b). A 1000-character message therefore cannot carry buttons
in a single message, so a long message with buttons is split: the text first,
then a short template that holds the buttons.
"""

from typing import Any

TITLE_LIMIT = 80
SUBTITLE_LIMIT = 80
TEMPLATE_TEXT_LIMIT = TITLE_LIMIT + SUBTITLE_LIMIT
MAX_BUTTONS = 3

BUTTON_TITLE_LIMIT = 20


def split_for_template(text: str) -> tuple[str, str]:
    """Split text across a template's title and subtitle, preferring a word break."""
    text = text.strip()
    if len(text) <= TITLE_LIMIT:
        return text, ""

    head = text[:TITLE_LIMIT]
    # Break on whitespace so a word is not sliced in half.
    cut = head.rfind(" ")
    if cut > TITLE_LIMIT // 2:
        head = text[:cut]
        rest = text[cut + 1 :]
    else:
        rest = text[TITLE_LIMIT:]

    return head.strip(), rest.strip()[:SUBTITLE_LIMIT]


def web_url_button(title: str, url: str) -> dict[str, str]:
    return {"type": "web_url", "title": title[:BUTTON_TITLE_LIMIT], "url": url}


def postback_button(title: str, payload: str) -> dict[str, str]:
    return {"type": "postback", "title": title[:BUTTON_TITLE_LIMIT], "payload": payload}


def text_message(text: str) -> dict[str, Any]:
    return {"text": text}


def template_message(text: str, buttons: list[dict[str, str]]) -> dict[str, Any]:
    title, subtitle = split_for_template(text)
    element: dict[str, Any] = {"title": title or " ", "buttons": buttons[:MAX_BUTTONS]}
    if subtitle:
        element["subtitle"] = subtitle
    return {
        "attachment": {
            "type": "template",
            "payload": {"template_type": "generic", "elements": [element]},
        }
    }


def build_messages(text: str, buttons: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """One or more message payloads for `text` plus optional `buttons`.

    Returns a list because a long message with buttons cannot be one message:
    the template can only hold 160 characters, so the text is sent on its own and
    the buttons follow in a short template.
    """
    text = (text or "").strip()
    buttons = buttons or []

    if not buttons:
        return [text_message(text)] if text else []

    if len(text) <= TEMPLATE_TEXT_LIMIT:
        return [template_message(text, buttons)]

    # Too long for the template: text first, then the buttons.
    return [text_message(text), template_message(" ", buttons)]
