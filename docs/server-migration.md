# Moving Social Flow to another server

This app currently runs on a laptop, which means **automations only work while that laptop is
awake and online**. Meta does not retry a webhook forever, and a comment that arrives while the
machine is asleep is simply never answered.

This guide moves the whole thing to an always-on server on the same tailnet, keeping the
existing data: the connected Instagram account, the rules, the synced posts and the history.

**You do not need to write a Dockerfile or a compose file.** `backend/Dockerfile`,
`frontend/Dockerfile` and `docker-compose.yml` already exist and are what runs today. The
server runs exactly the same stack from the same files.

---

## First: what is the funnel, and why do you need it?

Meta calls your webhook from its own servers. It will not call:

- plain `http://` — only HTTPS
- a bare IP address
- a self-signed certificate

So the machine running Social Flow needs a **public HTTPS URL with a certificate a public CA
issued**. Normally that means buying a domain, pointing DNS at the machine, and running
something like Let's Encrypt.

**Tailscale Funnel gives you all of that for free.** It publishes one port of a tailnet machine
to the public internet at `https://<machine-name>.<tailnet>.ts.net`, terminates TLS at
Tailscale's edge with a certificate it manages, and forwards plain HTTP to `127.0.0.1:8090`,
where Caddy is listening.

That is the *only* reason the funnel exists in this project. It is not how you reach the
dashboard — you can do that over the tailnet, or over SSH — it is how **Meta** reaches the
webhook.

So yes: **you must start the funnel on the new server too**, or no automation will ever fire
there.

### The part that catches people

The funnel hostname comes from the **machine name**, so it changes when the machine changes:

```
laptop:  https://kamrans-macbook-pro.tail2bcecb.ts.net
server:  https://<whatever-the-server-is-called>.tail2bcecb.ts.net
```

That means two things must be updated, and **nothing warns you if you forget** — webhooks just
silently stop arriving:

1. `PUBLIC_BASE_URL` in `.env` on the server
2. The **OAuth redirect URI** and the **webhook callback URL** in the Meta dashboard

---

## Step 1 — Prepare the server

Install Docker Engine and the Compose plugin, and Tailscale. Join the tailnet.

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"      # log out and back in
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Nothing else is needed. There is no Python or Node on the host; everything runs in containers.

Check the machine name, because you will need it later:

```bash
tailscale status --json | grep -m1 DNSName
```

## Step 2 — Get the code onto the server

```bash
git clone git@github.com:mkamranr/insta-flow.git
cd insta-flow
```

200 files are tracked. **`.env` and the contents of `uploads/` are not** — they hold secrets
and user data, and move separately in the next two steps.

## Step 3 — Back up, on the laptop

```bash
make backup
```

Writes a timestamped pair into `backups/`:

- `mongo-<stamp>.archive` — the whole database
- `uploads-<stamp>.tar.gz` — attachments and the GeoIP database

Then **prove the dump is restorable** rather than assuming it. This restores into a scratch
database, counts what came back, and drops it again. It never touches live data:

```bash
make verify-backup ARCHIVE=backups/mongo-<stamp>.archive
```

You should see your real numbers — users, rules, instagram_accounts, media, executions. A dump
you have never restored is not a backup.

## Step 4 — Move the secrets

```bash
scp .env               user@server:~/insta-flow/.env
scp backups/mongo-*    user@server:~/insta-flow/backups/
scp backups/uploads-*  user@server:~/insta-flow/backups/
ssh user@server 'chmod 600 ~/insta-flow/.env'
```

Three values in `.env` must be carried across **unchanged**. Regenerating them is silent and
destructive:

| Value | What a new one costs you |
| --- | --- |
| `ENCRYPTION_KEY` | Every stored Instagram token becomes unreadable. You must reconnect the account. |
| `SESSION_SECRET` | Everyone is logged out. Harmless, but avoidable. |
| `META_APP_ID`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN` | Same Meta app, so the same values. A different verify token fails the webhook handshake. |

`PUBLIC_BASE_URL` is the one value you **will** change — in step 7, once you know the new
funnel hostname.

## Step 5 — Restore, before starting the app

Bring up only the database first. Restoring underneath a running worker risks it acting on
half-restored rows.

```bash
docker compose up -d mongodb
make restore ARCHIVE=backups/mongo-<stamp>.archive
tar xzf backups/uploads-<stamp>.tar.gz
```

`make restore` asks you to type the database name before it drops anything.

The `uploads/` tarball matters more than it looks: attachments are fetched **by Meta, from
Meta's servers**, at the moment a DM is sent. A missing file is a 404 that you never see and
the recipient just never gets the file. The 63 MB GeoIP database inside it can instead be
re-downloaded later from **Settings → Click geography**, if you would rather not copy it.

## Step 6 — Start the app

```bash
docker compose up -d
docker compose ps          # all six healthy
curl localhost:8090/health # {"status":"ok","service":"social-flow"}
```

**Check `docker compose ps` before moving on.** `up -d` returns as soon as containers are
created and tells you nothing about whether the processes inside survived — a crash-looping
MongoDB looks exactly like success here. Look for `healthy`, not just `Up`. If anything says
`Restarting`, stop and read [Troubleshooting](#troubleshooting).

Indexes need no step — they are created automatically on API and worker startup.

## Step 7 — Start the funnel and learn the new URL

```bash
make funnel
make tunnel-status
```

`make funnel` refuses to publish unless `/health` already answers, so you cannot expose a
broken stack. `make tunnel-status` prints exactly what you need:

```
PUBLIC_BASE_URL=https://<server>.tail2bcecb.ts.net
  redirect URI : https://<server>.tail2bcecb.ts.net/api/auth/instagram/callback
  webhook URL  : https://<server>.tail2bcecb.ts.net/webhooks/instagram
```

Put that `PUBLIC_BASE_URL` into `.env`, then:

```bash
make reload
```

**`make reload`, not `docker compose restart`.** Compose reads `env_file` when a container is
*created*, so a restart silently keeps the old value and you would spend an hour wondering why
the URL never changed.

Verify from **outside the tailnet** — your phone on mobile data, or any machine that is not on
your tailnet:

```
https://<server>.tail2bcecb.ts.net/health
```

A `curl` on the server itself proves nothing: the `.ts.net` name can resolve locally through
Tailscale's own DNS and never leave the machine.

## Step 8 — Re-point Meta

In the Meta dashboard, update **both**, then re-verify the webhook:

- **Valid OAuth Redirect URI** → `https://<server>.tail2bcecb.ts.net/api/auth/instagram/callback`
- **Webhook callback URL** → `https://<server>.tail2bcecb.ts.net/webhooks/instagram`

Two different dashboard pages have a field called "Valid OAuth Redirect URIs". Only the one
under **Instagram → API setup with Instagram business login** is read. The Facebook Login
page's validator will even tell you the URL is valid, which is actively misleading.

## Step 9 — Decide what happens to the laptop

**Do not leave both running with the funnel on.** Both instances receive the same webhook and
both send a DM. Execution idempotency keys live in each instance's own database, so nothing
prevents the double send — the recipient just gets two messages.

Pick one:

```bash
# The server takes over. Safest.
make funnel-off                     # on the laptop

# Or keep the laptop for development: it runs the full pipeline and can never send.
# In the laptop's .env:
DRY_RUN=true
# and leave the funnel off.
```

## Step 10 — Confirm it actually works

In order. Each step gates the next:

1. `docker compose ps` — six containers, healthy
2. `curl localhost:8090/health` on the server
3. `/health` **from outside the tailnet**
4. Open the dashboard, sign in
5. **Instagram → still connected**, token expiry unchanged. If it says the token cannot be
   read, `ENCRYPTION_KEY` did not survive step 4
6. Webhook re-verified in the dashboard
7. Comment on a post from another account and watch it end to end:

```bash
docker compose logs -f api worker | grep -E "webhook_received|message_reply_sent|private_reply"
```

---

---

## Troubleshooting

### "Container is restarting, wait until the container is running"

You ran `make restore` (or `make backup`) and got this from Docker. **The problem is not the
restore — MongoDB never started.**

`docker compose up -d mongodb` returns as soon as the container is *created*. It says nothing
about whether the process inside survived, so a crash-looping `mongod` looks like success right
up until something tries to use it.

Find the actual reason:

```bash
docker compose logs mongodb --tail 40
docker inspect --format '{{.State.ExitCode}} {{.State.OOMKilled}}' $(docker compose ps -q mongodb)
```

Three causes cover almost all of these:

| The log says | Cause | Fix |
| --- | --- | --- |
| `Illegal instruction`, `signal=ILL`, or a warning about AVX | **The CPU has no AVX.** MongoDB 5.0 and later require it and will not start without it. Common on older VPS hardware and some virtualised CPUs | Pin an older MongoDB — see below |
| Exit code `137`, `OOMKilled: true` | Out of memory. MongoDB wants ~1 GB to itself | Add RAM, or add a swap file |
| `WiredTiger` errors, "unclean shutdown", a version complaint | The data volume was written by a different MongoDB version | `docker compose down -v` to discard it — **only** if nothing on that server matters yet |

Check AVX directly:

```bash
grep -o -m1 avx /proc/cpuinfo || echo "NO AVX — mongo:8.0 cannot run on this machine"
```

### If the server has no AVX

MongoDB 4.4 does not require AVX and supports everything this app uses, including the four
partial unique indexes that provide duplicate-send protection. Nothing in the code needs 8.0.

Create `docker-compose.override.yml` **on the server only** — Compose merges it automatically,
and it is untracked, so the laptop stays on 8.0:

```yaml
services:
  mongodb:
    image: mongo:4.4
```

Then start clean, because a volume written by 8.0 cannot be read by 4.4:

```bash
docker compose down -v
docker compose up -d mongodb
make restore ARCHIVE=backups/mongo-<stamp>.archive
```

The dump restores fine across versions; it is the on-disk volume format that does not.

### `docker compose up -d` worked but the app is broken

Compose starting a container is not the same as the program inside staying alive. Always
follow it with:

```bash
docker compose ps          # look for "healthy", not just "Up"
```

`make backup`, `make verify-backup` and `make restore` all check this first and refuse with an
explanation rather than letting Docker's raw error surface.

### Instagram shows as disconnected after the restore

`ENCRYPTION_KEY` did not survive the move. Meta tokens are AES-256-GCM encrypted at rest with
that key, so a different one makes every stored token unreadable. Copy the original value from
the laptop's `.env` and `make reload`. If the key is genuinely lost, reconnect the account
through the dashboard.

### Everything looks healthy but no automation ever fires

Almost always the funnel hostname. Check, in this order:

```bash
make tunnel-status                     # is the funnel even up, and what is the hostname?
grep PUBLIC_BASE_URL .env              # does it match?
docker compose logs api --since 1h | grep webhook_received
```

If `PUBLIC_BASE_URL` is right but nothing arrives, the **Meta dashboard** still points at the
old machine. Update the webhook callback URL and re-verify it. Watch the handshake land:

```bash
docker compose logs -f caddy | grep webhooks
```

And confirm the endpoint is reachable **from outside the tailnet** — a phone on mobile data.
A `curl` on the server itself can resolve the `.ts.net` name locally and never leave the box,
so it proves nothing.

## Notes for a Linux server

- **`AI_BASE_URL=http://host.docker.internal:11434/v1`** is a Docker-Desktop-only hostname and
  will not resolve on Linux. Harmless while `AI_ENABLED=false`, which is the default — but if
  you turn AI on, point it at the host's tailnet IP instead.
- **Caddy binds `127.0.0.1:${HOST_PORT}`.** That is deliberate and is what the funnel expects.
  Do not publish it more widely, and do not open any port in the server's firewall — the funnel
  reaches it over loopback.
- **The funnel must survive a reboot**, or your always-on server is not always on. `make funnel`
  runs it in the background but does not install a service; check it after your first reboot.
- **Set a backup schedule.** `make backup` in cron, keeping 7 daily copies. MongoDB is the
  authoritative store — Redis holds only in-flight jobs and can be discarded.
- **Follower snapshots cannot be recovered.** Meta keeps no history, so a day the server is
  down is a day of follower growth that no backup can bring back.
