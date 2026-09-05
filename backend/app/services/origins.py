"""The public origin, validated in one place.

`PUBLIC_BASE_URL` and the Settings-page override are the same value with two
front doors, and only one of them used to be checked. A scheme-less origin --
`home-srv-1.example.ts.net` rather than `https://home-srv-1.example.ts.net` --
was accepted from the environment and then silently turned link tracking off,
because `workers/tasks.py` decides trackability with `startswith("https://")`.
Nothing failed; the click-through metric simply read zero forever, which is
indistinguishable from nobody clicking.

So the rule lives here and both doors use it: the Settings page (as a 422) and
`Settings` itself (as a startup failure). A malformed origin should be loud.
"""

from urllib.parse import urlsplit

# Not "must be https". `http://localhost:8080` is the legitimate local default,
# and rejecting it would break every developer. What was actually missing is a
# scheme at all, and that is what this insists on.
NO_SCHEME = "The origin must start with http:// or https://."
NO_HOST = "The origin needs a hostname, for example https://host.ts.net."
HAS_PATH = "The origin must be the host only, with no path after it."


def normalise(raw: str) -> str:
    """Validate a bare origin and return it without a trailing slash.

    A path here would be doubled onto every derived URL -- `/u/<slug>/` is added
    by this app -- and the result is a webhook URL Meta will happily accept and
    this server will never answer on.

    Raises `ValueError` rather than the app's `ValidationError` so it stays
    importable from `config.py`, which must not depend on the API layer.
    """
    value = raw.strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise ValueError(NO_SCHEME)
    if not parts.netloc:
        raise ValueError(NO_HOST)
    if parts.path or parts.query or parts.fragment:
        raise ValueError(HAS_PATH)
    return f"{parts.scheme}://{parts.netloc}"


def is_trackable(origin: str) -> bool:
    """Whether links sent from this origin can be click-tracked.

    Meta rejects a non-HTTPS `web_url` button, so an http origin must send the
    original URL unchanged: a broken tracking link loses the whole message,
    which is worse than losing the metric.
    """
    return origin.lower().startswith("https://")
