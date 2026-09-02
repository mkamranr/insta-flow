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
