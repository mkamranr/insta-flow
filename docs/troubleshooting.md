# Troubleshooting

Ordered by how often each thing actually goes wrong.

## Nothing happens when someone comments

Work down the pipeline; each step has a place to look.

**1. Did the webhook arrive?**

```bash
docker compose logs api | grep webhook_received
```

Nothing? The event never reached us. Check the funnel is up (`tailscale funnel
status`) and that `/webhooks/instagram` is the saved Callback URL.

**If some webhooks arrive but never comment ones**, the `comments` field is not
subscribed at the *app* level. There are two independent subscriptions:

```bash
# Account level - what Social Flow set. Should list "comments".
docker compose exec -T api python -c "
import asyncio, httpx
from app.config import get_settings
from app.db import mongodb
from app.services.security.encryption import decrypt
async def m():
    s = get_settings(); db = await mongodb.connect(s.mongo_url, s.mongo_database)
    a = await db.instagram_accounts.find_one({})
    tok = decrypt(a['access_token_encrypted'], s.encryption_key)
    async with httpx.AsyncClient() as c:
        r = await c.get(f'{s.meta_graph_base}/me/subscribed_apps', params={'access_token': tok})
        print(r.text)
    await mongodb.disconnect()
asyncio.run(m())"
```

If that lists `comments` and comment events still never arrive, the **app**
level is missing it: Meta dashboard → Instagram → Configure webhooks → click
**Subscribe** next to `comments`. Saving the callback URL does not subscribe
any field.

**Rule out the subscription before blaming permissions.** An unsubscribed
`comments` field produces exactly the same silence as a permissions problem, and
is far more common. Fix that first, then retest with an Instagram Tester
account — which works regardless of App Review status. Only if a tester's
comment also fails to arrive is it worth investigating further. See
`docs/meta-api.md` §7.

To see what has actually been delivered:

```bash
docker compose exec -T mongodb mongosh --quiet --eval '
const db = db.getSiblingDB("instaflow");
let kinds = {};
db.webhook_events.find({}).forEach(e => (e.payload.entry || []).forEach(en => {
  (en.changes || []).forEach(c => kinds["changes:"+c.field] = (kinds["changes:"+c.field]||0)+1);
  (en.messaging || []).forEach(m => Object.keys(m)
    .filter(k => !["sender","recipient","timestamp"].includes(k))
    .forEach(k => kinds["messaging:"+k] = (kinds["messaging:"+k]||0)+1));
}));
printjson(kinds);'
```

**2. Was it rejected as unsigned?**

```bash
docker compose logs api | grep webhook_signature_invalid
```

Means `META_APP_SECRET` does not match the app Meta is signing with. Usually the
Facebook app secret was used instead of the Instagram one.

**3. Was it a self-comment?**

`webhook_self_comment_skipped`. Your own comments and replies fire the same
webhook; they are skipped deliberately, or Social Flow would answer itself.

**4. Did a rule match?**

`comment_unmatched` with `rules_on_post` tells you how many rules were even
considered. Zero means the rule is on a different post, or is disabled. The
Activity page shows the same thing in words.

**5. Did policy deny it?**

The Activity page shows the reason. The common ones:

| Reason | Meaning |
| --- | --- |
| `already_messaged` | "Only send once per person" and they were already messaged |
| `outside_reply_window` | Comment older than Meta's 7-day private-reply limit |
| `not_following` | Follow gate is on and they do not follow you |
| `rate_limited_*` | Your own per-rule safety limits, not Meta's |
| `rule_disabled` | The automation is switched off |
| `automation_paused` | Auto DM was paused for the account; the comment is held, and the Auto DM pause page offers to send or discard it |

**6. Was it a dry run?**

`DRY_RUN=true` records everything and sends nothing. That is the default. The
banner at the top of the dashboard says so.

## The automation fired but nobody received anything

Twice now this has been reported as a broken pipeline and twice the pipeline was fine. Work
through it in this order; the first two checks cost nothing.

**1. Did it actually send?** Look for the execution and Meta's own receipt:

```
docker compose logs worker --since 1h | grep -E "private_reply_success|public_reply_sent|awaiting_follow"
```

A `meta_message_id` in that line means Meta accepted the message. That ends the question of
whether the software sent it — everything after this is about where Instagram put it.

**2. Look in Message Requests, not the inbox.** Instagram delivers a private reply to the inbox
only for people who already follow the account; everyone else gets a message request they must
accept first, and it is not badged like a normal message. On a follow-gated rule this is
*every* recipient by definition, because the gate exists to reach non-followers
(`docs/meta-api.md` §4). Check on the *commenting* account: Direct → Requests.

Seen from the business account the thread looks completely normal, which is what makes this so
convincing a false alarm.

**3. No public reply under the comment?** That is a separate per-rule setting, off by default.
Check `public_reply.enabled` on the rule. When it is off, nothing is posted publicly and only
the DM is sent — no log line, no execution row, by design.

**4. Got the follow prompt instead of the link?** Working as intended: they had not followed
when they tapped. With an opening DM the execution is parked as `awaiting_follow` and the link
is delivered automatically once they follow — no second comment or tap needed. Watch it:

```
docker compose logs -f worker | grep -E "awaiting_follow|delivered_after_follow"
```

Meta publishes no follow webhook (`docs/meta-api.md` §5.4), so this is polled: 15s, 30s, 1m, 2m,
3m, 5m, 10m, 15m, 30m, then hourly, for up to 23 hours.

## Link clicks always read zero

The click-through rate stays at 0% no matter how many links go out, and
`db.link_clicks.countDocuments({})` is 0 — not merely un-clicked rows, *no rows at all*.

Check the scheme on the public origin:

```bash
grep PUBLIC_BASE_URL .env
curl -s localhost:8090/ready | python3 -m json.tool | grep public_base_url
```

It must include `https://`. A bare hostname —

```
PUBLIC_BASE_URL=home-srv-1.example.ts.net      # wrong
PUBLIC_BASE_URL=https://home-srv-1.example.ts.net   # right
```

— fails the HTTPS test in `tracked_buttons`, so buttons ship the raw target URL and no
`link_clicks` row is ever written. There is nothing to click through; the metric was never
collectable rather than empty.

Current builds refuse to start on a scheme-less value, so this only bites a deployment
configured before that check existed. Fix `.env` and `make reload` — **not**
`docker compose restart`, which keeps the old environment.

**Two things this also breaks, worth checking at the same time.** File attachments are built
from the same value, so a scheme-less origin produced `host.ts.net/files/...` which Meta cannot
fetch — `send_attachment_if_any` only logs a warning, so recipients got the text and no file.
And a rule sent *before* the fix keeps its raw button forever: those clicks are unattributable,
so test with a freshly sent message rather than an old one.

**Clicks recorded but no geography?** Different fault, same-looking symptom — see the
`trusted_proxies` note in the Caddy section, not this one.

## "Invalid redirect_uri" when connecting

The OAuth redirect URI lives on the **Instagram business login settings** page,
not the Facebook Login page — both have a field with that exact name. See
`docs/meta-api.md` §7a.

## `.env` changes have no effect

Use `make reload`, not `docker compose restart`. Compose reads `env_file` when a
container is **created**; a restart keeps the old environment and gives no
warning.

## The public URL serves the wrong application

Tailscale Funnel proxies to IPv4 `127.0.0.1:<port>`. Docker's default
`"8080:8080"` binds the *IPv6* wildcard, so an unrelated IPv4-loopback service on
the same port wins the funnel while `curl localhost:8080` still looks right.

`make funnel` refuses to expose a port unless `/health` reports
`"service":"social-flow"`. If it refuses, it prints what answered instead. Change
`HOST_PORT` in `.env` if something else holds the port.

## Token errors

`meta_token_error` in the logs, or the account showing `token_error`. The token
expired or was revoked. Retrying cannot help, so the worker pauses the account
rather than spinning. Reconnect from the Instagram page.

Long-lived tokens last 60 days and a daily cron refreshes them. If the machine
was off for weeks, the token may be past saving — reconnect.

## An invitation link does not work

The message the recipient sees is deliberately specific, so start from that.

**"That invitation link is not valid" (404).** The token is not one of ours. Either it was
truncated in transit -- mail clients wrap long URLs -- or the invitation was **resent**, which
mints a new token and kills the old one on purpose, so an older email in the same thread is
now dead. Send them the newest link, or resend and use that.

**"No longer usable" (410).** It is ours, and spent: already accepted, revoked, or past its
deadline. `Invites` shows which. Resend to issue a fresh one. The distinction between this and
the 404 is deliberate -- collapsing them would make a late click look like a corrupted link.

**"The address /u/<name>/ was just taken" (409).** Two people picked the same workspace name.
The invitation is **not** consumed -- the reservation is released -- so they can simply choose
another and the same link still works.

**A 422 on the signup form.** The password is under 12 characters, the two do not match, or the
workspace name is invalid or reserved. Same as above: the invitation survives, because those
rules run before the token is spent.

**Nothing arrives at all.** That is a mail problem, not an invitation problem -- see the next
section. `Invites` shows "Email not sent" against a row whose message could not be queued, and
the invitation is still valid: use **Copy link** and pass it on directly.

**The link points at the wrong host.** Activation links are built from the configured public
origin, never the request's `Host` header. Check *Settings → Public address*.

## A test email or invitation never arrives

Five distinct things look identical from the outside, so check them in this order. The last
five messages and their status are on *Settings → Email*, which answers most of these without
a shell.

**1. Is there a row at all?** No row means the queue never accepted the job — the API returns
503 in that case and the row is marked `failed` rather than left pending, so an absent row
means the request itself never got that far. `docker compose logs api | grep email_enqueue_failed`.

**2. Does the row say `skipped`?** The mail settings are not filled in. The job does not retry,
because there is nothing to retry.

**3. Does it say `failed` with SMTP 535?** The password was refused. With Gmail that is an App
Password that is wrong, was revoked, or was never issued — and note Google will not issue one
at all unless two-step verification is on. The card names the date of the refusal. Retrying is
deliberately not attempted: repeated auth failures count against the Google account.

**4. Does it say `failed` with a 4xx, or a timeout?** Transient, and the worker will already
have retried up to five times with backoff. A connect timeout on an otherwise-working host is
usually egress filtering on TCP 587 — see `docs/deployment.md`.

**5. Is it stuck at `queued` or `sending`?** The worker is not running, or died mid-send.
`docker compose logs worker | grep email`.

**6. Does it say `sent`?** The mail server accepted it. That is genuinely all SMTP can tell
us — "sent" means handed off, not delivered. Check the spam folder and the sending account's
own Sent items. If Gmail accepted it but nothing arrives repeatedly, the daily cap (~500
recipients) presents as an authentication failure rather than a quota error.

> **`DRY_RUN` is not the answer here.** It suppresses Meta mutations only; email is sent
> regardless, and the Settings card says so. `EMAIL_CLIENT=mock` is the flag that stops mail.

## Worker not processing jobs

```bash
docker compose ps worker
docker compose logs worker --tail 50
docker compose exec redis redis-cli LLEN arq:queue
```

A crash-looping worker with `at least one function or cron_job must be
registered` means a task was removed from `WorkerSettings.functions`.

## Duplicate DMs

Should be impossible: three unique indexes plus a reservation taken before any
Meta call. If you see one, check the indexes actually exist —

```bash
make indexes
```

— and file it, because it means the reservation was bypassed.

## Everything is slow / Meta returns 429

Meta allows 750 private replies per hour per account. The worker retries 429s
with backoff and jitter. Your own per-rule limits are separate and are the ones
worth lowering first.

## Resetting local state

```bash
docker compose down -v     # deletes the database and queue too
make up && make bootstrap-admin
```

`-v` removes the volumes. Everything — accounts, rules, contacts, history — goes
with it.
