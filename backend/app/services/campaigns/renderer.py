"""Template rendering (§29).

Plain `{{variable}}` substitution against a fixed allowlist. Deliberately not
Jinja and not `eval`: templates are edited through the dashboard, and a template
language would turn that form into remote code execution.
"""

import re
from typing import Final

from app.errors import TemplateError

ALLOWED_VARIABLES: Final = frozenset(
    {"username", "comment", "link", "campaign_name", "post_caption"}
)

MAX_MESSAGE_LENGTH: Final = 1000

# Matches {{ name }} with optional surrounding whitespace. Names are restricted
# to identifiers, so nothing expression-like can appear here at all.
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def extract_variables(body: str) -> set[str]:
    return set(_PLACEHOLDER.findall(body or ""))


def validate(body: str) -> set[str]:
    """Check a template at save time. Returns the variables it uses.

    Rejecting unknown names here means a bad template can never reach the worker,
    where the failure would be invisible until a real comment arrived.
    """
    if not body or not body.strip():
        raise TemplateError("Template body cannot be empty.")

    unknown = extract_variables(body) - ALLOWED_VARIABLES
    if unknown:
        allowed = ", ".join(f"{{{{{name}}}}}" for name in sorted(ALLOWED_VARIABLES))
        raise TemplateError(
            f"Unknown template variable(s): {', '.join(sorted(unknown))}. Allowed: {allowed}"
        )

    # A stray single brace is almost always a typo for a placeholder.
    stripped = _PLACEHOLDER.sub("", body)
    if "{{" in stripped or "}}" in stripped:
        raise TemplateError(
            "Unclosed or malformed {{placeholder}}. Check for a missing brace."
        )

    return extract_variables(body)


def render(body: str, context: dict[str, str | None]) -> str:
    """Substitute allowed variables. Unknown names raise; known-but-absent render empty.

    The substituted values are never re-scanned, so a comment containing
    `{{link}}` cannot inject another variable.
    """
    validate(body)

    def substitute(match: re.Match[str]) -> str:
        return str(context.get(match.group(1)) or "")

    rendered = _PLACEHOLDER.sub(substitute, body).strip()

    if not rendered:
        raise TemplateError("Rendered message is empty.")
    if len(rendered) > MAX_MESSAGE_LENGTH:
        raise TemplateError(
            f"Rendered message is {len(rendered)} characters; the limit is "
            f"{MAX_MESSAGE_LENGTH}."
        )
    return rendered
