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
- **Deferred delivery** — a gated non-follower who follows later gets the link automatically,
  inside the 24-hour messaging window their button tap opened
- **Reply delay** — wait up to 59:59 before answering, so it does not read as instant automation
- **File attachments** — send an image, video, audio clip or PDF with the payoff DM
- **Live DM preview** while you build the automation, including the attachment bubble
- **Superfans** — people who comment 2+ times a week for consecutive weeks
- **Click tracking** with honest click-through numbers, per link destination
- **Analytics** — highlights, engagement trends, activity log, and follower growth recorded daily
- **Click geography** (optional) — country and city from a local GeoLite2 database; the IP is
  used for the lookup and never stored
- **18 starter templates** across five categories, pre-filled and ready to customise
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

## Important: put the app in Live mode

**Read this before you start.**

In **Development Mode**, Meta withholds comment content from anyone who does not hold a role on
your app — through webhooks *and* the read API, even for accounts you add as Instagram Testers.
`comments_count` goes up and the comment list comes back empty. It looks exactly like a broken
webhook, and it is not.

Switching the app to **Live** fixes it. Comments from ordinary followers are then delivered in
full. **No App Review and no Business Verification are required** to automate an account you own.

Live mode requires *App Settings → Basic* to be complete: Privacy Policy URL, Terms of Service
URL, App Icon, and Data Deletion Instructions URL.

An earlier version of this project concluded that Advanced Access was required. That was wrong —
Development Mode was a confound held constant across every experiment. The full investigation,
the correction, and a Development-vs-Live comparison are in
[`docs/meta-api.md`](docs/meta-api.md) §7.

Advanced Access is only needed to serve *other people's* Instagram accounts, i.e. to run this as
a service for clients.

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

550 tests covering keyword matching, template rendering, policy decisions, webhook signature
validation, idempotency under concurrency, token encryption, retry semantics, and the AI
classifier's prompt-injection resistance, the follow gate, deferred delivery after a follow,
attachment storage and path traversal, the reply delay, and who counts as a superfan. Meta is
never contacted in tests.

```bash
make test    # backend suite
make lint    # ruff + mypy
```

## Status

**Working end to end against real traffic.** Comments from accounts with no role on the app are
delivered, matched, and answered; the follow gate holds; links are tracked.

Known gap: story reactions and inbound-DM keywords are not triggers yet — the rule engine is
comment-driven. See [`DEVELOPMENT.md`](DEVELOPMENT.md).

## License

MIT
