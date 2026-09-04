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
