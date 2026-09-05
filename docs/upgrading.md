# Upgrading a running deployment

For a server that is already running Social Flow and needs the newest version.

Standing a new server up instead is `docs/deployment.md`; moving an existing one to different
hardware is `docs/server-migration.md`.

---

## The one thing that catches people

`make reload` is `docker compose up -d`. After a `git pull` **the images already exist**, so
`up -d` on its own recreates containers from the *old* images and you have deployed nothing.
Nothing errors. `docker compose ps` says healthy. The app runs the previous version.

The frontend makes it worse: the dashboard is compiled into its image at build time
(`frontend/Dockerfile` runs `npm run build`, then copies `dist/` into nginx). An unrebuilt
frontend serves the old bundle, so any new page 404s while the API happily answers.

So the sequence is always:

```bash
git pull
make build      # <- the step that is easy to skip
make reload
```

---

## Step 1 — Make sure the code is actually pushed

The server pulls `master` from `github.com/mkamranr/insta-flow`. Work sitting on a branch, or
committed but not pushed, will not arrive:

```bash
git status --short          # nothing uncommitted
git log origin/master..HEAD # nothing unpushed
```

## Step 2 — Back up, and prove the backup

On the server:

```bash
make backup
make verify-backup ARCHIVE=backups/mongo-<stamp>.archive
```

`verify-backup` restores into a scratch database, counts what came back, and drops it again.
It never touches live data. You should see your real numbers.

This is not optional ceremony. Some releases write to collections that already hold your
data — the one below rewrites every row in `users`. A dump you have never restored is a
hypothesis, not a backup.

## Step 3 — Pull, build, reload

```bash
git pull
make build
make reload
docker compose ps
```

**Read `docker compose ps` before moving on.** `up -d` returns as soon as containers are
created and says nothing about whether the processes inside survived. Look for `healthy`, not
just `Up`. If anything says `Restarting`, see Troubleshooting in `docs/server-migration.md`.

`make build` rebuilds `api`, `worker` and `frontend`. `api` and `worker` share the same image
context (`./backend`), so both pick up backend changes.

### Never deploy the frontend ahead of the API

The dashboard calls endpoints that may not exist in the older API — a new admin page hitting
a 404 looks like a broken app, not a version skew. The reverse is safe: an old frontend
against a new API simply ignores fields it does not know about.

`make build && make reload` does both together, which is what you want. Only if you are
deploying services one at a time does the order matter, and then it is API first.

## Step 4 — Migrations run themselves

There is no migration command. `prepare()` runs in the API's startup lifespan and brings the
database up to date: indexes first, then any seed data, then any backfill. The same function
runs from `scripts/create_indexes.py`, `scripts/bootstrap_admin.py` and the test fixtures, so
there is one code path and it cannot drift.

What that means in practice: **the migration happens the moment the new API container
starts**, before it serves a request. If it fails, the container fails its healthcheck and you
will see it in `docker compose ps`.

## Step 5 — Verify

```bash
curl -s localhost:8090/ready | python3 -m json.tool   # "status": "ready"
docker compose ps                                     # six services, all healthy
docker compose logs api --tail 50                     # startup_complete, no tracebacks
```

Then sign in and click through whatever the release changed. The per-release notes below say
what to look at.

## Step 6 — If you need to roll back

```bash
git checkout <previous-sha>
make build
make reload
```

Mechanically safe: new collections and new fields are simply ignored by older code, and no
data is lost.

**Read the release's rollback note first, though.** Rolling back does not undo a migration,
and for some releases that matters — see the warning under this release's notes.

---

# Release notes

## Workspaces: per-user Meta apps and `/u/<slug>/` URLs

### What is new

- **Workspaces.** Every user gets one, addressed at `/u/<slug>/` — dashboard *and* the
  Meta-facing callbacks. The slug is derived from their email local part.
- **Per-workspace Meta apps.** Settings → **Instagram app** lets each person connect through
  **their own** Meta app, with their own App ID, App Secret and webhook verify token, and
  prints the four URLs to paste into Meta's dashboard.
- **A workspace switcher** under the logo, for anyone holding the new `workspaces.view`. With
  one workspace it stays the plain block it always was.
- **Server settings split out.** Click geography and the new public-address setting moved from
  `settings.*` to a new `instance.*` module. They are one setting for the whole deployment, so
  the same grant used to mean both "may reword an automated reply" and "may re-point the
  server". Only Owner holds `instance.*` by default.

### Why this needs no App Review

Each user brings **their own** Meta app for **their own** Instagram account, so nobody's
account is ever reached through somebody else's app. That keeps every user in the "automating
your own account" case, which needs only Live mode. Reaching another person's account through
your app is the third-party case that needs Advanced Access, App Review and Business
Verification. See `docs/meta-api.md` §7b — and do not collapse this back into one shared app.

### What this release does to your database

On the first start of the new API:

1. Creates the `meta_apps` collection and its unique index on `user_id`.
2. Adds `uq_user_slug` to `users` — unique, and **partial** on `{"slug": {"$type": "string"}}`.
   It has to be partial: the index is created before the backfill runs, so at that moment every
   existing user is still slug-less, and a non-partial unique index reads them all as null,
   collides, and aborts startup on exactly the deployments that need backfilling.
3. Gives every existing user a slug, oldest first — so on a single-operator install the
   original operator keeps the unsuffixed one rather than being bumped to `-2` by someone who
   signed up later. Logged once:

```bash
docker compose logs api --tail 80 | grep backfilled
# users_backfilled_slugs  {"count": 1}
```

4. Re-syncs the Owner role, which picks up `workspaces.view` and `instance.*` automatically.
   Manager and Viewer are operator-owned and are left untouched — so **a custom role that
   relied on `settings.edit` to reach Click geography loses it** and needs `instance.manage`
   granted explicitly on the Roles page.

**Nobody is signed out**, and **no `.env` change is needed.** No new environment variables and
no new dependencies were added.

### Nothing changes in the Meta dashboard

No new webhook URL, no new redirect URI, no verify-token change. Your existing Meta app keeps
calling the URLs it already has.

That is deliberate, not accidental. A workspace with no `meta_apps` row falls back to the
`META_*` values in `.env` **and to the old unprefixed URL layout** — `/webhooks/instagram`,
`/api/auth/instagram/callback`. Deriving `/u/<slug>/` URLs for the environment's app would hand
an already-live Meta app a redirect URI it has never heard of, and reconnecting would start
failing for no visible reason. The rule in `services/meta/credentials.py` is: **the URL layout
follows the credentials, not the request.** Leave it that way.

Moving a workspace onto its own Meta app is opt-in, whenever you want it, and only then do the
prefixed URLs apply.

`/ready` no longer reports `META_APP_ID`, `META_APP_SECRET` or `META_WEBHOOK_VERIFY_TOKEN` as
missing configuration — they are a default now, not a requirement, since an instance where
every workspace brought its own app needs none of them. It still requires `ENCRYPTION_KEY` and
`SESSION_SECRET`.

### Verifying this release

**Check Caddy first.** `infra/caddy/Caddyfile` gained a matcher routing
`/u/<slug>/{api,webhooks,privacy,data-deletion}` to the API. Caddy loads its config into memory
at start and the file is bind-mounted, so editing it on disk changes nothing until the
container is recreated. `make reload` does recreate it, because `caddy` depends on `api` — but
check, because the failure is severe and points nowhere near Caddy: without the route,
`/u/<slug>/api/...` falls through to the SPA and returns `index.html`, so every API call fails
as a JSON parse error and the whole dashboard looks broken.

```bash
curl -s http://127.0.0.1:8090/u/anything/api/auth/me | head -c 40
# {"error":{"code":"authentication_error"   <- correct, the API answered
# <!doctype html>                           <- stale Caddy config:
#                                              docker compose up -d --force-recreate caddy
```

Then the migration and the unchanged webhook:

```bash
docker compose exec -T mongodb mongosh instaflow --quiet --eval '
  db.users.find({}, {email:1, slug:1, _id:0}).forEach(u => print(JSON.stringify(u)));
  print("meta_apps: " + db.meta_apps.countDocuments({}));'

TOKEN=$(grep -E "^META_WEBHOOK_VERIFY_TOKEN=" .env | cut -d= -f2- | tr -d " ")
curl -s "http://127.0.0.1:8090/webhooks/instagram?hub.mode=subscribe&hub.verify_token=$TOKEN&hub.challenge=ok"
# ok
```

Expect a slug on every user and `meta_apps: 0` — zero is correct, and is what keeps you on
your existing Meta app.

In the dashboard: signing in lands you at `/u/<your-slug>/`, every page works as before, and
Settings → Instagram app says *"This workspace is on the instance-wide app from `.env`"* while
listing the prefixed URLs you would register if you ever wanted your own.

### Rolling back this release — read this first

Mechanically safe: `meta_apps`, `users.slug` and the `instance.*` permissions on Owner are all
ignored by older code, and nothing is lost. `uq_user_slug` lingers on the `users` collection
and is harmless.

**The roles release's rollback warning still applies on top of this one** — see below. If you
roll back past both, any restricted user you created becomes an administrator, because the
older code consults no role at all.

---

## Roles, permissions, user management, and clone automation

### What is new

- **Roles and permissions.** An admin defines roles as a grid of feature/action permissions,
  creates users, and assigns roles. Two new pages, `/users` and `/roles`, under a new
  **Administration** group in the sidebar.
- **Server-side enforcement on every `/api` route.** A test walks the route table and fails
  the build if a route ships without declaring a permission.
- **Clone automation.** A post that already has an automation gets a **Clone** button; pick
  posts that have none and the automation is copied to each as an independent rule.

### What is fixed

- **A cross-tenant IDOR.** `GET`, `PATCH` and `DELETE /api/rules/{rule_id}` fetched a rule by
  id with no ownership check. Harmless with one user; a hole the moment a second existed —
  which is exactly what this release makes possible. Small behaviour change: those three now
  return **422** rather than 404 when no Instagram account is connected, matching
  `GET /api/rules`.
- **Attachments are refcounted before deletion.** `attachments.delete` removes the whole
  upload folder, and the Duplicate button on the Automations page copies `attachment`
  verbatim — so two rules could point at one folder and deleting either destroyed the file for
  both. The survivor kept sending DMs with an attachment URL that 404s, and the worker only
  logs a warning, so the recipient silently got the text and no file. Deletion now checks
  whether another rule still references the file.
- **`make test` was running the wrong code.** A previous commit deleted the `DEV_MOUNT`
  definition from the `Makefile` but left its three uses, so `make test`, `make lint` and
  `make format` ran against the image baked at build time rather than your working tree — and
  would have found no tests at all once the image was rebuilt, since `backend/.dockerignore`
  excludes `tests`. Restored.

### What this release does to your database

On the first start of the new API:

1. Creates the `roles` collection and its unique index on `slug`.
2. Seeds three roles — **Owner** (every permission; re-synced on every boot so a future
   release's new permissions reach it automatically; cannot be edited or deleted),
   **Manager** and **Viewer** (seeded once, then yours to change or remove).
3. Sets `role_id` on **every existing user**.

**Every account that existed before the upgrade becomes an Owner.** That is deliberate:
before this release there was no authorization at all, so those accounts already had every
capability, and Owner preserves exactly that rather than granting anything new. Anything less
would lock you out of your own deployment on upgrade. It is logged, once, at WARNING:

```bash
docker compose logs api --tail 50 | grep backfilled
# users_backfilled_to_owner  {"count": 1}
```

**Nobody is signed out.** Session cookies carry only a user id and an epoch, and permissions
are read fresh on every request, so open sessions keep working and pick up their permissions
on the next request. The same property means a role change takes effect immediately, without
the person signing in again.

**No `.env` change is needed**, and nothing changes in the Meta dashboard — no new webhook,
no new redirect URI.

### Verifying this release

```bash
docker compose exec -T mongodb mongosh instaflow --quiet --eval '
  db.roles.find({}, {slug:1, is_system:1}).toArray().forEach(r => print(r.slug, r.is_system));
  db.users.find({}, {email:1, role_id:1}).toArray().forEach(u => print(u.email, u.role_id));
'
```

Expect `owner true`, `manager false`, `viewer false`, and a `role_id` on every user.

In the dashboard: the **Administration** group appears with Users and Roles; on Roles, Owner
shows as *Built-in* and offers View rather than Edit, with no Delete.

Then prove the gate is real rather than hidden UI. Create a role holding only
`analytics.view`, add a user to it, sign in as them in a private window, and confirm the
sidebar shows only what that role allows. Then, from that session's cookie:

```bash
curl -b <cookie> -o /dev/null -w '%{http_code}\n' localhost:8090/api/analytics/summary  # 200
curl -b <cookie> -X POST localhost:8090/api/rules -d '{}' -w '%{http_code}\n'           # 403
```

A 403 there is the point: the permission is enforced by the server, not by hiding a button.
Delete the test user afterwards.

For clone: open a post that has an automation, click **Clone**, select two posts that have
none, and confirm two new automations appear targeting exactly those posts while the original
is unchanged.

### Rolling back this release — read this first

Rolling back is mechanically safe. The `roles` collection and the `role_id` field are ignored
by the older code, and nothing is lost.

**But the older code has no permission model at all.** Any restricted user you created while
the new version was running — a Viewer, an Analyst — can still sign in against it and will
have full access to everything. Their role is simply not consulted.

So if you roll back after adding restricted users, **deactivate them first**, or accept that
for the duration of the rollback they are administrators.

### After upgrading

Two things worth doing once, in `docs/production-checklist.md`:

- Move people off Owner onto the narrowest role that lets them work.
- Keep **at least two Owners**. The last active Owner cannot be deactivated, deleted, or
  moved to another role — a guard that stops you stranding the deployment, and one you will
  meet at an inconvenient moment if there is only ever one. `make bootstrap-admin` remains the
  recovery path if you manage it anyway.
