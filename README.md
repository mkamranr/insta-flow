# Social Flow

**Self-hosted Instagram comment-to-DM automation.** Someone comments a keyword on your post,
they get a direct message with your link. Runs entirely on your own hardware, using Meta's
official Instagram Platform API.

A free, self-hosted alternative to tools like **CreatorFlow** and **ReplyRush**.

---

## The problem

Creators use "comment LINK and I'll DM you" as a growth and conversion mechanic. Done by hand
it does not scale: every comment needs reading, matching, and answering within Instagram's
seven-day reply window, and duplicates are easy to send by accident.

The tools that automate it are SaaS products with recurring subscriptions and monthly DM
quotas, and they hold your audience's data on their servers.

## What this does instead

The same mechanic, on infrastructure you control, with no subscription and no quota beyond
Meta's own rate limits.

| | CreatorFlow / ReplyRush | Social Flow |
| --- | --- | --- |
| Cost | Monthly subscription | Free |
| DM limits | Plan quota (e.g. 500/mo) | Meta's own ceiling only |
| Features behind a paywall | Follow gate, email capture, public replies, analytics | None — all included |
| Where your data lives | Their servers | Your machine |
| Source | Closed | Open |

## Features

- **Per-post rules** — on this post, if someone comments this keyword, send this message
- Several keywords per rule, matched **exactly**, as a **whole word**, or **anywhere** in the comment
- **Two-step DMs** — an opening message with a button, then the payoff with link buttons
- **Public auto-reply** under the comment, to boost engagement
- **Follow gate** — require the commenter to follow first, using Meta's official follow check
- **Email capture** — ask for an address in the DM before sending the link, exportable as CSV
- **Click tracking** with honest click-through numbers
- "Only send once per person", per-rule rate limits, and a full activity log
- **Optional local AI** — classify comments by intent with Ollama or vLLM when no keyword matches
- **Dry-run mode** that exercises the entire pipeline without sending anything

## How it works

```
Instagram comment
      ↓
Meta webhook ──HTTPS──▶ Caddy ──▶ FastAPI
                                     │ validate signature, dedupe, persist, enqueue
                                     ▼
                                   Redis ──▶ worker
                                                │
                        rule engine → policy engine → Meta client
                                                ▼
                                        Instagram DM
```

MongoDB is authoritative for all state; Redis is a queue only. The webhook endpoint never
calls Meta — it returns in milliseconds and hands the work to the worker.

Three unique indexes provide duplicate protection, so a redelivered webhook, a replayed job,
or two workers racing can never produce two DMs. The database is the authority, not
application logic.

## Requirements

- Docker and Docker Compose
- An Instagram **Professional** account (Business or Creator) — free
- A Meta developer account — free
- A public HTTPS URL for webhooks (Tailscale Funnel works and is free)

## Quick start

```bash
cp .env.example .env
make secrets          # generates the values to paste into .env
make up               # start all six services
make bootstrap-admin  # create your login
open http://localhost:8090
```

Then follow **Meta App Setup** in [`docs/meta-api.md`](docs/meta-api.md) to create your Meta
app, and connect your account from the Instagram page.

`DRY_RUN=true` is the default. The whole pipeline runs and records executions, but no message
is ever sent, until you deliberately turn it off.

## Important: Meta requires Advanced Access

**Read this before investing time.**

Meta does not deliver comment data — by webhook or by API — for comments authored by people
who do not hold a role on your app. This is a platform restriction, not a limitation of this
software, and it applies even to accounts you add as Instagram Testers.

Serving comments from your real audience therefore requires **Advanced Access** to
`instagram_business_manage_comments`, which requires App Review, which requires business
verification.

The investigation behind that conclusion — every hypothesis tested and how each was ruled out
— is recorded in [`docs/meta-api.md`](docs/meta-api.md) §7. It is worth reading first if you
are considering deploying this.

Everything else works and is fully tested. The software is ready the day that access is
granted.

## Documentation

| File | Contents |
| --- | --- |
| [`docs/meta-api.md`](docs/meta-api.md) | Verified Meta API reference and app setup, with dates |
| [`docs/architecture.md`](docs/architecture.md) | Components, data flow, idempotency, the AI layer |
| [`docs/deployment.md`](docs/deployment.md) | Public URL options, container runtimes, production notes |
| [`docs/publishing.md`](docs/publishing.md) | Getting the Meta app published and reviewed |
| [`docs/security.md`](docs/security.md) | Threat model, controls, and an honest list of known gaps |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Ordered by how often things actually go wrong |
| [`docs/production-checklist.md`](docs/production-checklist.md) | Gates to clear before real sending |
| [`DEVELOPMENT.md`](DEVELOPMENT.md) | Conventions and ground rules for working on the code |

## Compliance

Social Flow uses **only** Meta's official, documented APIs. No browser automation, no private
endpoints, no cookie or session scraping, and no attempt to work around Meta's restrictions.
The private-reply limits — one reply per commenter, within seven days — are enforced in code
rather than worked around.

## Tests

449 tests covering keyword matching, template rendering, policy decisions, webhook signature
validation, idempotency under concurrency, token encryption, retry semantics, and the AI
classifier's prompt-injection resistance. Meta is never contacted in tests.

```bash
make test    # backend suite
make lint    # ruff + mypy
```

## Status

Feature-complete and tested. Parked pending Meta Advanced Access — see the section above.

## License

MIT
