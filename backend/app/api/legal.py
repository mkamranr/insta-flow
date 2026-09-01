"""Public legal pages.

Meta requires a reachable Privacy Policy URL before an app can be published, and
a Data Deletion URL alongside the callback. These are served by the app itself so
there is nothing else to host and the URLs cannot drift from the deployment.

Unauthenticated by necessity — Meta's reviewers must be able to read them.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.deps import SettingsDep

router = APIRouter(tags=["legal"])

_STYLE = """
  :root { color-scheme: light dark; }
  body { max-width: 46rem; margin: 3rem auto; padding: 0 1.25rem;
         font: 16px/1.65 -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
  h1 { font-size: 1.6rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
  code { background: rgba(128,128,128,.15); padding: .1em .35em; border-radius: 3px; }
  footer { margin-top: 3rem; font-size: .85rem; opacity: .7; }
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><style>{_STYLE}</style></head><body>{body}</body></html>"
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_policy(settings: SettingsDep) -> HTMLResponse:
    """Privacy policy. Describes what this deployment actually does — it is a
    self-hosted, single-operator tool, not a service with users."""
    return _page(
        "Privacy Policy — InstaFlow",
        f"""
<h1>Privacy Policy</h1>
<p>InstaFlow is self-hosted software. This instance runs on hardware controlled by its
operator. There is no company behind it, no shared service, and no third party receives data
from it.</p>

<h2>What is stored</h2>
<p>When someone comments on a post belonging to the connected Instagram Professional account,
Instagram sends this instance a notification. It stores the comment text, the commenter's
Instagram username and Instagram-scoped ID, the post it was left on, and a record of any
automated reply that was sent.</p>
<p>If an automation asks for an email address, and the person chooses to reply with one, that
address is stored too. Providing it is voluntary; not replying simply means no further message
is sent.</p>

<h2>What is not stored</h2>
<p>No passwords, payment details, contact lists, or private messages beyond those exchanged
directly with the connected account. Instagram access tokens are encrypted at rest and are
never shown in the interface or written to logs.</p>

<h2>Where it goes</h2>
<p>Nowhere. Data stays in this instance's own database. It is not sold, shared, transmitted to
analytics services, or used to train anything. The only external service contacted is Meta's
official Instagram API, in order to send the replies the operator configured.</p>

<h2>How long it is kept</h2>
<p>Until the operator deletes it. Disconnecting the Instagram account, or removing the
instance, removes the associated data.</p>

<h2>Deleting your data</h2>
<p>If you commented on a post handled by this instance and want the record removed, contact the
operator of the Instagram account you interacted with, and it will be deleted. See
<a href="/data-deletion">Data Deletion</a>.</p>

<h2>Cookies</h2>
<p>One session cookie, set only for the operator signing in to the dashboard. Visitors and
commenters are never given a cookie. There is no tracking or advertising cookie of any kind.</p>

<h2>Links</h2>
<p>Links sent in automated replies pass through this instance so the operator can count clicks.
The click count and time are recorded; the destination is a URL the operator chose.</p>

<footer>Served by this InstaFlow instance at <code>{settings.public_base_url}</code>.</footer>
""",
    )


@router.get("/data-deletion", response_class=HTMLResponse)
async def data_deletion_instructions(settings: SettingsDep) -> HTMLResponse:
    """Human-readable deletion instructions. Meta's programmatic callback lives
    at /api/auth/instagram/data-deletion; this page is for people."""
    return _page(
        "Data Deletion — InstaFlow",
        f"""
<h1>Data Deletion</h1>
<p>InstaFlow is self-hosted software operated by one person for their own Instagram account.
There is no central service holding your data.</p>

<h2>If you commented on a post</h2>
<p>Contact the operator of the Instagram account you interacted with and ask them to delete
your data. They control the instance and can remove your comment record, any reply sent to you,
and any email address you provided.</p>

<h2>If you are the operator</h2>
<p>Disconnecting the Instagram account from the dashboard removes the stored account and its
token. To remove everything, including comments, replies and contacts:</p>
<p><code>docker compose down -v</code></p>
<p>That deletes the database volume and is not reversible.</p>

<h2>Automated requests</h2>
<p>Meta's data deletion callback is accepted at
<code>{settings.public_base_url.rstrip('/')}/api/auth/instagram/data-deletion</code>.</p>

<footer>Served by this InstaFlow instance.</footer>
""",
    )
