# Deployment

## Public HTTPS endpoint

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
automatically. Replace the Caddyfile's `:8080` block with:

```
instagram.yourdomain.com {
    encode gzip
    handle /api/*      { reverse_proxy api:8000 }
    handle /webhooks/* { reverse_proxy api:8000 }
    handle             { reverse_proxy frontend:3000 }
}
```

and publish `80:80` and `443:443` instead of the loopback mapping. This is the only
always-on option.

## AWS: one EC2 instance, images from ECR

Tailscale Funnel only carries webhooks while the Mac is awake. This is the always-on version:
a single small instance running the same Docker Compose stack, with the two application images
pulled from ECR rather than built on the box.

Nothing about the application changes. Only packaging and how it is reached.

### What goes to ECR, and what does not

AWS's own push instructions assume one image built from a Dockerfile at the repository root.
**There is no Dockerfile at the root**, and this stack is six services:

| Service | Image | To ECR? |
| --- | --- | --- |
| `api` | built from `backend/` | yes, tagged `api` |
| `worker` | **the same image**, different `command` | no — nothing separate to push |
| `frontend` | built from `frontend/` | yes, tagged `web` |
| `caddy`, `mongodb`, `redis` | official images | no — pulled from Docker Hub |

So `make push` builds two images and pushes four tags: `api`, `web`, and a `-<sha>` copy of
each. The moving tags are what production runs; the SHA tags are what you roll back to.

```
make push                 # from the Mac; refuses if the working tree is dirty
```

The image tag is the git SHA, so a dirty tree would produce an artifact that no commit
describes — hence the refusal.

### The instance

- **t3.small** or larger. The images total roughly 1.3 GB and MongoDB wants headroom;
  t3.micro's 1 GB of RAM is tight once Mongo, Redis and two Python processes are resident.
- **30 GB gp3** root volume. `uploads/` grows with attachments, and the GeoLite2 database
  alone is 63 MB.
- **Elastic IP**, so the address survives a stop/start. A domain pointed at a changing IP is
  the classic way to lose webhooks silently.
- **Security group: inbound 80 and 443 only.** Never 27017, 6379, 8000 or 3000 — those are
  `expose:`d to the Docker network, not published, and must stay that way.
- Install Docker and the Compose plugin. Nothing else; there is no Python or Node on the host.

### The domain

Meta will not accept a bare IP or a self-signed certificate for a webhook callback, so a
hostname is required. Any registrar works.

1. Point an `A` record at the Elastic IP.
2. Put it in `.env` as `SITE_DOMAIN`.
3. `infra/caddy/Caddyfile.prod` reads it from the environment and Caddy obtains a Let's
   Encrypt certificate on first boot. Port 80 must be open for the HTTP-01 challenge; Caddy
   redirects it to HTTPS afterwards.

`caddy_data` now holds the ACME account key and certificates. It is a named volume, so it
survives container replacement — but never prune it, or repeated re-issue will hit Let's
Encrypt rate limits.

### First boot

```
git clone <repo> && cd insta-flow          # for the compose file and Caddyfile
cp .env.example .env && $EDITOR .env       # see the AWS block at the bottom
make ecr-login
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Set before the **first** `up`, because they cannot be applied afterwards:

- `MONGO_USER` / `MONGO_PASSWORD` — applied only to a fresh data volume
- `ADMIN_SETUP_SECRET` — so the login page offers first-run admin creation. There is no
  `make bootstrap-admin` from a pulled image: `scripts/` is bind-mounted, not baked in
- `ENCRYPTION_KEY` and `SESSION_SECRET` — carried over **unchanged** from any existing
  deployment. A new encryption key makes every stored Instagram token unreadable

Indexes need no step: `create_indexes` runs on both API and worker startup.

### Moving existing data across

```
# on the Mac
docker compose exec -T mongodb mongodump --archive --db=instaflow | gzip > backup.gz
scp backup.gz uploads.tar.gz ec2-user@<ip>:~/

# on the instance
gunzip -c backup.gz | docker compose -f docker-compose.prod.yml exec -T mongodb \
  mongorestore --archive --username "$MONGO_USER" --password "$MONGO_PASSWORD" \
  --authenticationDatabase admin
```

Copy `uploads/` too. Attachments are referenced by absolute URL and Meta fetches them from its
own servers, so a missing file is a 404 at send time. The GeoLite2 database inside it can be
re-downloaded from Settings instead of copied.

### Then, in the Meta dashboard

Both of these move to the new domain, and the webhook needs re-verifying:

- **OAuth redirect URI** — `https://your.domain/api/auth/instagram/callback`
- **Webhook callback URL** — `https://your.domain/webhooks/instagram`

Remember there are two dashboard pages with a "Valid OAuth Redirect URIs" field. Only the one
under *Instagram → API setup with Instagram business login* is read.

Finally, `make funnel-off` on the Mac. Two instances both answering webhooks would double-send.

### Deploying a change

```
make push                                              # on the Mac
docker compose -f docker-compose.prod.yml pull         # on the instance
docker compose -f docker-compose.prod.yml up -d
```

To roll back, set `API_TAG=api-<sha>` in `.env` and `up -d` again.

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
