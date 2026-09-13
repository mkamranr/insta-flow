# Deployment

## Public HTTPS endpoint

> **Moving to another machine?** See [server-migration.md](server-migration.md) for the
> step-by-step, including what the funnel is actually for and the two things that must be
> re-pointed when the hostname changes.


Meta requires a publicly reachable HTTPS URL for the OAuth redirect and the webhook callback.
Three free-or-cheap options; this deployment uses **A**.

### A. Tailscale Funnel (current setup)

Stable hostname, free, valid certificate, no domain purchase. Tailscale terminates TLS at its
edge and forwards plain HTTP to Caddy — which is why `infra/caddy/Caddyfile` listens on
`:8080` with **no TLS directives**.

```bash
tailscale up         # log in, if not already
make funnel          # tailscale funnel --bg 8080
make tunnel-status   # prints the exact URLs for the Meta dashboard
make funnel-off      # stop exposing
```

> **Port collisions are silent and dangerous.** Funnel proxies to IPv4
> `127.0.0.1:<port>`. Docker's default `"8080:8080"` binds the *IPv6* wildcard, so an
> unrelated IPv4-loopback service on the same port can win the funnel while
> `curl localhost:8080` still looks correct (it resolves to `::1` and reaches Docker).
> The result is that the internet sees the wrong application.
>
> Two guards: Caddy is published on `127.0.0.1:${HOST_PORT:-8090}` explicitly, and
> `make funnel` runs `funnel-preflight`, which refuses to expose the port unless
> `/health` reports `"service":"social-flow"`. Change `HOST_PORT` in `.env` if 8090 is
> taken.

**First run only — enable Funnel on the tailnet.** Funnel is off by default and is a
tailnet-level permission, not a per-machine one. The first `make funnel` will print:

```
Funnel is not enabled on your tailnet.
To enable, visit:

         https://login.tailscale.com/f/funnel?node=<YOUR_NODE_ID>
```

Open that URL, approve, then run `make funnel` again. The command **blocks silently** while it
waits for approval, which looks like a hang — it is not. Press Ctrl-C, approve, and re-run.

This grants the `funnel` node attribute in your tailnet policy. It is a one-time step; later
runs of `make funnel` go straight through.

For this machine:

```
PUBLIC_BASE_URL = https://your-machine.your-tailnet.ts.net
redirect URI    = https://your-machine.your-tailnet.ts.net/api/auth/instagram/callback
webhook URL     = https://your-machine.your-tailnet.ts.net/webhooks/instagram
```

Those are the URLs for the Meta app configured in `.env`. A user who configures **their own**
Meta app from Settings gets a workspace-prefixed set instead —
`https://your-machine.your-tailnet.ts.net/u/<slug>/webhooks/instagram` and so on — printed
ready to copy on their own Settings page. See `docs/meta-api.md` §7b.

Funnel must be enabled in the tailnet policy; the first `tailscale funnel` run prints an
admin-console link if it is not.

**Caveat:** webhooks only arrive while the machine is awake and online. Comments that arrive
while it is asleep are missed — Meta retries for a while, but not indefinitely. For
always-on operation, use option C on a small server.

### B. Cloudflare Quick Tunnel

```bash
cloudflared tunnel --url http://127.0.0.1:8090
```

No account needed, but the `*.trycloudflare.com` hostname is random and changes on every
restart, so the redirect URI and webhook URL must be re-entered in the Meta dashboard and the
webhook re-verified each time. Useful for a one-off test, not for ongoing use.

### C. Your own domain

Point a domain at a host with a public IP, open 80/443, and let Caddy obtain certificates
automatically. This is the only always-on option.

**Change only the site address line.** In `infra/caddy/Caddyfile`, replace

```
:8080 {
```

with

```
instagram.yourdomain.com {
```

and leave everything inside the block exactly as it is. Then publish `80:80` and
`443:443` in `docker-compose.yml` instead of the `127.0.0.1:${HOST_PORT}:8080` loopback
mapping. The comment at the top of the Caddyfile says the file must not contain a public
hostname; with Caddy terminating TLS itself that no longer applies, so update it rather
than leaving the next reader to trust it.

> **Do not replace the block with a hand-written one.** An earlier version of this section
> offered a four-line sample carrying only `/api/*`, `/webhooks/*` and a catch-all to the
> frontend. Pasting it drops every other route in the file, and each failure is silent:
>
> | Dropped | What breaks |
> | --- | --- |
> | `@workspace` path_regexp | `/u/<slug>/api/...` falls through to the SPA — the `405 Not Allowed` / raw-`<!doctype html>` trap |
> | `/privacy`, `/data-deletion` | The two URLs Meta requires for Live mode stop resolving, which can take the app out of Live |
> | `/files/*` | Meta fetches an attachment for a DM and receives an HTML page instead (`docs/meta-api.md` §4c) |
> | `/r/*` | Trackable link redirects stop resolving, so every click is lost |
> | `/health`, `/ready` | Monitoring, and `make funnel`'s `"service":"social-flow"` preflight |
> | `header` block | CSP, HSTS, `nosniff` and `X-Frame-Options` all silently gone |
> | `log` filter | The invite token and Meta's verify token reach the container log unredacted |
> | `trusted_proxies` | Click geography records every visitor as unlocatable |
>
> The Makefile carries a comment noting that a Caddyfile mistake of exactly this kind
> "cost a production incident". Keep the file and change its address.

## Container runtime

Docker Desktop's licence requires a paid subscription for government bodies and larger
organisations. Everything here is plain Docker Compose with no Desktop-specific features, so
a free runtime is a drop-in replacement:

```bash
brew install colima docker docker-compose
colima start --cpu 4 --memory 8
```

`make up` and friends work unchanged.

## Firewall

Expose **only** the port Caddy listens on. Never publish 27017 (MongoDB), 6379 (Redis), 8000
(API), or 3000 (frontend) — the compose file uses `expose:` rather than `ports:` for all of
them, so this holds unless someone changes it.

With Tailscale Funnel, nothing needs to be open on the host firewall at all: Tailscale dials
out.

Everything above is about **inbound** traffic. Outbound email needs egress on TCP 587 (or 465
for SSL) from the `worker` container. On an egress-filtered host that is easy to miss, because
the symptom is a connect timeout on the Settings page's test send with nothing else wrong.

## Attachment storage

Rules can carry a file — image, video, audio or PDF — that Meta delivers with the payoff DM.
Meta does not accept bytes; it takes a URL and fetches it, so uploads have to be served
publicly (docs/meta-api.md §4c).

- Files land in `./uploads/<uuid>/<slug>-<hex>.<ext>`, bind-mounted into the API container at
  `/app/uploads`. They live on the host, so a rebuild does not lose them.
- They are served at `/files/<uuid>/<name>` **without authentication** — Meta carries none of
  our credentials. The UUID is the only thing keeping a file unlisted. Treat anything uploaded
  as readable by whoever holds the URL.
- Caddy needs a `handle /files/*` route. Without it requests fall through to the SPA and Meta
  silently receives an HTML page instead of the file.
- Meta only fetches **once, at send time**, then re-hosts on `cdn.fbsbx.com`. The host must be
  reachable at that moment, not forever.
- Deleting a rule deletes its file, and replacing an attachment deletes the one it replaced.
  Nothing else references them, so an orphan would sit there at up to 25MB a time.

Back these up with the database if the automations matter; `uploads/` is gitignored.

## Email (optional)

Needed only for sending invitations. Nothing else in the product sends mail, and a deployment
that never configures it reports itself healthy — `missing_required()` deliberately does not
mention SMTP.

The settings live in the database, not in `.env`, so they are filled in on
*Settings → Email* by anyone holding `instance.manage` (Owner only, by default). With a Gmail
account:

1. Turn on two-step verification for the Google account — Google will not issue an App
   Password without it.
2. Create an **App Password** at <https://myaccount.google.com/apppasswords>. It is 16
   characters, shown with spaces; either form is accepted.
3. Fill in `smtp.gmail.com`, port `587`, STARTTLS, the Gmail address as both username and
   sender, and the App Password.
4. Click **Send test email**. The credentials are deliberately *not* checked when you save —
   verifying would mean an AUTH round trip on every save, and Google rate-limits and
   eventually flags that. The test send is the check, and it exercises the same path a real
   invitation takes.

Two Gmail constraints worth knowing before relying on it. Gmail rejects or silently rewrites
a `From` address the account does not own, so keep the sender equal to the username or use a
verified alias. And it caps outbound at roughly **500 recipients a day**; exceeding it
presents as an authentication failure rather than a quota error.

The send happens in the **worker**, not the API, so `docker compose logs worker` is where to
look. The Settings card also lists the last five messages with their status, which is the
faster answer.

**`DRY_RUN` does not suppress email.** It means "no Meta mutation", and an invitation is not
one — so mail is sent even on a default install. `EMAIL_CLIENT=mock` in `.env` is what stops
mail leaving the server.

## Click geography (optional)

Country and city for link clicks, from MaxMind's **GeoLite2 database** — not their web
service. The database is downloaded here and every lookup runs locally, so no visitor IP is
ever sent to MaxMind.

Set it up in **Settings → Click geography** with a MaxMind account ID and license key. The key
is encrypted at rest and never returned by the API. Credentials are verified against MaxMind
on save, so a typo surfaces immediately rather than as a quietly empty report weeks later.

- The database refreshes automatically on Wednesdays and Saturdays. MaxMind rebuilds GeoLite2
  twice a week, and the job checks with a HEAD request first, which does not count against the
  daily download limit.
- It lands in `./uploads/.geoip/GeoLite2-City.mmdb`, about 65MB.
- **Only the derived country and city are stored.** The IP is used for the lookup and dropped.
- Locations are resolved at click time, so clicks made before setup have none and cannot be
  backfilled.

**The visitor's address has to survive the proxy chain.** `request.client.host` is always
Caddy, so the code reads `X-Forwarded-For` and takes the first public entry scanning right to
left. If the chain never carried a public address, the click is recorded without a location
rather than attributed to a proxy. A click that could not be placed logs `click_not_located`
with the chain *shape* — `private>private` versus `public>private`, never the addresses — which
is how to tell "no database" apart from "the address never reached us".

## Backups

MongoDB is authoritative; Redis holds only in-flight jobs and can be discarded.

```bash
docker compose exec -T mongodb mongodump --archive --db=instaflow | gzip > backup-$(date +%F).gz
```

Retain 7 daily and 4 weekly copies. Restore with `mongorestore --archive --drop`.

Take one before every upgrade — `make backup` then `make verify-backup` do both halves. See
`docs/upgrading.md`, which also covers why `make reload` alone does not deploy new code.

## Production checklist

Written in Phase 18. The essentials already in place: pinned image tags, healthchecks on
mongodb/redis/api, `restart: unless-stopped`, named volumes for both datastores, secret
redaction in logs, and `DRY_RUN=true` by default.


## MongoDB authentication

Off by default so `make up` works with no setup. MongoDB is unreachable outside
the compose network, so this is defence in depth — but enable it before running
on a shared host.

Add to `.env`:

```env
MONGO_INITDB_ROOT_USERNAME=instaflow
MONGO_INITDB_ROOT_PASSWORD=<openssl rand -hex 24>
MONGO_URL=mongodb://instaflow:<same password>@mongodb:27017/?authSource=admin
```

and to the `mongodb` service in `docker-compose.yml`:

```yaml
    environment:
      MONGO_INITDB_ROOT_USERNAME: ${MONGO_INITDB_ROOT_USERNAME}
      MONGO_INITDB_ROOT_PASSWORD: ${MONGO_INITDB_ROOT_PASSWORD}
```

Credentials are only applied to a **fresh** data volume. On an existing one,
either create the user by hand with `mongosh`, or `docker compose down -v` and
start over — which deletes everything.

## Production environment

```env
APP_ENV=production
SESSION_COOKIE_SECURE=true
DRY_RUN=false
```

`SESSION_COOKIE_SECURE=true` means the cookie is only sent over HTTPS, so the
dashboard must be reached through the public URL rather than
`http://localhost`.
