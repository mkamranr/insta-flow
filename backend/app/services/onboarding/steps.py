"""The guided Meta-app setup, as data.

Rewritten from `docs/meta-api.md` § "Meta App Setup", **not ported from it**.
That document is addressed to whoever runs the server: its steps reach for
`.env`, `make reload`, `curl /ready` and `make funnel`. An invited person has
none of those -- the tunnel is already up and the public address is already set
by the operator -- so those steps become either a paste into the Settings page
or a single reassuring sentence.

Everything a workspace must copy into Meta's dashboard is referenced by **key**,
never inlined as a literal. The URLs are per workspace and come from
`services/meta/credentials.urls_for`, so an inlined copy would drift the moment
the public origin changed -- and inlining is exactly what produced the missing
`deauthorize_url`, which nobody noticed for the life of the Settings page.

`Step.id` is a stable identifier and progress is stored under it. Renaming one
orphans somebody's progress, which is why `test_onboarding.py` pins the list.
"""

from dataclasses import dataclass, field
from typing import Any, Final, Literal

BlockKind = Literal[
    "text", "note", "warn", "code", "list", "table", "links", "copy", "embed", "check"
]


@dataclass(frozen=True)
class Block:
    kind: BlockKind
    text: str = ""
    items: tuple[str, ...] = ()
    ordered: bool = False
    head: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    links: tuple[dict[str, str], ...] = ()
    # For `copy`: a key into the workspace URL set, or "verify_token".
    value: str = ""
    label: str = ""
    hint: str = ""
    # For `embed`: which interactive fragment the frontend should render.
    embed: str = ""
    # For `check`: which piece of server state confirms this step.
    check: str = ""

    def serialise(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in ("", (), False)}


@dataclass(frozen=True)
class Step:
    id: str
    number: str
    title: str
    summary: str
    body: tuple[Block, ...] = field(default_factory=tuple)
    # Excluded from the progress denominator; reached only from another step.
    optional: bool = False

    def serialise(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "title": self.title,
            "summary": self.summary,
            "optional": self.optional,
            "body": [block.serialise() for block in self.body],
        }


def _t(text: str) -> Block:
    return Block("text", text=text)


def _note(text: str) -> Block:
    return Block("note", text=text)


def _warn(text: str) -> Block:
    return Block("warn", text=text)


INTRO: Final[tuple[Block, ...]] = (
    _t(
        "Social Flow replies to comments on **your own** Instagram account. To do "
        "that it needs its own Meta app, in your name -- not shared with anyone "
        "else. This walks you through creating one. It is free, and takes about "
        "twenty minutes the first time."
    ),
    _note(
        "**Two different accounts are involved, and that is expected.** Your Meta "
        "*developer* account is a Facebook account. Your *Instagram* professional "
        "account is separate and probably a different identity. Nothing needs to "
        "be linked -- Social Flow uses Instagram Login, which authenticates "
        "against Instagram directly and needs no Facebook Page."
    ),
    _t(
        "Tick each step as you finish it. You can leave and come back; your "
        "progress is saved. The last two steps tick themselves once Social Flow "
        "can confirm them."
    ),
)

STEPS: Final[tuple[Step, ...]] = (
    Step(
        id="instagram-professional",
        number="1",
        title="Switch your Instagram account to professional",
        summary="A personal account cannot receive comment notifications.",
        body=(
            _t(
                "In the Instagram mobile app: **Settings → Account type and tools → "
                "Switch to professional account**, then pick Business or Creator. "
                "Either works, and it is free."
            ),
            _note(
                "This is required, not a preference: Meta does not send comment "
                "notifications for personal accounts at all."
            ),
        ),
    ),
    Step(
        id="meta-developer-account",
        number="2",
        title="Create a Meta developer account",
        summary="A one-off registration on Meta's developer site.",
        body=(
            _t("Open Meta for Developers, choose **Get Started**, and accept the terms."),
            Block(
                "links",
                links=(
                    {"label": "developers.facebook.com", "href": "https://developers.facebook.com"},
                ),
            ),
        ),
    ),
    Step(
        id="create-app",
        number="3",
        title="Create the app",
        summary="Choose Other, not one of the offered use cases.",
        body=(
            _t("Go to **My Apps → Create App**, then work through four screens:"),
            Block(
                "list",
                ordered=True,
                items=(
                    "**Name it** anything you like -- `Social Flow` is fine.",
                    "When asked what you want your app to do, select **Others**.",
                    "Under **Looking for something else?**, select **Other**.",
                    "For the app type, select **Business**.",
                ),
            ),
            _note(
                "The **Other** route puts the app in what Meta calls the **old "
                "experience**, where you pick from all available permissions, "
                "features and products yourself instead of being handed a preset. "
                "That is what you want here. Verified 2026-09-07 on a real setup -- "
                "Meta reorganises this flow often, so trust the screens over any "
                "guide, including this one."
            ),
            _note(
                "A Business portfolio is **optional**; you can skip it. If the "
                "sidebar shows **Required actions** asking for business "
                "verification, ignore it -- that is for Advanced Access, which "
                "automating your own account does not need."
            ),
        ),
    ),
    Step(
        id="instagram-api-setup",
        number="4",
        title="Add the Instagram product",
        summary="Instagram is a product you add, not a preset you were given.",
        body=(
            _t(
                "On your new app's dashboard, scroll to **Available products** and "
                "click **Set up** next to **Instagram** -- the row described as "
                '"Allow creators and businesses to manage messages and comments, '
                'publish content, track insights, hashtags and mentions".'
            ),
            _warn(
                "**Do not set up `Facebook Login for Business`**, which sits in the "
                "same list a few rows down. It is a separate flow with its own "
                "redirect-URI list, and anything you save there is invisible to "
                "Social Flow. It is the single most expensive wrong turn in this "
                "whole setup, because its settings page looks correct and its "
                "redirect-URI validator will even tell you our URL is valid. You "
                "want **Instagram**, and nothing else in that list."
            ),
            _t(
                "Within the Instagram setup, the flavour you want is **Instagram "
                "business login** -- it authenticates against Instagram directly "
                "and needs no linked Facebook Page. The three permissions to "
                "request are `instagram_business_basic`, "
                "`instagram_business_manage_comments` and "
                "`instagram_business_manage_messages`."
            ),
            _t(
                "That lands you on **Welcome to the Instagram API**, which is the "
                "page every remaining step refers to. Three things are on it: the "
                "app name, ID and secret (**Copy the App ID and secret**), a "
                "**Generate access tokens** section, and further down the login and "
                "webhook settings (**Register the login URLs** and **Configure "
                "notifications**)."
            ),
            _warn(
                '**Ignore the link offering to "switch to the API setup with '
                'Facebook login"** at the top of that page. It is worded as an '
                "upgrade -- it mentions hashtags and insights -- but it is the "
                "other flow, the one whose redirect URIs Social Flow never reads, "
                "and Meta allows only one setup per app. Taking it means starting "
                "over with a new app."
            ),
            _note(
                "That page also tells you, in passing, to assign the Instagram "
                "Tester role before generating a token. The next step does exactly "
                "that -- it is ordered this way on purpose."
            ),
            _note(
                "Verified 2026-09-07: this is what the dashboard shows for an app "
                "created through **Other → Business**, as in **Create the app**."
            ),
            _t(
                "**If your app is in Meta's newer use-case experience instead** -- "
                "you see **Use cases** in the sidebar rather than a products list -- "
                "that route still works. Follow this instead:"
            ),
            Block(
                "code",
                text=(
                    'Do you see "API setup with Instagram business login"?\n'
                    "│\n"
                    "├─ YES ──▶ already customized. Skip to the credentials step.\n"
                    "│\n"
                    '├─ NO, but you see "Use cases"\n'
                    "│    │\n"
                    "│    └─▶ click Use cases\n"
                    "│         │\n"
                    '│         ├─ "Manage messaging and content on Instagram" is listed\n'
                    "│         │    └─▶ click Customize\n"
                    '│         │         ├─ offered "API setup with Instagram business login"\n'
                    "│         │         │    └─▶ select it, then\n"
                    '│         │         │        click "Add all required permissions"\n'
                    "│         │         │        the sidebar entry now exists; carry on\n"
                    '│         │         └─ ONLY "API setup with Facebook login" is offered\n'
                    "│         │              └─▶ wrong setup, cannot be converted → step 3b\n"
                    "│         │\n"
                    "│         └─ the Instagram use case is NOT listed\n"
                    '│              └─▶ "Add use case" → add it → Customize as above\n'
                    "│\n"
                    '└─ NO, and there is no "Use cases" either\n'
                    "     └─▶ you are in the old experience -- use the products list above"
                ),
            ),
            _note(
                "In that experience, `API setup with Instagram business login` does "
                "**not** appear in the sidebar until you customize the use case -- "
                "customizing is what creates the menu entry. So if you have not done "
                'it there is nothing to find, and older guides that say "sidebar → '
                'Instagram" do not apply yet.'
            ),
        ),
    ),
    Step(
        id="tester-access",
        number="5",
        title="Give your Instagram account access to the app",
        summary="Two halves, and the second is the one people miss.",
        body=(
            _t(
                "**In Meta's dashboard:** open **App roles → Roles** and add your "
                "Instagram account as an **Instagram Tester**."
            ),
            _t(
                "**Then accept it from Instagram.** This has to be done on "
                "instagram.com in a browser -- the Tester Invites tab does not "
                "reliably appear in the mobile app."
            ),
            Block(
                "links",
                links=(
                    {
                        "label": "instagram.com/accounts/manage_access",
                        "href": "https://www.instagram.com/accounts/manage_access/",
                    },
                ),
            ),
            _t(
                "That page has three tabs -- Active, Expired and **Tester "
                "Invites**. Open Tester Invites and click **Accept**. Be signed in "
                "as the Instagram account being invited, not as your Facebook "
                "developer account."
            ),
            _note(
                "Until it is accepted the role sits at `pending` and behaves "
                "exactly as though it were never added, so connecting fails for an "
                "account that looks correctly configured. If the tab is empty, "
                "wait a minute and reload."
            ),
        ),
    ),
    Step(
        id="link-account",
        number="6",
        title="Link your Instagram account to the app",
        summary="Generates the token the dashboard uses. Needs the Tester role first.",
        body=(
            _t(
                "Back on **Welcome to the Instagram API**, under "
                "**1. Generate access tokens**, click **Add account** and link your "
                "Instagram account."
            ),
            _warn(
                "**This only works once the previous step is finished** -- both "
                "halves of it. The page mentions the requirement in a single line "
                'of grey text (*"before proceeding, make sure to assign the '
                'Instagram Tester role to the account in the Roles tab"*), which is '
                "easy to read straight past. Until the role is assigned **and "
                "accepted**, the account behaves exactly as though it were never "
                "added."
            ),
            _note(
                "This is Meta's own token, for its dashboard. Social Flow gets its "
                "own separately when you connect at the end -- so doing this does "
                "not mean you are finished."
            ),
        ),
    ),
    Step(
        id="app-credentials",
        number="7",
        title="Copy the App ID and secret into Social Flow",
        summary="Two values, from the Instagram API setup page only.",
        body=(
            _t(
                "On the same **Welcome to the Instagram API** page, the panel below "
                "the heading holds **Instagram app ID** and **Instagram app "
                "secret** -- press **Show** to reveal the secret. Paste both here."
            ),
            _warn(
                "**These are the *Instagram* App ID and secret, found on that page "
                "only.** They are *not* the App ID and secret under **App settings "
                "→ Basic** -- those are the Facebook ones, they are different "
                "values, and using them makes the connection fail with an "
                "unhelpful error. After finding this page at all, this is the most "
                "common mistake in the whole setup."
            ),
            Block("embed", embed="meta_app_credentials"),
            Block("check", check="meta_app_saved", label="Social Flow has an App ID and secret"),
        ),
    ),
    Step(
        id="app-basic-settings",
        number="8",
        title="Fill in App settings → Basic",
        summary="Four values Meta requires before it will let the app go live.",
        body=(
            _t(
                "In the Meta dashboard, open **App settings → Basic** and fill in "
                "these fields. Social Flow hosts the two pages for you -- copy the "
                "URLs below."
            ),
            Block(
                "copy",
                value="privacy_url",
                label="Privacy Policy URL",
                hint="Served by Social Flow.",
            ),
            Block(
                "copy",
                value="data_deletion_url",
                label="Data Deletion Instructions URL",
                hint="Also served by Social Flow.",
            ),
            Block(
                "table",
                head=("Field", "What to put"),
                rows=(
                    ("Terms of Service URL", "Any reachable page you control."),
                    ("App Icon", "A 1024x1024 PNG. There is one below you can use."),
                    ("Category", "Anything reasonable, e.g. Business and Pages."),
                ),
            ),
            Block(
                "links",
                links=({"label": "Download a 1024x1024 app icon", "href": "/icon-1024.png"},),
            ),
        ),
    ),
    Step(
        id="live-mode",
        number="9",
        title="Switch the app to Live mode",
        summary="The step that actually unlocks comments. Do not skip it.",
        body=(
            _warn(
                "**In Development mode Meta withholds the text of comments written "
                "by anyone without a role on your app.** Not delays -- withholds. "
                "The notification never arrives and the comment looks invisible, "
                "while every symptom points at a broken connection instead. This "
                "cost two days here before it was understood."
            ),
            _t(
                "Once **App settings → Basic** is complete, flip the toggle at the top of the App "
                "Dashboard from **Development** to **Live**."
            ),
            _note(
                "Live mode needs **no** App Review, business verification, "
                "registered company or paid plan. Those are for Advanced Access, "
                "which you would only need to automate *other people's* accounts."
            ),
        ),
    ),
    Step(
        id="redirect-uri",
        number="10",
        title="Register the login URLs",
        summary="Three fields, on a page that has an identical-looking twin.",
        body=(
            _t(
                "On the **API setup with Instagram business login** page, open "
                "**Set up Instagram business login** and paste these three values."
            ),
            Block("copy", value="redirect_uri", label="Valid OAuth Redirect URI"),
            Block("copy", value="deauthorize_url", label="Deauthorize callback URL"),
            Block("copy", value="data_deletion_url", label="Data deletion request URL"),
            _warn(
                '**Two different pages both have a field called exactly "Valid '
                'OAuth Redirect URIs", and only one of them is read.** Tell them '
                "apart by what else is on the page."
            ),
            Block(
                "table",
                head=("On the page", "Instagram business login (right)", "Facebook Login (wrong)"),
                rows=(
                    ("Deauthorize callback URL", "yes", "no"),
                    ("Data deletion request URL", "yes", "no"),
                    ("Client OAuth login / Strict Mode", "no", "yes"),
                    ("Anything about the JavaScript SDK", "no", "yes"),
                ),
            ),
            _note(
                "If the page mentions the JavaScript SDK or smart TVs you are on "
                "the Facebook Login page, and anything saved there is invisible to "
                "Social Flow. **Its redirect-URI validator will even report our "
                "URL as valid** -- because it is, in the wrong list. Only trust "
                "the validator on the Instagram business login page."
            ),
            _t(
                "The dashboard's separate **Client OAuth Settings** panel belongs "
                "to Facebook Login and is unused here. Nothing in it can cause or "
                "fix a redirect error from Social Flow."
            ),
        ),
    ),
    Step(
        id="webhooks",
        number="11",
        title="Configure notifications",
        summary="Save the callback, then subscribe to comments separately.",
        body=(
            _t("On the same page, open **Configure webhooks** and paste these two values."),
            Block("copy", value="webhook_url", label="Callback URL"),
            Block("embed", embed="verify_token"),
            _t(
                "Then click **Verify and save**. Meta checks the URL immediately, "
                "so this works only while Social Flow is running -- which it is, "
                "since you are reading this."
            ),
            _warn(
                "**Now subscribe to the `comments` field. That is a separate "
                "click.** Below the callback URL is a list of fields, each with "
                "its own Subscribe button. Saving the callback URL subscribes "
                "nothing. Verified in live traffic: with the field left "
                "unsubscribed, message notifications arrive normally and comment "
                "notifications never do."
            ),
            Block("check", check="webhooks_subscribed", label="Comments subscription confirmed"),
            _note(
                "The confirmation above reflects only the subscription **Social "
                "Flow** made for your account. Meta's dashboard has its own "
                "switches, which we cannot read back -- the next step covers them, "
                "and they are the usual reason comments do not arrive while "
                "everything here looks correct."
            ),
        ),
    ),
    Step(
        id="webhook-switches",
        number="12",
        title="Turn on the two webhook switches",
        summary="Neither is on by default, and neither complains when it is off.",
        body=(
            _t(
                "Back on **Welcome to the Instagram API**, two separate switches "
                "have to be on. This is the last thing to check before connecting, "
                "and it is worth being fussy about: with either one off, comments "
                "simply never arrive and **nothing anywhere reports an error**."
            ),
            _t(
                "**Under `1. Generate access tokens`**, the account you linked has "
                "a **Webhook Subscription** column beside its token. Set it to "
                "**On**."
            ),
            _t(
                "**Under `2. Configure webhooks`**, find the **Webhook fields** "
                "table and set every field's **Subscribe** toggle to "
                "**Subscribed**."
            ),
            _note(
                "Social Flow reads three of those fields -- `comments`, `messages` "
                "and `messaging_postbacks` -- and takes note of `standby`. "
                "Subscribing the rest is harmless: anything it does not use is "
                "ignored on arrival. Turning them all on is simply less to get "
                "wrong than picking four from a list of eleven. Leave the "
                "**Version** column at whatever Meta has already selected."
            ),
            _warn(
                "**These two switches are the ones Social Flow cannot check for "
                "you.** Meta gives us no way to read either back, so the "
                "confirmation on the previous step reflects only the subscription "
                "Social Flow made for itself. If comments are not arriving and "
                "everything in Social Flow looks correct, this page is where to "
                "look."
            ),
        ),
    ),
    Step(
        id="connect",
        number="13",
        title="Connect your Instagram account",
        summary="The last step. Everything else was preparation.",
        body=(
            _t(
                "Connect below and consent to the three permissions. If this fails "
                "with an invalid redirect error, **Register the login URLs** is the one "
                "to re-check -- not **Configure notifications**."
            ),
            Block("embed", embed="connect_instagram"),
            Block("check", check="instagram_connected", label="Instagram account connected"),
        ),
    ),
)

TROUBLESHOOTING: Final[tuple[Block, ...]] = (
    Block(
        "table",
        head=("Symptom", "Cause"),
        rows=(
            (
                "No `Instagram` / `API setup` entry in the sidebar",
                "Use case not customized yet -- see Add the Instagram product",
            ),
            (
                "Only *API setup with Facebook login* is offered",
                "App bound to the wrong setup -- step 3b",
            ),
            (
                "`Required actions` demands business verification",
                "Not needed for your own account -- ignore it",
            ),
            (
                "Connecting fails immediately",
                "Facebook App ID used instead of the Instagram one -- see Copy the App\n"
                "ID and secret",
            ),
            (
                "`Invalid redirect_uri` when connecting",
                "Redirect URI not registered -- Register the login URLs, not\n"
                "Configure notifications",
            ),
            (
                "Redirect URI mismatch",
                "A trailing slash, `http` instead of `https`, or a stale address",
            ),
            (
                "Tester invite stuck at `pending`",
                "Not accepted on instagram.com -- see Give your Instagram account access",
            ),
            (
                "`Verify and save` fails",
                "The verify token does not match the one Social Flow generated",
            ),
            (
                "Messages arrive but comments never do",
                "The `comments` field is not subscribed in Meta's dashboard --\n"
                "see Configure notifications",
            ),
            (
                "A real follower comments and nothing happens",
                "The app is still in Development mode -- see Switch the app to Live mode",
            ),
        ),
    ),
)

# Progress is keyed on these, so the frontend never needs to know the order.
STEP_IDS: Final[tuple[str, ...]] = tuple(step.id for step in STEPS)
REQUIRED_STEP_IDS: Final[tuple[str, ...]] = tuple(step.id for step in STEPS if not step.optional)


def content() -> dict[str, Any]:
    return {
        "intro": [block.serialise() for block in INTRO],
        "steps": [step.serialise() for step in STEPS],
        "troubleshooting": [block.serialise() for block in TROUBLESHOOTING],
    }
