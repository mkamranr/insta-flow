# Security

Reviewed 2026-08-31 against the checklist in project-plan.md §50 and §89.

## Threat model

Social Flow holds one thing worth stealing: a long-lived Meta access token that
can send DMs as the connected Instagram account. Everything below is arranged
around not losing it, and around not sending messages the operator did not
intend.

The realistic attackers are: someone on the internet who finds the public
webhook and OAuth endpoints; someone who obtains the dashboard URL; and a
malicious commenter whose text flows through the whole pipeline.

## What is exposed to the internet

Only Caddy's port, and only these paths:

| Path | Auth | Why public |
| --- | --- | --- |
| `/webhooks/instagram` | HMAC signature | Meta must reach it |
| `/api/auth/instagram/callback` | Signed single-use state | Meta redirects the browser here |
| `/api/auth/instagram/deauthorize`, `/data-deletion` | None | Registered with Meta; a 404 shows as a broken integration |
| `/r/{token}` | None | The clicker is an Instagram user, not an operator |
| `/health`, `/ready` | None | Liveness probes |
| `/`, static assets | None | The dashboard shell; all data behind it needs a session |

Every `/api/*` route other than the OAuth callbacks requires a session.

MongoDB, Redis, the API, and the frontend use `expose:` rather than `ports:`, so
they are reachable only on the compose network. Caddy binds `127.0.0.1` only.

## Controls in place

**Passwords** — Argon2id via argon2-cffi. Never stored, logged, or returned. A
corrupt stored hash returns False rather than raising.

**Login** — one identical 401 for wrong password, unknown email, and inactive
user, verified against a real Argon2 hash of a random string so response time
does not reveal whether an account exists. Throttled per email+IP: Argon2 is
deliberately expensive, so an unthrottled login is a denial of service as well
as a credential-stuffing risk. Throttling fails open if Redis is down — locking
the operator out of their own dashboard is worse than the risk it prevents.

**Sessions** — signed, timed cookie (itsdangerous), `HttpOnly`, `SameSite=Lax`,
`Secure` in production. Carries only a user id and a session epoch. Logout
increments the epoch, revoking every cookie already issued rather than only the
one in the current browser.

**Authorization** — every route under `/api` declares the permission it needs in
its decorator (`dependencies=[Depends(require("automations.edit"))]`), and a test
walks the route table and fails if one does not. The check is in the decorator
rather than the handler body precisely because a body check is opt-in and
therefore forgettable — three handlers in `rules.py` once loaded a record with no
ownership check at all for exactly that reason.

Permissions resolve from the database on every request. The session cookie
carries no claims, so revoking a permission takes effect on the person's next
request rather than at their next login.

Two escalation paths are closed explicitly: you cannot grant a permission you do
not hold yourself, and you cannot administer a user whose permissions are not a
subset of your own. Without the second, a role carrying `users.manage` but not
`roles.manage` could reset the owner's password and take the deployment.

The frontend hides what you cannot use, but that is presentation only — the
permission list reaches the browser as ordinary JSON. The server is the authority.

**CSRF** — `SameSite=Lax` blocks cross-site form posts. All state-changing routes
take JSON, which forces a CORS preflight, and no CORS middleware is installed,
so no cross-origin app can call them. There is no separate CSRF token because
there is no cross-origin surface for one to protect.

**Meta tokens** — AES-256-GCM at rest, key from `ENCRYPTION_KEY`, never in
MongoDB. The tag makes tampering detectable, so a modified ciphertext fails to
decrypt rather than yielding a corrupted token that would be sent to Meta.
Ciphertext is versioned (`v1:`) so the scheme can be rotated.
`InstagramAccountPublic` has no token field by construction, so a token cannot
leak by someone forgetting to strip one — asserted by tests against a real
connected account.

**Logs** — a redaction filter runs on every record, covering tokens, passwords,
secrets, OAuth codes, and bearer headers, including nested structures. Verified
against the live token after a real OAuth connection.

**Webhooks** — HMAC-SHA256 over the raw request body, compared with
`hmac.compare_digest`, before any parsing. A signed-but-unparseable body is
acknowledged with 200 rather than an error, because a 4xx/5xx makes Meta
redeliver a payload that can never succeed.

**OAuth state** — signed *and* single-use. The signature stops an attacker
minting a state; only burning a Redis nonce stops the same callback URL being
resubmitted. Both are tested.

**Open redirect** — `/r/{token}` re-checks the target is `http(s)` on the way
out, even though the URL was validated on the way in. Storage is not a trust
boundary, and an open redirect would turn the deployment's own domain into an
attack vector.

**Template injection** — messages are `{{name}}` substitution against a fixed
allowlist. Not Jinja, not `eval`. Substituted values are never rescanned, so a
comment containing `{{link}}` cannot inject a variable. Unknown names are
rejected at save time, so a bad message never reaches the worker.

**Untrusted input** — comment text is treated as data everywhere: matched with
literal string operations (`re.escape` where regex is used), stored, and
interpolated only into the allowlisted `{{comment}}` slot. It never reaches a
shell, a query construction, or a template engine.

**Duplicate sends** — three unique indexes, and the worker reserves an execution
before any Meta call. The database, not application logic, is the authority.

**Headers** — CSP, HSTS, `X-Frame-Options: DENY`, `X-Content-Type-Options`,
`Referrer-Policy`, and the `Server` header removed. The dashboard loads no
third-party code; `img-src https:` exists only so Instagram thumbnails render.

**Audit log** — every administrative mutation records actor, action, entity, and
IP.

## Known gaps

Honest list. None are exploitable in the current single-operator deployment, but
they matter before this serves anyone else.

1. **MongoDB has no authentication.** It is unreachable outside the compose
   network, so this is defence in depth rather than an open door. Enabling it is
   documented in `docs/deployment.md`; do it before running on a shared host.
2. **The deauthorize and data-deletion callbacks only acknowledge.** They return
   200 so the Meta dashboard is satisfied, but do not verify Meta's signed
   request or delete anything. If this ever serves accounts other than your own,
   both become a compliance obligation rather than a formality.
3. **Permissions are per-role, not per-record.** A role says *what* someone may
   do, not *which* rows they may do it to. Everyone who can see automations sees
   all of them. That is correct while a deployment serves one Instagram account;
   serving several would need record-level scoping as well.
4. **No CSP nonces.** `style-src` allows `unsafe-inline` because the build
   inlines styles. Tightening it needs a build change.
5. **Link tokens are unguessable but not expiring.** A leaked tracking URL keeps
   redirecting. Targets are the operator's own public URLs, so the exposure is
   the click count, not the destination.

## Rotating a secret

`ENCRYPTION_KEY` cannot be rotated in place — stored tokens are encrypted under
the old key and will fail to decrypt. To rotate: disconnect the Instagram
account, change the key, `make reload`, reconnect. `SESSION_SECRET` can be
changed freely; it signs out everyone. `META_APP_SECRET` must match the Meta
dashboard, and changing it invalidates webhook signatures until both sides agree.
