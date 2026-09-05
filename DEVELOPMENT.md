# Development notes

Self-hosted Instagram comment→private-reply automation. FastAPI + MongoDB + Redis/ARQ +
React, behind Caddy, all in Docker Compose. A free alternative to CreatorFlow/ReplyRush.

Verified Meta API facts live in `docs/meta-api.md` — **that file is the only source of truth
for Meta endpoints, scopes, and restrictions.** Do not add an endpoint anywhere without
recording it there first, with the date it was verified.

## Ground rules

1. **Official Meta APIs only.** Never Selenium/Playwright/Puppeteer, private endpoints,
   cookie or session scraping, or reverse-engineered APIs. This is a compliance boundary,
   not a preference.
2. **Never guess a Meta API detail.** If `docs/meta-api.md` does not cover it, STOP and
   research the official docs, then update that file with a new "verified on" date.
3. **`DRY_RUN=true` is the default** and must never send a Meta mutation. Tests assert this.
4. **Secrets never leave the server.** No tokens in API responses, logs, or the frontend.
   Meta tokens are encrypted at rest. Never commit `.env`.
5. **MongoDB is the duplicate-protection authority** — unique indexes and atomic upserts,
   never application-level "check then write".
6. **The LLM is untrusted.** It classifies text and nothing else. No tools, no Meta access,
   no DB access, no secrets. Comment text is hostile input.

## Commands

```
make up               # docker compose up -d
make down             # docker compose down
make logs             # follow all services
make test             # pytest in the api container
make lint             # ruff check + mypy
make format           # ruff format
make build            # docker compose build
make bootstrap-admin  # create the initial admin user
make funnel           # expose Caddy publicly via Tailscale Funnel
make tunnel-status    # print the public base URL
```

Frontend: `cd frontend && npm run dev | build | test`.
Single test: `docker compose exec api pytest tests/path.py::test_name -x`.

## Architecture

```
Meta ─webhook→ Caddy → FastAPI ─enqueue→ Redis → ARQ worker
                          │                          │
                          └──────→ MongoDB ←─────────┘
                                                     ↓
                        campaign engine → policy engine → Meta client → private reply
```

The webhook endpoint only validates, persists, and enqueues. It never calls Meta.

Layout: `backend/app/{api,db,models,services,workers}`, `frontend/src`, `infra/caddy`,
`docs`, `scripts`.

## Conventions

- **All Meta HTTP lives in `services/meta/`.** Nothing else builds a Meta URL or reads a
  token. Everything else depends on the `InstagramClient` protocol, so `MockInstagramClient`
  substitutes cleanly in tests.
- **Campaign logic must not import Meta code.** Matching, priority, and rendering are pure
  functions over plain data — that is what makes them cheap to test.
- **Repositories own all DB access.** API routes and workers never touch a collection
  directly.
- **An attachment is only deleted once nothing references it.** `attachments.delete` removes
  the whole upload folder, and rules can share a `storage_key` — cloning and the Duplicate
  button both produce that. Deleting unconditionally takes the file out from under the
  survivor, and the worker only logs a warning when a send fails, so the recipient silently
  gets the text and no file. Go through `_drop_attachment_if_unused`.
- **Every `/api` route declares its permission in the decorator**, never in the handler body.
  `app/permissions.py` is the single catalogue: the API enforces from it and the admin grid
  renders from it, so the two cannot drift. `tests/test_authorization.py` walks the route
  table and fails on any route that declares nothing, with a short allowlist for the handful
  that legitimately cannot (login, `/me`, and the callbacks Meta drives).
- **Template rendering is `{{var}}` substitution against a fixed allowlist** — no Jinja, no
  `eval`, no arbitrary attribute access. Unknown variables are rejected at save time.
- **Structured JSON logs.** Never log a token, password, session secret, encryption key, or
  authorization code.
- Async throughout: `httpx.AsyncClient`, `pymongo.AsyncMongoClient` (not Motor — deprecated).

## Idempotency (three layers, all enforced by unique index)

| Layer | Key |
| --- | --- |
| Webhook | `webhook_events.event_hash` |
| Comment | `comments.(instagram_account_id, instagram_comment_id)` |
| Execution, comment-triggered | `executions.(comment_id, rule_id, action)`, partial on `comment_id: {$type: objectId}` |
| Execution, message-triggered | `executions.(trigger_message_id, rule_id, action)`, partial on `trigger_message_id: {$type: string}` |

The worker reserves an execution with an atomic insert **before** calling Meta. A duplicate
key error means another worker already has it — skip, do not send.

## Meta constraints that shape the code

- One private reply per commenter, within **7 days** of the comment.
- 750 private-reply calls/hour per IG account.
- Long-lived token lasts 60 days; refresh needs the token to be ≥24h old.
- Retry 429/5xx/timeout with backoff+jitter. Never retry other 4xx. A 401 pauses the account.
- Skip webhook events where `value.from.id` is the connected account (self-comment loop),
  or Social Flow will reply to its own replies.
- The comment id arrives as `value.comment_id` **or** `value.id` — Meta's own docs disagree.
  Accept either.

## Collections beyond the core

| Collection | Why it exists |
| --- | --- |
| `account_snapshots` | Instagram gives a follower total and no history. Growth is only knowable if we write it down daily. One row per account per day, unique index. |
| `click_events` | One row per click. `link_clicks` is a counter with a first/last timestamp — it knows how many, never when, so an hourly chart cannot come from it. |
| `app_settings` | A single document for things an operator may edit from the dashboard. Currently the MaxMind credentials; the license key is encrypted like a Meta token. |
| `meta_apps` | One Meta app per user, so a second person can connect their own Instagram account without going through the first person's app -- which is what would turn this into a third-party integration needing App Review. App secret and webhook verify token are encrypted like a Meta token. A separate collection rather than fields on the user document, because `current_user` reads the whole user document on every request. |
| `app_settings` | Also holds the public origin, which overrides `PUBLIC_BASE_URL`. Server-wide, so it and the GeoLite2 credentials sit behind `instance.*` rather than `settings.*` -- otherwise "may reword an automated reply" would also mean "may re-point the server". |
| `roles` | A named set of permissions, assigned to users via `users.role_id`. `owner` is seeded from the whole catalogue and re-synced on every boot, so a new permission reaches it with no migration; `manager` and `viewer` are seeded once and then belong to the operator. |

## Workspaces

One user, the Instagram account they connected, and the `/u/<slug>/` prefix both live under.
`users.slug` is the tenant key; `services/slugs.py` owns its shape and `URL_PREFIX`.

Every router in `main.py` is mounted **twice** -- bare, and again under `/u/{slug}`. No handler
declares `slug`: `deps.workspace_slug` reads it out of `request.path_params`, which FastAPI
populates for an undeclared path parameter. Both layouts have to work at once, because the
bare routes are what an already-live Meta app calls and what pre-workspace bookmarks point at.

`deps.current_workspace` decides whose data a request addresses. Your own by default; someone
else's costs `workspaces.view`, and failing that check raises **404, not 403** -- a 403 would
confirm the slug exists and turn the endpoint into a way to enumerate who has an account here.

`services/meta/credentials.py` decides which Meta app is acting. Its central rule: **the URL
layout follows the credentials, not the request.** A workspace with its own app gets prefixed
URLs; one falling back to the environment's `META_*` gets the unprefixed ones, because those
are what the live Meta app already has registered.

The webhook, the OAuth callback and the two `signed_request` callbacks all have to pick a
secret before they can trust anything else in the request. That is why a workspace is a URL
prefix and not a header -- see the table in `docs/meta-api.md` §7b.

## Cloning an automation

`POST /api/rules/{rule_id}/clone` copies a rule onto other posts as **one independent rule
per post** — its own stats, its own send limits, its own once-per-person ledger. The source
is untouched.

Three constraints the endpoint enforces, each for a reason worth knowing:

- Only `trigger: "comment"` with `scope: "specific"` can be cloned. `list_for_media` filters
  on trigger, so a cloned story-reply rule's media ids would never be read; and an
  account-wide rule already covers every post, so clones would merely shadow it (equal
  priority, and `select_rule` breaks the tie on the newer `_id`).
- A target that already has a rule is **skipped and reported**, not failed. Partial success
  is the normal case.
- Fan-out is capped at `MAX_CLONE_TARGETS`. Send limits are counted per rule, so N clones
  authorise N x `limits.per_hour` against Meta's 750/hour account ceiling, and
  `list_for_account` only returns 50 rules.

Build each clone's payload **inside** the loop: `BaseRepository.insert` mutates the dict it
is given and pymongo writes `_id` back into it, so a reused dict makes every insert after the
first collide.

## Known gaps

**Three triggers, one matcher.** A rule carries `trigger`: `comment` (the original and the
default), `story_reply`, or `dm_keyword`. Rules written before that field existed have no
`trigger` key, so `list_for_media` filters `{"$in": ["comment", None]}` — `$in` with `None`
matches a missing field as well as an explicit null, which is what lets them keep working
with no backfill. Without that clause every existing rule would also fire on DMs.

`select_rule` and `rule_matches` were always generic over text; only the caller was
comment-coupled.

**Story replies are account-wide, but need not stay that way.** The webhook carries
`reply_to.story.id`, and `GET /me/stories` works under Instagram Login (docs/meta-api.md §4d), so a
rule can target specific stories. It expires with them: the sync marks a story expired once
Meta stops listing it, and a rule whose every target has gone is disabled with
`disabled_reason: "story_expired"` — opt-out per rule, and never applied to an account-wide
story rule, which targets nothing that can expire.

**Expiry is observed, not computed.** A failed fetch and "everything expired" look identical
if the error is swallowed, so a failed `get_stories` expires nothing.

**Story *reactions* are still not a trigger.** Meta does not document how a bare emoji tap on
a story is delivered, and the one reaction this account has received was a reaction to a
*message*, carried only `mid` + emoji, and had **no sender** — nobody to reply to. The
endpoint logs the shape of any unhandled messaging event so a real one can be inspected
rather than guessed at.

**Still comment-only:** `opening_dm` and `public_reply` (both structurally need a comment id),
`any_word` on `dm_keyword` (it would answer every real question anyone sends), AI intent
matching (`ai_classifications` is keyed on `comment_id`), and `awaiting_follow` parking
(`recheck_follow` loads a comment row). All are refused at the API with an explanation.

**Click geography needs a trusted proxy.** Caddy v2 replaces an inbound `X-Forwarded-For`
unless the sender is declared in `trusted_proxies`, so behind Tailscale Funnel the visitor's
address was being swapped for the Docker gateway and `client_ip()` correctly refused it as
private. Every click was stored with no location and the map was empty with no error anywhere.
The two states — "nobody clicked" and "nothing could be placed" — are told apart by `located`
vs `total_clicks` on `/api/analytics/places`; the UI now prints a different sentence for each.

**Locations cannot be added later.** A `click_events` row keeps only the derived place, never
the address, so an unplaced click is unplaced forever. That is deliberate, and it means
`click_events` has no history before the day it shipped and no backfill is possible.

**The first admin can be created from the browser, once.** `make bootstrap-admin` needs a
shell, which a managed container host may not give you, so `POST /api/auth/setup` exists. It is
an account-creation endpoint open to the internet, so it is fenced four ways and each is tested
separately:

1. refused once **any** user exists — not "any admin", any user
2. refused unless `ADMIN_SETUP_SECRET` is set and at least 16 characters; an unset secret
   disables the route rather than meaning "no secret required", and the route answers 404 so it
   does not advertise itself
3. constant-time comparison, throttled per client on the same limiter as login
4. a partial unique index on `is_bootstrap` — two requests can pass check 1 together with
   different emails, and the unique email index would not stop them

The `/verify` step is UX only. It lets the operator learn the code is wrong before choosing a
password, and grants nothing: creation re-runs every check.

**Follower history is only what we recorded.** Instagram exposes a follower total and no
history, so growth begins the day the account was connected, and gross new-vs-unfollowed is not
derivable from a total at all — only net change is.

**Click history begins with `click_events`.** Clicks recorded before that collection existed
are a counter and a last-clicked timestamp, so they cannot be placed on a timeline or a map.

**A CSS specificity trap that has now bitten four times.** Several base rules in
`index.css` target `button` plus a class, which is (0,1,1) or (0,2,1). A later rule written as
`.some-class { ... }` is only (0,1,0) and silently loses, so the change appears not to apply:

| Symptom | Losing selector | What beat it |
| --- | --- | --- |
| Tabs filled dark on hover | `.tab:hover` (0,2,0) | `button:hover:not(:disabled)` (0,2,1) |
| Template cards went dark, text unreadable | `.template-card:hover` (0,2,0) | same |
| Stats rail and keyword chips, same shape | `.stats-rail:hover` (0,2,0) | same |
| "View automation" would not pin to the card bottom | `.ig-body > button` (0,1,1) | `.ig-body button.secondary` (0,2,1) |

Write the element into the selector — `button.some-class` — rather than relying on source
order. Source order only decides a tie, and these are not ties.

## Environment

macOS + Docker Compose. Public HTTPS via Tailscale Funnel →
`https://your-machine.your-tailnet.ts.net`. Tailscale terminates TLS, so **Caddy serves
plain HTTP on :8080 inside the container**, published to `127.0.0.1:$HOST_PORT`
(default 8090) on the host — do not add TLS directives to the Caddyfile.

`AI_ENABLED=false` by default. When enabled it speaks OpenAI-compatible `/v1` (Ollama
locally, vLLM on a GPU box) and only ever runs after the keyword matcher finds no match.
