"""The guided setup: content integrity, progress, and derived state."""

import pytest

from app.services.meta.credentials import urls_for
from app.services.onboarding import STEPS
from app.services.onboarding.steps import REQUIRED_STEP_IDS, STEP_IDS

from .conftest import TEST_PASSWORD

# Written out, because progress is stored under these strings. Renaming one
# orphans whatever progress a real user had already made, silently -- they would
# simply find a step un-ticked and tick it again, and nobody would ever know the
# old key was still sitting in their document. If this list has to change,
# change it deliberately.
EXPECTED_STEP_IDS = (
    "instagram-professional",
    "meta-developer-account",
    "create-app",
    "instagram-api-setup",
    # Ahead of `link-account` deliberately: Meta's own page says to assign the
    # Instagram Tester role before generating a token, in one line of grey text
    # that is very easy to read past.
    "tester-access",
    "link-account",
    "app-credentials",
    "app-basic-settings",
    "live-mode",
    "redirect-uri",
    "webhooks",
    # Meta's own dashboard switches, which we cannot read back -- so this step
    # exists purely to make somebody look at them before they connect.
    "webhook-switches",
    "connect",
)


def test_the_step_ids_are_stable():
    assert STEP_IDS == EXPECTED_STEP_IDS


def test_the_step_ids_are_unique():
    assert len(set(STEP_IDS)) == len(STEP_IDS)


def test_optional_steps_are_excluded_from_the_denominator():
    """An optional step must not make 100% unreachable.

    There are none at present -- the `clean-app-fallback` step was dropped once
    `Other → Business` became the documented route, because that route cannot
    bind an app to the wrong Instagram setup and so cannot produce the failure
    the fallback existed to recover from. The assertion is kept general so it
    still means something if an optional step returns.
    """
    optional = {step.id for step in STEPS if step.optional}
    assert not optional & set(REQUIRED_STEP_IDS)
    assert len(REQUIRED_STEP_IDS) == len(STEP_IDS) - len(optional)


def test_every_copy_block_names_a_real_url():
    """The whole reason values are keyed rather than inlined. A typo here would
    otherwise ship as a step telling somebody to paste an empty string."""
    known = set(urls_for("https://example.test")) | {"verify_token"}
    for step in STEPS:
        for block in step.body:
            if block.kind == "copy":
                assert block.value in known, f"{step.id}: {block.value}"


def test_every_embed_and_check_block_is_one_the_frontend_knows():
    embeds = {"meta_app_credentials", "verify_token", "connect_instagram"}
    checks = {"meta_app_saved", "instagram_connected", "webhooks_subscribed"}
    for step in STEPS:
        for block in step.body:
            if block.kind == "embed":
                assert block.embed in embeds, f"{step.id}: {block.embed}"
            if block.kind == "check":
                assert block.check in checks, f"{step.id}: {block.check}"


def test_the_walkthrough_does_not_ask_a_tenant_to_use_a_shell():
    """`docs/meta-api.md` is written for whoever runs the server and reaches for
    `.env`, `make reload` and `make funnel`. An invited person has none of
    those, so this content is a rewrite rather than a port -- and this test is
    what stops the operator-facing instructions creeping back in."""
    text = " ".join(
        block.text for step in STEPS for block in step.body
    ) + " ".join(step.summary for step in STEPS)
    for shell_ism in ("make reload", "make funnel", "docker compose", ".env", "curl "):
        assert shell_ism not in text, shell_ism


def test_the_deauthorize_url_is_in_the_walkthrough():
    """It is one of the three fields Meta's login page wants, and it was missing
    from `urls_for` entirely until this feature -- so nobody who configured
    their app from the Settings page ever set it."""
    values = {
        block.value for step in STEPS for block in step.body if block.kind == "copy"
    }
    assert "deauthorize_url" in values


# --- the API ----------------------------------------------------------------


async def test_the_steps_endpoint_returns_workspace_urls(signed_in, admin):
    response = await signed_in.get("/api/onboarding/steps")

    assert response.status_code == 200
    body = response.json()
    assert [s["id"] for s in body["steps"]] == list(EXPECTED_STEP_IDS)
    # Prefixed with this workspace's slug, because that is what its own Meta app
    # has to be configured against.
    assert f"/u/{admin['slug']}/" in body["urls"]["webhook_url"]
    assert "deauthorize_url" in body["urls"]


async def test_progress_starts_empty_and_persists(signed_in):
    start = await signed_in.get("/api/onboarding/progress")
    assert start.json()["done"] == []
    assert start.json()["connected"] is False
    assert start.json()["total"] == len(REQUIRED_STEP_IDS)

    ticked = await signed_in.put("/api/onboarding/progress/create-app", json={"done": True})
    assert ticked.status_code == 200
    assert ticked.json()["done"] == ["create-app"]

    # Survives a fresh read, which is what "leave and come back" means.
    assert (await signed_in.get("/api/onboarding/progress")).json()["done"] == ["create-app"]


async def test_a_step_can_be_unticked(signed_in):
    await signed_in.put("/api/onboarding/progress/create-app", json={"done": True})
    response = await signed_in.put(
        "/api/onboarding/progress/create-app", json={"done": False}
    )
    assert response.json()["done"] == []


async def test_an_unknown_step_id_is_refused(signed_in):
    """A stale bookmark must not write a key nothing will ever read again."""
    response = await signed_in.put(
        "/api/onboarding/progress/no-such-step", json={"done": True}
    )
    assert response.status_code == 404


async def test_dismissing_hides_the_banner_without_completing(signed_in):
    response = await signed_in.post("/api/onboarding/dismiss")
    assert response.status_code == 200
    assert response.json()["dismissed"] is True
    # Still not connected: dismissing is not finishing.
    assert response.json()["connected"] is False


async def test_ticking_every_step_does_not_dismiss_the_banner(signed_in):
    """A banner that vanished while nothing actually worked would be the worst
    of both outcomes."""
    for step_id in REQUIRED_STEP_IDS:
        await signed_in.put(f"/api/onboarding/progress/{step_id}", json={"done": True})

    progress = await signed_in.get("/api/onboarding/progress")
    assert len(progress.json()["done"]) == len(REQUIRED_STEP_IDS)
    assert progress.json()["dismissed"] is False
    assert progress.json()["connected"] is False


async def test_progress_is_per_user(signed_in, other_client, role_factory, user_factory):
    role = await role_factory("Tenant", ["automations.view", "instagram.manage"])
    await user_factory("tenant@example.com", role)
    await other_client.post(
        "/api/auth/login", json={"email": "tenant@example.com", "password": TEST_PASSWORD}
    )

    await signed_in.put("/api/onboarding/progress/create-app", json={"done": True})

    mine = await other_client.get("/api/onboarding/progress")
    assert mine.json()["done"] == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/onboarding/steps",
        "/api/onboarding/progress",
    ],
)
async def test_a_narrow_role_can_still_read_its_own_setup(
    api, role_factory, user_factory, path
):
    """The reason these are ungated. `GET /api/analytics/summary` needs
    `analytics.view` and `/api/instagram/accounts` needs `instagram.view`, so
    driving the banner from either would make a narrow role read as "never
    connected" forever -- and a freshly invited person is exactly that role."""
    role = await role_factory("Barely anything", ["automations.view"])
    await user_factory("narrow@example.com", role)
    await api.post(
        "/api/auth/login", json={"email": "narrow@example.com", "password": TEST_PASSWORD}
    )

    assert (await api.get(path)).status_code == 200


async def test_the_setup_is_unreachable_without_a_session(api):
    """Ungated is not unauthenticated."""
    assert (await api.get("/api/onboarding/progress")).status_code == 401


def test_no_step_refers_to_another_by_number():
    """Cross-references are by title, not by number, and that is deliberate.

    Numbers shift whenever a step is inserted, moved or dropped -- which has
    already happened twice while this content was being corrected against a
    real setup, and silently pointed people at the wrong step both times.
    Titles survive reordering.
    """
    import re

    prose = " ".join(
        block.text
        for step in STEPS
        for block in step.body
        if block.kind in {"text", "note", "warn"}
    )
    offenders = re.findall(r"\bstep \d+\b", prose, flags=re.I)
    assert offenders == [], offenders


def test_the_tester_role_comes_before_the_account_is_linked():
    """Meta refuses to generate a token for an account with no Tester role, and
    says so in one line of grey text that is easy to miss. The wizard order has
    to reflect that, not the order the dashboard happens to present."""
    order = list(STEP_IDS)
    assert order.index("tester-access") < order.index("link-account")
