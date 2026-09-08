# Upgrading a running deployment

For a server that is already running Social Flow and needs the newest version.

Standing a new server up instead is `docs/deployment.md`; moving an existing one to different
hardware is `docs/server-migration.md`.

---

## The two things that catch people

Both fail the same way: nothing errors, `docker compose ps` says healthy, and the server runs
the old thing.

### 1. `make reload` without `make build` deploys nothing

`make reload` is `docker compose up -d`. After a `git pull` **the images already exist**, so
`up -d` on its own recreates containers from the *old* images.

The frontend makes it worse: the dashboard is compiled into its image at build time
(`frontend/Dockerfile` runs `npm run build`, then copies `dist/` into nginx). An unrebuilt
frontend serves the old bundle, so any new page 404s while the API happily answers.

So the sequence is always:

```bash
git pull
make build      # <- the step that is easy to skip
make reload
```

### 2. Caddy will not pick up a Caddyfile change on its own

Caddy reads `infra/caddy/Caddyfile` into memory once, at startup. The file is bind-mounted, so
editing it changes nothing Compose can see: `up -d` finds the container spec unchanged, leaves
Caddy running, and the edge keeps routing by the old rules.

`make reload` now force-recreates `caddy` for exactly this reason, so on a current checkout
this is handled. It is written down because the symptom points nowhere near the cause. When
the workspaces release added `/u/<slug>/` routes and Caddy never loaded them, every API call
under a workspace prefix fell through to the SPA — so signing in POSTed to nginx and came back:

```
405 Not Allowed
```

An nginx error page, from a login request, on a deployment whose API and database were both
fine. If you ever see a `405 Not Allowed` or a raw `<!doctype html>` where JSON belongs:

```bash
docker compose up -d --force-recreate caddy
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
data: **the newest below deletes rows from `media`**, the contacts one rewrote every row in
`contacts`, and the workspaces one rewrote every row in `users`. A dump you have never restored
is a hypothesis, not a backup.

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

**A backfill is not always additive.** Most only fill in a missing field, but one now *deletes*
rows -- orphaned `media`, see the newest release notes below -- and it runs automatically, with
no prompt and no dry run. Rolling the image back does not undo it. That is the specific reason
Step 2 is not ceremony: read the release notes for the version you are moving to and find out
what its migration writes before you start it.

## Step 5 — Verify

**Before signing in**, prove the edge is routing the current rules. Anything that returns HTML
where JSON belongs is Caddy on a stale config — see trap 2 above:

```bash
curl -s localhost:8090/u/anything/api/auth/me | head -c 40
# {"error":{"code":"authentication_error"    <- good, the API answered
# <!doctype html>                            <- stale Caddy; force-recreate it
```

Then the rest:

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

## Orphaned posts, and a full resync

**This is the first release whose migration DELETES rows.** Everything else in it is
additive. Read this section before pulling.

### What it deletes, and why

A disconnect used to hard-delete the account row and leave its posts and automations behind,
keyed on an `_id` nothing pointed at any more. Because `accounts.upsert` is keyed on
`instagram_user_id`, reconnecting the *same* Instagram account then minted a fresh `_id` and
stranded everything permanently -- there is no `instagram_user_id` on a `media` row, so nothing
can remap them afterwards. Four connect/disconnect cycles during setup were enough to leave 168
orphaned rows on the development machine.

Disconnect now marks the account `disconnected` and keeps the row, which is what Meta's own
deauthorize callback always did. Nothing can orphan media any more. The rows already orphaned
are removed once, by a backfill in `prepare()`.

**That deletion is irreversible except from a backup.** Rolling the image back does not bring
the rows back, and a sync cannot re-fetch them: they belonged to accounts that no longer exist,
so there is no token and no Instagram account to fetch them from.

Orphaned *automations* are left alone and counted in the log. They hold message text somebody
wrote, they are inert because the worker only iterates accounts that exist, and deleting
authored content during an upgrade is not a default worth having.

### Predict the deletion before you pull

```bash
# MONGO_DATABASE is set for the containers, not for your login shell -- a bare
# "$MONGO_DATABASE" expands to empty, mongosh silently connects to `test`, and
# the whole check reports a reassuring row of zeroes.
DB=$(grep -E '^MONGO_DATABASE=' .env | cut -d= -f2- | tr -d '"'"'"' '); DB=${DB:-instaflow}
echo "querying database: $DB"

docker compose exec mongodb mongosh --quiet "$DB" --eval '
var live = db.instagram_accounts.find({},{_id:1}).toArray().map(a=>String(a._id));
var all  = db.media.find({},{instagram_account_id:1}).toArray();
var orph = all.filter(m => live.indexOf(String(m.instagram_account_id)) === -1);
print("media rows total : " + all.length);
print("WILL BE DELETED  : " + orph.length);
db.instagram_accounts.find({},{username:1,status:1}).forEach(a =>
  print("  @" + a.username + "  status=" + a.status));'
```

`WILL BE DELETED: 0` means the backfill is a no-op for you. A non-zero number is what the
backup in Step 2 is for.

**One finding blocks the upgrade.** If your only account shows `status=disconnected`, stop.
"This workspace's account" now means "not disconnected", so after upgrading the app would
report no account connected. `token_error`, or a missing status, is still treated as live --
the filter is `$ne: "disconnected"`, which matches an absent field too.

### Then

```bash
git pull
make build      # mandatory: aiosmtplib is new, so up -d alone reuses the old image
make reload
docker compose logs api --since 5m | grep orphaned_media_dropped
```

Expect `rows: N` matching the prediction, or no line at all if it was zero.

### What else changed

Media fetching now paginates, following `paging.cursors.after` -- see `docs/meta-api.md` §2c,
where every fact is recorded with the date it was verified against a live account. Routine
syncs are unaffected and still request **one page**: the 15-minute cron and
`POST /api/posts/sync`, which is what both the Dashboard's "Sync from Instagram" and the
Posts & Reels "Refresh" call. Only the new **Settings -> Resync all posts** walks the whole
edge, and it is the single best end-to-end check after an upgrade -- it exercises the stored
token, the encryption key protecting it, outbound Meta access and the new job registration in
one press.

### Verify

```bash
docker compose exec mongodb mongosh --quiet "$DB" --eval '
print("media rows now: " + db.media.countDocuments({}));
db.media.aggregate([{$group:{_id:"$instagram_account_id",n:{$sum:1}}}]).forEach(r=>
  print("  account " + r._id + ": " + r.n));'
```

Every row should now belong to an account that exists. Then in the dashboard: sign in, press
Refresh on Posts & Reels, and press **Resync all posts** in Settings -- it should report a count
and a finish time. The guided-setup banner should stay absent; if it appears, the account is
being excluded and its `status` is the thing to look at.

Do not disconnect the account just to see the new behaviour. It is recoverable now, but
reconnecting costs a full OAuth round trip against Meta.

### Rolling back

Mostly safe, with two exceptions worth reading before you do it.

**The deleted media rows do not come back.** Rolling back restores the code and nothing else.
They cannot be re-fetched either: they belonged to accounts that no longer exist, so there is
no token to fetch them with. `make restore ARCHIVE=...` is the only recovery.

**`status: "disconnected"` rows written by the new disconnect button persist**, and the older
code does not filter on status -- so a disconnected account reads as live again and the
Automations and Analytics pages will present it as the workspace's active one. No message goes
out (the policy engine independently requires `connected`), but the UI will be misleading until
you reconnect or remove the row.

And the standing warning from the roles release compounds now: rolling back past it turns every
person you invited -- each holding `workspace-owner` -- into a full administrator of the whole
install, because the older code has no permission model. Deactivate invited users first.

## Invite-only signup

Adds invitations, a guided Meta-app setup, and a fourth seeded role. Additive: nothing
existing changes behaviour.

```bash
git pull
make build
make reload          # required: the Caddyfile changed
```

**`make reload`, not `docker compose up -d`.** The Caddyfile gained one line -- an invitation
token is redacted from the access log the same way the webhook verify token already is -- and
Caddy reads that file into memory at startup, so `up -d` sees an unchanged container spec and
silently keeps the old logging.

On first boot: an `invites` collection with four indexes, and a `workspace-owner` role seeded
alongside the existing three. **No existing user is repointed at it**, and any Manager or
Viewer role you have edited is untouched -- both are `locked=False`, so the seeder only ever
`$setOnInsert`s them.

**Correction.** This section previously claimed `ALL` was unchanged in size and that the Owner
re-sync stayed a no-op. That was wrong. No permission *module* was added, but an *action* was
removed: `settings.edit` left the catalogue when the disclosure moved to `instance.*`, so `ALL`
went from 19 to 18 and Owner -- which is `locked=True` and rebuilt from code on every boot --
does have its stored `permissions` array rewritten. Harmless for Owner itself, but it has a
consequence that is not:

### A hand-made role that still stores `settings.edit` becomes unassignable

Seeded roles are fine: Manager and Viewer never held it. Only a role you created or hand-edited
yourself can, and after the upgrade that role is rejected in three places, none of which name
the real cause:

- **Editing it returns 422** `unknown permissions: settings.edit`. The role editor submits the
  whole stored array, and the grid cannot show a checkbox for a permission that no longer
  exists -- so the offending value is invisible on screen while still being sent.
- **Duplicating it returns 422**, for the same reason.
- **Assigning it to anyone returns 403** "You cannot grant access you do not have yourself."
  `role_you_may_grant` requires the role's permissions to be a subset of yours, and after the
  re-sync even an Owner's 18 do not contain `settings.edit`. That blocks creating a user with
  that role, changing someone onto it, and inviting anyone into it.

People **already** on such a role keep working. Find them before upgrading:

```bash
docker compose exec -T mongodb mongosh --quiet "$DB" --eval '
  db.roles.find({permissions:"settings.edit"},{slug:1,name:1,is_system:1}).forEach(r =>
    print("  " + r.slug + "  is_system=" + r.is_system +
          "  users=" + db.users.countDocuments({role_id:r._id})));'
```

Anything listed needs one line to repair, either before or after the upgrade:

```bash
docker compose exec -T mongodb mongosh --quiet "$DB" --eval '
  db.roles.updateMany({permissions:"settings.edit"}, {$pull:{permissions:"settings.edit"}})'
```

If those holders genuinely need the disclosure or Click geography, grant them
**Server settings -> Manage** (`instance.manage`) on the Roles page instead. Note that is
Owner-level: it also carries the server's public address.

Verify:

```bash
# The role, with exactly 11 permissions and locked
docker compose exec mongodb mongosh --quiet instaflow --eval \
  'db.roles.find({slug:"workspace-owner"},{permissions:1,is_system:1}).toArray()'

# The pending-invite index must be partial on the VALUE, or an address can
# never be re-invited after a revoke
docker compose exec mongodb mongosh --quiet instaflow --eval \
  'db.invites.getIndexes().filter(i => i.name == "uq_invite_pending_email")'

# The token is redacted in the edge log
curl -s "http://127.0.0.1:8090/invite?token=probe" > /dev/null
docker compose logs caddy --since 1m | grep -o '"uri":"/invite[^"]*"'
# Expect: "uri":"/invite?token=REDACTED"
```

Then send yourself one from **Invites**. Sending needs *Settings → Email*; without it the
invitation is still created and the page offers **Copy link**, which is also the way to
exercise the flow before mail is configured.

Two things worth knowing before inviting anybody real. An invited person gets **their own
workspace and their own Meta app** -- they are not joining yours, and they see nothing of it;
that separation is what keeps this install out of Meta App Review. And revoking an invitation
does nothing to an account already created from it: deactivate the user instead.

## Outbound email

Adds the ability to send mail, which nothing in the product did before. Groundwork for
invitations; on its own it gives you a "Send test email" button and an audit of what was sent.

**This release adds a dependency**, so a plain `docker compose up -d` is not enough:

```bash
git pull
make build          # aiosmtplib is new -- the pip layer must be rebuilt
make reload
```

A new `email_log` collection and its three indexes are created by `prepare()` on first boot;
there is no backfill and nothing to migrate. Nothing is required in `.env` — the SMTP host,
credentials and sender live in the database and are filled in on *Settings → Email* by an
Owner. A deployment that never configures mail behaves exactly as before and still reports
healthy: `missing_required()` deliberately does not mention SMTP.

Verify:

```bash
# The collection and its unique index exist
docker compose exec mongodb mongosh --quiet instaflow \
  --eval 'db.email_log.getIndexes().map(i => i.name)'
# Expect: [ '_id_', 'ix_created', 'ix_recipient_created', 'uq_message_id' ]

# The worker registered the job
docker compose logs worker | grep worker_started
```

Then configure Gmail on the Settings page and click **Send test email**. See
`docs/deployment.md` § Email for the App Password steps, and `docs/troubleshooting.md` for
the six things that look identical when a message does not arrive.

**Note `DRY_RUN` does not suppress email.** It means "no Meta mutation", and a message to a
person is not one — so mail is sent even on an install that is otherwise in dry run. Set
`EMAIL_CLIENT=mock` if you want the pipeline to run without anything leaving the server.



## Contacts grouped by post, and CSV export per post

### What is new

- **Contacts are grouped by the post that earned them.** Each group is a collapsible card
  showing the post's thumbnail and caption with a contact count, ordered newest post first.
  Story replies, DM-keyword automations and anything untraceable sit in a final
  **Not from a post** group rather than being hidden.
- **A CSV per post**, from inside each group, plus **Download all (CSV)** on the page. The
  aggregated file carries `post_caption`, `post_media_id` and `automation` alongside
  `email`, `instagram_user_id` and `captured_at`, so a joined export still says where each
  lead came from.
- **A person captured on two posts now belongs to both.** A contact is still one row per
  person, but it accumulates a `sources` entry per (post, automation) that captured them.
  Recording only the most recent would mean a post's CSV shrank between downloads because a
  later post captured the same person — the kind of thing nobody notices and nobody can
  explain afterwards.

### What is fixed

- **The CSV silently truncated at 50 rows.** It was built in the browser from the array the
  page had fetched, and `list_for_account` inherits `BaseRepository.list`, whose default
  `limit` is 50 (clamped to 200). An operator with 300 leads downloaded 50 and was told
  nothing. Both files are now generated server-side from an uncapped read, so an export is
  never bounded by what the page happened to show.
- **`serialise` did not recurse into a list of dicts.** It stringified the ObjectIds sitting
  directly in a list but passed a list of dicts through untouched. `sources` is exactly that
  shape, so the first route returning one would have raised in FastAPI's encoder — a 500,
  not a test failure.
- **`find_awaiting_email` had no index.** It matches
  `(instagram_account_id, instagram_user_id, status)` on **every inbound DM** and was
  scanning the largest collection in the system. The new backfill needs the same key, so one
  index serves both.
- **Spreadsheet formula injection.** Captions come from Instagram and automation names from
  an operator; a cell opening `=`, `+`, `-` or `@` is executed by Excel and Sheets. Every
  cell is now prefixed with an apostrophe when it starts with one of those.

### What this release does to your database

On the first start of the new API:

1. Adds `ix_account_source_media` to `contacts` — **non-unique**, and it must stay that way.
   The index is created before the backfill runs, so at that moment every existing contact
   has no `sources` field and indexes as null. Under a non-unique index that is a harmless
   no-op; a unique one would collide across all of them and abort startup on exactly the
   deployments that need backfilling. This is the same trap as `uq_user_slug` in the release
   below, solved by not needing uniqueness rather than by a partial filter.
2. Adds `ix_account_user_created` to `executions`.
3. **Rewrites every row in `contacts` that has no `sources` field**, reconstructing which
   post each lead came from: contact, to its executions, to their comments, to the media id.

No new collection, no permission changes — `contacts.view` still gates the page and now also
the export, so no role needs re-syncing and no custom role loses anything.

**The reconstruction is an inference, and the data says so.** Nothing on an execution records
that it produced the email; by the time it finishes its status is `success` like any other.
So every backfilled entry is stamped `backfilled: true` and the dashboard marks it
**Inferred**. Only automations with the email gate switched on are attributed — otherwise
someone who gave their address once would appear under every post they ever commented on,
presented as fact. Anything unresolvable gets a single "not from a post" entry rather than an
empty list, because the watermark is `sources` not existing and an empty list would re-run the
job on every startup forever.

It is logged only when it does something:

```bash
docker compose logs api --tail 80 | grep contact_sources_backfilled
# contact_sources_backfilled  {"contacts": 1}
```

**Nobody is signed out**, **no `.env` change is needed** — no new environment variables and
no new dependencies — and **nothing changes in the Meta dashboard**: no new webhook URL, no
new redirect URI, no verify-token change.

### Deploy the API and the dashboard together

The general rule further up — that an old frontend against a new API is safe because it
ignores fields it does not know about — **does not hold for this release.**
`GET /api/analytics/contacts` returns an array of *groups* where it used to return an array of
*contacts*. An old dashboard against the new API renders one blank row per group on Contacts:
no crash, no error, just wrong.

`make build && make reload` does both together, which is what you want. It only matters if you
deploy services one at a time.

### Verifying this release

The migration first. Expect zero contacts without sources, and a `sources` array on each:

```bash
docker compose exec -T mongodb mongosh instaflow --quiet --eval '
  print("contacts: " + db.contacts.countDocuments({}));
  print("without sources: " + db.contacts.countDocuments({sources: {$exists: false}}));
  db.contacts.find({}, {email: 1, _id: 0, "sources.instagram_media_id": 1,
                        "sources.rule_name": 1, "sources.backfilled": 1})
             .forEach(c => print(JSON.stringify(c)));'
```

Then prove it is idempotent, which is the check actually worth running — a backfill that
re-runs every boot is silent until the collection is large:

```bash
docker compose restart api && sleep 10
docker compose logs api --since 30s | grep -c contact_sources_backfilled
# 0   <- correct: it found nothing to do the second time
```

Both indexes:

```bash
docker compose exec -T mongodb mongosh instaflow --quiet --eval '
  print(db.contacts.getIndexes().map(i => i.name).join(", "));
  print(db.executions.getIndexes().map(i => i.name).join(", "));'
# ... ix_account_source_media
# ... ix_account_user_created
```

The export is gated, not merely hidden behind a button:

```bash
curl -s -o /dev/null -w '%{http_code}
' localhost:8090/api/analytics/contacts.csv   # 401
```

In the dashboard: Contacts shows one card per post, the first open, each with its own
**Download CSV**, and pre-upgrade leads carry the **Inferred** pill. Open both files in a
spreadsheet and check the caption, media id and automation columns are populated, that a
caption with a comma or a quote stays in one field, and that a caption beginning `=` is
displayed rather than evaluated.

Then confirm new captures record their post for real, rather than only being reconstructed:
comment on a post whose automation has the email gate on, reply with an address, and look for
a `sources` entry with `backfilled: false`.

### Rolling back this release — read this first

Mechanically safe, and no data is lost. Older code ignores `sources` entirely, and its
`upsert` writes the same top-level fields it always did, so an existing contact keeps its
array untouched. The two new indexes linger and are harmless.

One nicety worth knowing: a contact captured *while* rolled back gets no `sources` field, so
it is picked up automatically when you roll forward — that missing field is precisely the
watermark the backfill looks for.

The CSV reverts to the browser-built one, and with it the silent 50-row truncation.

---

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
