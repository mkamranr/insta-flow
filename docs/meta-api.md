# Meta Instagram API — Verified Reference

**Verified on: 2026-08-31.** Every fact below was read from official Meta documentation on
that date. Meta changes this platform frequently — before implementing anything not covered
here, read the official docs and update this file with a new verification date.

**Flavour in use: Instagram API with *Instagram Login*** (not Facebook Login). No Facebook
Page is required, and the account authenticates with Instagram credentials.

**Cost: zero.** The Meta app, the Instagram Professional account, Standard Access, and App
Review are all free. App Review costs time, not money.

## Sources

- Instagram API with Instagram Login — <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login>
- Overview — <https://developers.facebook.com/docs/instagram-platform/overview/>
- Business Login for Instagram — <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login>
- Private Replies — <https://developers.facebook.com/docs/instagram-platform/private-replies/>
- Messaging API — <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/>
- Webhooks — <https://developers.facebook.com/docs/instagram-platform/webhooks>
- Webhooks for Instagram Login — <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/webhooks>
- Webhook notification examples — <https://developers.facebook.com/docs/instagram-platform/webhooks/examples/>
- Webhooks reference: Instagram — <https://developers.facebook.com/docs/graph-api/webhooks/reference/instagram/>

---

## 1. Quick reference

| Item | Verified value |
| --- | --- |
| API host | `graph.instagram.com` |
| API version in current docs | `v25.0` — configurable via `META_API_VERSION` |
| Scopes | `instagram_business_basic`, `instagram_business_manage_comments`, `instagram_business_manage_messages` |
| Access level needed | **Standard Access** connects your own account |
| App Review | Unresolved for this use case — Meta's guidance points both ways. See §7 |
| Business Verification | **Not required** |

## 2. Account requirements

- The Instagram account must be a **Professional** account (Business or Creator). A personal
  account cannot receive comment webhooks.
- No linked Facebook Page is required for the Instagram Login flow.
- Under Standard Access, the account must hold a role on the app (Instagram Tester or an
  admin/developer role).

## 3. OAuth

### 3.1 Authorization

```
GET https://www.instagram.com/oauth/authorize
      ?client_id=<INSTAGRAM_APP_ID>
      &redirect_uri=<REDIRECT_URI>
      &response_type=code
      &scope=instagram_business_basic,instagram_business_manage_comments,instagram_business_manage_messages
      &state=<CSRF_STATE>
```

- `scope` is comma-separated (URL-encoded space-separated is also accepted).
- `redirect_uri` must **exactly** match a URI registered in the App Dashboard.
- Optional: `state`, `enable_fb_login`, `force_reauth`.

Meta redirects to `redirect_uri?code=<CODE>&state=<STATE>`.

### 3.2 Code → short-lived token

```
POST https://api.instagram.com/oauth/access_token
Content-Type: application/x-www-form-urlencoded

client_id=<INSTAGRAM_APP_ID>
&client_secret=<INSTAGRAM_APP_SECRET>
&grant_type=authorization_code
&redirect_uri=<REDIRECT_URI>
&code=<CODE>
```

The authorization code is **valid for 1 hour and single-use**.

### 3.3 Short-lived → long-lived token

```
GET https://graph.instagram.com/access_token
      ?grant_type=ig_exchange_token
      &client_secret=<INSTAGRAM_APP_SECRET>
      &access_token=<SHORT_LIVED_TOKEN>
```

Long-lived tokens are **valid for 60 days**.

### 3.4 Refresh

```
GET https://graph.instagram.com/refresh_access_token
      ?grant_type=ig_refresh_token
      &access_token=<LONG_LIVED_TOKEN>
```

Conditions: the token must be **at least 24 hours old**, still valid, and the user must have
granted `instagram_business_basic`. **A token not refreshed within 60 days expires** and the
account must reconnect. InstaFlow runs a scheduled refresh well before that boundary.

## 4. Private replies

The core capability of this product.

```
POST https://graph.instagram.com/<API_VERSION>/<IG_USER_ID>/messages
Authorization: Bearer <INSTAGRAM_USER_ACCESS_TOKEN>
Content-Type: application/json

{
  "recipient": { "comment_id": "<COMMENT_ID>" },
  "message":   { "text": "<MESSAGE_TEXT>" }
}
```

Success:

```json
{ "recipient_id": "526...", "message_id": "aWdfZ..." }
```

### Restrictions — enforce these in code, do not rely on Meta rejecting the call

- **Only one message may be sent to a given commenter** for a given comment.
- **The message must be sent within 7 days** of the comment being made on the post or reel.
- The private reply automatically includes a link to the post or comment.
- For Live comments, private replies are only possible during the broadcast.
- Any follow-up message requires the recipient to have replied within 24 hours.

### Rate limit

**750 calls per hour, per Instagram professional account**, for private replies to comments
on posts and reels. (Instagram Live private replies are governed separately.)

### Permission ambiguity — recorded, not guessed

The Private Replies page lists `instagram_business_basic` + `instagram_business_manage_comments`
for this endpoint. The Messaging API page lists `instagram_business_basic` +
`instagram_business_manage_messages` for the same `/messages` path. The docs disagree.

**Resolution:** request all three scopes at OAuth. Both documented sets are then satisfied.
Do not attempt to narrow this without re-reading both pages.

## 4b. Capabilities verified for the automation features

Verified 2026-08-31. Each was checked before being promised, per rule 2.

### Public comment replies — supported

```
POST https://graph.instagram.com/<API_VERSION>/<IG_COMMENT_ID>/replies
Content-Type: application/json

{"message": "Thanks for sharing!"}
```

Requires `instagram_business_manage_comments`, which we already hold. Related:
`POST /<IG_COMMENT_ID>` hides/unhides, `DELETE /<IG_COMMENT_ID>` deletes,
`GET /<IG_COMMENT_ID>/replies` lists replies.

This resolves the §86 / Phase 13 open question: public replies **are** available for the
Instagram Login flow.

### Buttons on messages — supported

Generic and button templates accept `web_url` and `postback` button types. This makes the
two-step flow possible:

1. The **private reply** is one message only, so it cannot carry the payoff link and still
   count as a reply. It carries an opening message plus a `postback` button.
2. Tapping the button is a user-initiated interaction, which opens the 24-hour messaging
   window and arrives as a `messaging_postbacks` webhook.
3. Inside that window the main DM is sent, carrying `web_url` link buttons.

Subscribing to `messaging_postbacks` requires `instagram_business_manage_messages`, which we
already hold.

### "Require follow before sending" — supported

The Messaging **User Profile API** exposes `is_user_follow_business` for an IGSID, alongside
`name`, `username`, `profile_pic`, `follower_count`, and `is_business_follow_user`. That is
the officially supported way to gate on following — no scraping, no guessing.

Docs: <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/user-profile>

### "Collect email before sending" — no API blocker

Purely a conversational flow: ask in the DM, read the reply from the `messages` webhook, store
it. Nothing here needs a capability Meta does not already grant.

## 5. Webhooks

### 5.1 Verification (GET)

Meta sends a `GET` to the callback URL with:

| Parameter | Value |
| --- | --- |
| `hub.mode` | always `subscribe` |
| `hub.verify_token` | the token configured in the App Dashboard |
| `hub.challenge` | an integer to echo back |

Reject unless `hub.mode == "subscribe"` **and** `hub.verify_token` matches the configured
value; otherwise return `hub.challenge` verbatim as `text/plain` with HTTP 200.

### 5.2 Signature validation (POST)

Header: `X-Hub-Signature-256`, value `sha256=<hex>` where `<hex>` is HMAC-SHA256 of the
**raw request body** keyed by the app secret.

Compute over the raw bytes, before any JSON parsing — re-serialising the payload produces a
different signature. Compare with `hmac.compare_digest`.

### 5.3 Subscribing an account

```
POST https://graph.instagram.com/<API_VERSION>/me/subscribed_apps
      ?subscribed_fields=comments
      &access_token=<INSTAGRAM_USER_ACCESS_TOKEN>
```

Response: `{"success": true}`. `/me` resolves to the app user's Instagram professional
account. InstaFlow subscribes to `comments` only; `messages` is not needed for the MVP.

### 5.4 Fields available with Instagram Login

| Field | Required scopes |
| --- | --- |
| `comments`, `live_comments`, `message_echoes` | `instagram_business_basic` + `instagram_business_manage_comments` |
| `messages`, `message_reactions`, `messaging_handover`, `messaging_optins`, `messaging_postbacks`, `messaging_referral`, `messaging_seen`, `standby` | `instagram_business_basic` + `instagram_business_manage_messages` |

### 5.5 `comments` payload

```json
{
  "object": "instagram",
  "entry": [{
    "id": "<IG_ACCOUNT_ID>",
    "time": 1520383571,
    "changes": [{
      "field": "comments",
      "value": {
        "from": { "id": "<IG_SCOPED_USER_ID>", "username": "<USERNAME>" },
        "comment_id": "<COMMENT_ID>",
        "parent_id": "<PARENT_COMMENT_ID>",
        "text": "<COMMENT TEXT>",
        "media": {
          "id": "<MEDIA_ID>",
          "media_product_type": "REELS",
          "original_media_id": "<ORIGINAL_MEDIA_ID>",
          "ad_id": "<AD_ID>",
          "ad_title": "<AD_TITLE>"
        }
      }
    }]
  }]
}
```

### 5.6 Four parser requirements

1. **The field name lives at `entry[].changes[].field`, not `entry[].field`.** The sketch in
   `project-plan.md` §24 reads `entry.get("field")` and would therefore match nothing. That
   sketch is illustrative only; this document governs.
2. **The comment id key is inconsistent across Meta's own docs.** The webhooks *reference*
   page documents `value.id`; the *examples* page shows `value.comment_id`. Accept either,
   preferring `comment_id`, and record which key was present.
3. **Every field is optional.** `parent_id`, `ad_id`, `ad_title`, and `original_media_id`
   appear only when applicable. Never index blindly.
4. **Skip self-comments.** The connected account's own comments and replies also fire this
   webhook. Drop any event whose `value.from.id` is the connected account, or InstaFlow will
   trigger on its own replies.

## 6. Rate limits (platform-wide)

| Scope | Limit |
| --- | --- |
| Private replies to post/reel comments | 750 calls/hour per IG professional account |
| Standard Graph endpoints | `4800 × impressions` per 24-hour rolling window |
| Send API — text and links | 100 calls/second |
| Send API — media | 10 calls/second |
| Conversations API | 2 calls/second per account |

InstaFlow's per-campaign limits are **application safety controls**, unrelated to these.

## 7. Access levels

| | Standard Access | Advanced Access |
| --- | --- | --- |
| Default | Yes | No |
| Serves accounts you own/manage | Yes | Yes |
| Serves third-party accounts | No | Yes |
| App Review required | No | Yes |
| Business Verification required | No | Yes |

### Does this need App Review? Unresolved — and Meta says both things

Two statements from Meta's own dashboard, which point in opposite directions for this exact
use case.

On the webhooks configuration page:

> To get webhooks notification that include data owned or managed by people who do not have a
> role on your app, your app must have been approved in App Review.

On the App Review page:

> You can skip this step if you're a direct developer who only builds for your own Instagram
> businesses and don't plan to create solutions for clients.

The tension is over who "owns or manages" a comment on your post. If it is the **commenter**,
App Review is needed before your audience can trigger anything. If it is **you**, as the owner
of the media the comment sits on, the second statement exempts this deployment entirely.

Note the first says "owned **or managed**". A comment on media you own is plausibly managed by
you — which is also the reading under which `instagram_business_manage_comments` lets you
delete and reply to those comments at all.

**We do not yet know which applies.** Do not plan around either until it is tested.

### How to settle it

A comment from a non-tester account did produce no webhook here — but the `comments` field was
also not subscribed at the app level at the time, which alone explains it. One cause was
enough; a second was never demonstrated.

Test in this order, changing one thing at a time:

1. Subscribe the **`comments`** field at the app level (step 8). This is confirmed missing and
   is sufficient on its own to explain silence.
2. Comment from an account that **is** an Instagram Tester. If nothing arrives, the problem is
   still configuration, not permissions.
3. Comment from an account that is **not** a tester and holds no role.
   - Webhook arrives → the exemption applies, no App Review needed for your own account.
   - Nothing arrives → the commenter's role is the binding constraint, and App Review is
     required before real followers can trigger anything.

Step 3 is the experiment that answers the question. Record the result here with the date.

### Result — 2026-08-31

**A comment from an account with no role on the app produced no webhook at all.**

Everything on our side was verified working at the same moment, which is what makes the
result meaningful rather than just another silence:

| Checked | Result |
| --- | --- |
| Funnel up, publicly reachable | ✅ external fetch returned `{"status":"ok","service":"instaflow"}` |
| Webhook endpoint responding | ✅ 403 on an unsigned request, as designed |
| App-level `comments` subscription | ✅ Meta's own test payload arrived and parsed correctly |
| Account-level subscription | ✅ `me/subscribed_apps` lists `comments` |
| Real comment from a non-tester | ❌ **no event delivered** |

Not a signature rejection, not a parse failure, not a queue problem — Meta never sent it.

### Confirmed on a second, independent path — 2026-09-01

The same comment was then queried through the **read** API, which has nothing to do with
webhooks:

```
GET /<MEDIA_ID>?fields=comments_count   →  {"comments_count": 1}
GET /<MEDIA_ID>/comments                →  {"data": [], "paging": {...}}
```

Meta **counts** the comment and **withholds its content**. The empty array comes back with
paging cursors, so this is filtering, not absence.

**This settles it: the restriction is on the data, not on the delivery mechanism.** Webhooks
and polling are blocked by the same boundary — content authored by someone with no role on the
app is not returned through any path. No client-side workaround exists, and none can be built.

The fix has to come from Meta's side: Advanced Access to
`instagram_business_manage_comments`, which means App Review.

### The tester workaround does not work either — 2026-09-01

An earlier version of this section claimed that a comment from an Instagram Tester would flow
through, and that this was how to demo the pipeline. **That was asserted without testing it,
and it is wrong.**

Tested: a second account was added as an Instagram Tester, the invite was accepted, and that
account commented. Result:

- No webhook delivered
- The comment is still not readable — `comments_count` rose from 8 to 9, `data` stayed `[]`
- The token holds all three scopes, and the edge returns HTTP 200 with paging cursors, so this
  is filtering rather than a permission error

So holding a role on the app does **not**, on its own, make a commenter's content visible.

**One untested variable remains:** the access token was issued before those tester roles
existed. Meta bakes authorization state into a token at issue time, so a token minted earlier
may not reflect a later role change. Reconnecting the account mints a fresh one. Until that is
tried, "tester roles do not help" is the observation, not the explanation.

### Reconnecting does not help either — conclusion

Tested with a token minted after the tester roles existed (fresh OAuth, webhooks re-subscribed,
tester commented again): still no webhook, still `0` of `9` comments readable.

**Conclusion. Comment content is not available at Standard Access.** Not through webhooks, not
through the read API, not with an Instagram Tester role, not with a freshly minted token.

What was ruled out along the way, each independently verified:

| Hypothesis | Verdict |
| --- | --- |
| Funnel down or endpoint unreachable | ✗ — external fetch returned our service throughout |
| Webhook field not subscribed | ✗ — Meta's own test payload arrived and parsed |
| Account-level subscription missing | ✗ — `me/subscribed_apps` lists `comments` |
| Scopes not granted | ✗ — all three present on the token |
| Signature or parse failure | ✗ — no event arrives at all to fail |
| Only non-role commenters blocked | ✗ — an accepted tester's comment was blocked too |
| Token predates the role change | ✗ — a fresh token behaves identically |

The edge returns HTTP 200 with paging cursors and an empty array, which is filtering rather
than absence or error.

**The only remaining route is Advanced Access to `instagram_business_manage_comments`, which
requires App Review, which requires verification.** No code change affects this, and nothing
further is worth testing without that access.

This is also why the paid tools work: they hold Advanced Access. The approval is the moat, not
the software.

| Goal | What you need |
| --- | --- |
| Connect your own Instagram account | Standard Access — already done |
| Test the pipeline end to end today | An Instagram Tester account, invite accepted |
| React to comments from real followers | Unknown until step 3 above is run |

If App Review does turn out to be required, it is **free** — it costs time, not money, so the
no-paid-services constraint holds either way.

---

# Meta App Setup — steps for the account owner

Perform these in the Meta App Dashboard. Everything here is free.

Meta reorganises this dashboard often, and it has two generations of navigation in the wild.
These steps are written against the current **use-case-based** dashboard and start from what
you can actually see on screen rather than assuming a menu exists.

## About the two accounts

Your Meta **developer** account is a Facebook account. Your **Instagram** professional account
is separate, and very likely a different identity. **That is expected and fully supported** —
it needs no fixing.

This is the reason the project uses the *Instagram Login* flavour of the API rather than
*Facebook Login*: Instagram Login authenticates against Instagram credentials directly and
requires **no linked Facebook Page**. Your personal Facebook account never needs any
relationship to the Instagram account beyond owning the app.

Public base URL for this deployment (Tailscale Funnel):

```
https://your-machine.your-tailnet.ts.net
```

---

## Before Milestone 3 — create the app and collect credentials

### 1. Instagram Professional account

In the Instagram mobile app: *Settings → Account type and tools → Switch to professional
account* → Business or Creator. Free. A personal account cannot receive comment webhooks.

### 2. Meta developer account

<https://developers.facebook.com> → *Get Started*, accept the terms.

### 3. Create the app

*My Apps → Create App* → name it `InstaFlow` → select the use case
**"Manage messaging and content on Instagram"**.

- A Business portfolio is **optional**. You can defer it.
- `Required actions` in the sidebar usually asks for business verification. **Standard Access
  does not need it** — ignore it. See §7.

### 4. Open the Instagram API setup page

> **This is the step that trips people up.** `API setup with Instagram business login` does
> **not** exist in the sidebar until you customize the use case. The customize step is what
> *creates* that menu entry — so if you have not done it, there is no Instagram entry to find,
> and older guides that say "left sidebar → Instagram" simply do not apply.

Open your app and look at the left sidebar:

```
Do you see "API setup with Instagram business login"?
│
├─ YES ──▶ already customized. Go to step 5.
│
├─ NO, but you see "Use cases"
│    │
│    └─▶ click Use cases
│         │
│         ├─ "Manage messaging and content on Instagram" is listed
│         │    │
│         │    └─▶ click Customize
│         │         │
│         │         ├─ offered "API setup with Instagram business login"
│         │         │    └─▶ select it
│         │         │        click "Add all required permissions"
│         │         │        the sidebar entry now exists → step 5
│         │         │
│         │         └─ ONLY "API setup with Facebook login" is offered
│         │              └─▶ this app is bound to the wrong setup
│         │                  and cannot be converted → step 3b
│         │
│         └─ the Instagram use case is NOT listed
│              └─▶ "Add use case" → add it → Customize as above
│
└─ NO, and there is no "Use cases" either
     └─▶ the app type does not support Instagram → step 3b
```

Required permissions on that page: `instagram_business_basic`,
`instagram_business_manage_comments`, `instagram_business_manage_messages`. See §4 for why all
three are requested.

**Seeing `Facebook login for business` in the sidebar is normal.** Meta adds it alongside many
use cases. It is not the problem, and it does not need to be removed.

### 3b. Fallback — start a clean app

*Out of order deliberately: you only arrive here from step 4's tree.*

Meta's rule: **"You can only add one setup per app. If you want to implement both setups,
create an app for each setup."** An app already bound to the *Facebook login* setup therefore
cannot be converted to *Instagram login*.

Do not fight it. Create a new app from step 3, choosing the Instagram business login setup at
the customize step. Apps are free and take about a minute. Leave the old app alone.

### 5. Copy the credentials

On the **API setup with Instagram business login** page, in the token-generation area near the
top, find the **Instagram App ID** and **Instagram App Secret**. Put them in `.env`:

```env
META_APP_ID=<Instagram App ID>
META_APP_SECRET=<Instagram App Secret>
```

> **⚠ These are the *Instagram* App ID and Secret**, found on this page only. They are **not**
> the App ID and App Secret under *App settings → Basic* — those are the Facebook ones, and
> they are different values. Using the Facebook pair makes OAuth fail with an unhelpful error.
> After the step-4 navigation problem, this is the most common setup mistake.

**Check your work** — reload the containers and confirm the credentials registered:

```bash
make reload    # docker compose up -d  (recreates containers with the new .env)
curl -s http://127.0.0.1:8090/ready | python3 -m json.tool
```

Expect `"status": "ready"` and no `missing_config` key.

> **Use `make reload`, not `docker compose restart`.** Compose reads `env_file` when a
> container is *created*, not when it restarts, so `restart` silently keeps the old
> environment and `/ready` still reports the values as missing.

---

## At Milestone 4 — the server must be running and publicly reachable

### 6. Start the tunnel

```bash
tailscale up      # log in, if not already
make funnel
```

**On the very first run, Funnel must be enabled for your tailnet.** It is off by default. The
command prints a `https://login.tailscale.com/f/funnel?node=…` URL and then *blocks silently*
waiting for you to approve — that looks like a hang, but it is not. Open the URL, approve,
Ctrl-C, then run `make funnel` again. One-time step.

Then verify from a device **outside** the tailnet (a phone on mobile data works — a device on
your tailnet would reach it privately and prove nothing):

```
curl https://your-machine.your-tailnet.ts.net/health   # expect {"status":"ok"}
```

`make funnel-off` stops exposing the port.

### 7. Business login settings — the OAuth redirect URI

> **⚠ Two different fields, both called some variant of "callback".** Setting one does not
> set the other, and getting them mixed up is the most common failure here:
>
> | Section | Field | Value ends with |
> | --- | --- | --- |
> | *Set up Instagram business login* (step 7) | **Valid OAuth Redirect URIs** | `/api/auth/instagram/callback` |
> | *Configure webhooks* (step 8) | **Callback URL** | `/webhooks/instagram` |
>
> If Connect Instagram fails with **"Invalid Request: Request parameters are invalid: Invalid
> redirect_uri"**, this step has not been done — the webhook Callback URL does not satisfy it.

On the **API setup with Instagram business login** page → *Set up Instagram business login*:

| Field | Value |
| --- | --- |
| Valid OAuth Redirect URI | `https://your-machine.your-tailnet.ts.net/api/auth/instagram/callback` |
| Deauthorize callback URL | `https://your-machine.your-tailnet.ts.net/api/auth/instagram/deauthorize` |
| Data deletion request URL | `https://your-machine.your-tailnet.ts.net/api/auth/instagram/data-deletion` |

Must match byte-for-byte, including the absence of a trailing slash. Run `make tunnel-status`
to print both URLs ready to copy, rather than typing them out.

### 7a. Make sure you are on the right page

**Two different pages both have a field called exactly "Valid OAuth Redirect URIs."** Only one
of them is read by our flow. This is the single most likely cause of a persistent
`Invalid redirect_uri`.

Tell them apart by what else is on the page:

| | Instagram business login settings ✅ | Facebook Login for Business ❌ |
| --- | --- | --- |
| Valid OAuth Redirect URIs | yes | yes — **the decoy** |
| Deauthorize callback URL | yes | no |
| Data deletion request URL | yes | no |
| Client OAuth login / Enforce HTTPS / Strict Mode | no | yes |
| Login from devices | no | yes |
| Login with the JavaScript SDK | no | yes |
| Allowed Domains for the JavaScript SDK | no | yes |

If the page mentions the JavaScript SDK or smart TVs, you are on the Facebook Login page and
anything saved there is invisible to our flow.

The right page: **Instagram → API setup with Instagram business login → Set up Instagram
business login**. It has three fields and nothing about SDKs.

> **The redirect URI validator lies here.** Run *Redirect URI to check* on the Facebook Login
> page and it will report our callback URL as **valid** — because it is, in the Facebook list.
> That result says nothing about the Instagram list. Only trust the validator on the Instagram
> business login page.

### 7b. Ignore the Facebook Login OAuth panel

The dashboard also has a **Client OAuth Settings** panel — *Client OAuth login*, *Web OAuth
login*, *Enforce HTTPS*, *Use Strict Mode for redirect URIs*, and so on. That panel belongs to
**Facebook Login for Business**, a separate flow with its own redirect URI list.

InstaFlow does not use it. Our flow calls `https://www.instagram.com/oauth/authorize` and reads
its redirect URIs from *Instagram business login settings* (step 7). Nothing in the Client OAuth
Settings panel can fix or cause an `Invalid redirect_uri` from our flow.

Optional hardening, since that surface is unused: turn **Client OAuth login** off, and leave
**Enforce HTTPS** and **Use Strict Mode for redirect URIs** on. All reversible.

### 8. Configure webhooks

Same page, *Configure webhooks*:

| Field | Value |
| --- | --- |
| Callback URL | `https://your-machine.your-tailnet.ts.net/webhooks/instagram` |
| Verify token | the exact `META_WEBHOOK_VERIFY_TOKEN` value from `.env` |

Click **Verify and save**. Meta sends the GET verification immediately, so the API must
already be running — that is why this step follows Milestone 3.

> **Then subscribe to the `comments` field — this is a separate action.** Below the callback
> URL is a list of webhook fields, each with its own **Subscribe** button. Saving the callback
> URL does not subscribe anything. Verified in live traffic: with the field left unsubscribed,
> messaging events arrive normally and comment events never do.
>
> **There are two independent subscriptions and both must include `comments`:**
>
> | Level | Where | Set by |
> | --- | --- | --- |
> | App | Meta dashboard → Configure webhooks → field list | you, by clicking Subscribe |
> | Account | `POST /me/subscribed_apps` | InstaFlow, via *Subscribe webhooks* |
>
> The Instagram page in InstaFlow reads the **account** level back from Meta, so it can show
> `subscribed (comments)` while the app level is still missing it. If comments are not arriving
> and that page looks correct, the app level is what to check.

### 9. Grant your Instagram account access to the app

Two halves. **Both are required**, and missing the second is a common cause of OAuth failing
for an account that looks correctly configured.

**9a — invite, from the Meta side.** *App roles → Roles* → add your Instagram account as an
**Instagram Tester**.

**9b — accept, from the Instagram side.** This is done on **instagram.com in a browser**, not
in the mobile app — the Tester Invites tab does not reliably appear on mobile.

Go directly to:

```
https://www.instagram.com/accounts/manage_access/
```

or navigate: instagram.com → your profile picture → the gear/settings icon → **Apps and
Websites**. That page has three tabs — *Active*, *Expired*, **Tester Invites**. Open **Tester
Invites** and click **Accept**.

Be signed in as the **Instagram account being invited**, not the Facebook developer account.
If the tab is empty, wait a minute and reload; the invite can take a moment to appear.

Until accepted, the role sits at `pending` and behaves exactly as though it were never added —
OAuth fails for an account that looks correctly configured. Testers need a role on the app
*and* a role on the Instagram professional account.

### 10. Connect

In InstaFlow: *Instagram → Connect Instagram*, and consent to the three scopes.

**Business Verification is not required.** Whether **App Review** is required before comments
from people without an app role reach the webhook is unresolved — Meta's own guidance points
both ways. See §7 for the two statements and the experiment that settles it. Testing with an
Instagram Tester account works regardless.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| No `Instagram` / `API setup…` entry in the sidebar | Use case not customized yet — step 4 |
| Only *API setup with Facebook login* is offered | App bound to the wrong setup; one setup per app — step 3b |
| `Required actions` demands business verification | Not needed for Standard Access — ignore |
| OAuth fails immediately | Facebook App ID used instead of Instagram App ID — step 5 |
| `Invalid redirect_uri` on Connect | OAuth redirect URI not registered — step 7, not step 8 |
| `redirect_uri` mismatch | Trailing slash, `http` vs `https`, or a stale Tailscale hostname |
| Tester stuck at `pending` | Invite not accepted at instagram.com/accounts/manage_access/ — step 9b |
| "Verify and save" fails | API not running, funnel down, or `META_WEBHOOK_VERIFY_TOKEN` mismatch |
| Verification passes, only messaging events arrive | Either the `comments` field is not subscribed at the **app** level (step 8), or the commenter holds no role on the app (§7) |
| Verification passes, no events at all | Callback URL wrong, or the funnel is down |
| Signature check fails on every request | Body re-serialised before HMAC — must use raw bytes |
| Private reply errors after 7 days | Outside the eligibility window; expected and non-retryable |
