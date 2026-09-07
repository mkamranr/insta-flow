"""The guided Meta-app setup: content, and where progress is kept.

The step list is a single git-versioned module, so swapping or reordering steps
is a data-only edit and shows up as a reviewable diff. Progress is per user and
lives on the user document rather than in its own collection -- it is a handful
of booleans, and `current_user` has already loaded that document on every
request, so reading it costs nothing.
"""

from app.services.onboarding.steps import (
    REQUIRED_STEP_IDS,
    STEP_IDS,
    STEPS,
    content,
)

__all__ = ["REQUIRED_STEP_IDS", "STEPS", "STEP_IDS", "content"]
