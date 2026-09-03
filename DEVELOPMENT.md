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
| Execution | `executions.(comment_id, campaign_id, action)` |

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

## Known gaps

**The rule engine is comment-driven.** A rule matches keywords in a comment on a specific post,
and nothing else. Two trigger types are therefore not available, and the templates gallery
deliberately omits them rather than shipping automations that would silently never fire:

| Wanted | Blocked by |
| --- | --- |
| Send link on **story reaction** | No story-reaction trigger. The `messages` webhook carries story replies, but `select_rule` only ever runs against a `CommentContext`. |
| **Auto-reply to DMs** on a keyword | `process_message_event` only resumes an execution parked at `awaiting_email`; there is no general inbound-DM matcher. Deliberate — the app is not a chatbot. |
| Thank **story reactors** | Same as the first row. |

Adding either means a trigger discriminator on the rule model and a second matcher path. Until
then, a template promising them would create a rule that never fires, which is worse than not
offering it.

**Follower history is only what we recorded.** Instagram exposes a follower total and no
history, so growth begins the day the account was connected, and gross new-vs-unfollowed is not
derivable from a total at all — only net change is.

**Click history begins with `click_events`.** Clicks recorded before that collection existed
are a counter and a last-clicked timestamp, so they cannot be placed on a timeline or a map.

**A CSS gotcha that has bitten three times.** The global `button:hover:not(:disabled)` rule is
specificity (0,2,1). Any `.some-class:hover` styling a `<button>` is (0,2,0) and loses, so the
element turns dark on hover. Write `button.some-class:hover:not(:disabled)` instead. The same
arithmetic applies to `.tab.active` and friends.

## Environment

macOS + Docker Compose. Public HTTPS via Tailscale Funnel →
`https://your-machine.your-tailnet.ts.net`. Tailscale terminates TLS, so **Caddy serves
plain HTTP on :8080 inside the container**, published to `127.0.0.1:$HOST_PORT`
(default 8090) on the host — do not add TLS directives to the Caddyfile.

`AI_ENABLED=false` by default. When enabled it speaks OpenAI-compatible `/v1` (Ollama
locally, vLLM on a GPU box) and only ever runs after the keyword matcher finds no match.
