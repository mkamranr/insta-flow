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
# Account level - what InstaFlow set. Should list "comments".
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
webhook; they are skipped deliberately, or InstaFlow would answer itself.

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
| `rule_disabled` | The automation is paused |

**6. Was it a dry run?**

`DRY_RUN=true` records everything and sends nothing. That is the default. The
banner at the top of the dashboard says so.

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
`"service":"instaflow"`. If it refuses, it prints what answered instead. Change
`HOST_PORT` in `.env` if something else holds the port.

## Token errors

`meta_token_error` in the logs, or the account showing `token_error`. The token
expired or was revoked. Retrying cannot help, so the worker pauses the account
rather than spinning. Reconnect from the Instagram page.

Long-lived tokens last 60 days and a daily cron refreshes them. If the machine
was off for weeks, the token may be past saving — reconnect.

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
