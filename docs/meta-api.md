# Meta Instagram API — Verified Reference

**Verified on: 2026-08-31; §§2, 7, 10 re-verified and corrected 2026-09-02; app-creation steps 3-4 and the Tester-role ordering re-verified and corrected 2026-09-07; §2c media pagination verified 2026-09-08; §2d media URL expiry verified live 2026-09-25, including that Meta does not re-mint an expired URL.** Every fact below was read from official Meta documentation on
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
| App Review | **Not required** for your own account — the app must be in **Live** mode. See §7 |
| Business Verification | **Not required** |

## 2. Account requirements

- The Instagram account must be a **Professional** account (Business or Creator). A personal
  account cannot receive comment webhooks.
- No linked Facebook Page is required for the Instagram Login flow.
- In **Development Mode**, the account must hold a role on the app (Instagram Tester or an
  admin/developer role) — and role-less users' comment content is withheld entirely (§7).
- In **Live** mode with Standard Access, comments from accounts with no role are delivered
  normally. Whether the *connecting* account still needs a role in Live mode was not isolated
  here: an `Insufficient Developer Role` error was seen in Development Mode and did not recur
  after the switch to Live, but a Tester role may also have been present. Untested.

## 2b. Account fields — verified live 2026-09-02

`GET /me?fields=...` on `graph.instagram.com`, probed field by field against a live
Standard Access token (asking for an unsupported field fails the whole request, so each was
requested alone).

| Field | Result |
| --- | --- |
| `username` | ✅ |
| `account_type` | ✅ (`MEDIA_CREATOR` here) |
| `followers_count` | ✅ |
| `follows_count` | ✅ |
| `media_count` | ✅ |
| `follower_count` | ❌ HTTP 400 — "Tried accessing nonexisting field" |

**Trap: the two edges spell it differently.**

- Account (`/me`) → **`followers_count`**, plural.
- Messaging User Profile (`get_user_profile`, for a commenter) → **`follower_count`**, singular.

Using the singular form on `/me` returns HTTP 400 and, because one bad field fails the whole
request, takes every other field down with it.

**No follower *history* is available from Meta.** `followers_count` is a point-in-time value,
so growth (new / unfollowed / net) has to be derived from snapshots this app records itself.

## 2c. Media pagination — verified live 2026-09-08

`GET /me/media` is paginated, which nothing here recorded before. The Instagram
reference page for this edge documents only *time*-based pagination (`since`,
`until`) and never mentions `limit`, `after` or `paging` — yet `limit` has always
worked and the edge does return cursors. So this was verified against a live
account rather than taken from the page.

**Response shape.** Observed, with `limit=2`:

```json
"paging": { "cursors": { "after": "...", "before": "..." }, "next": "https://..." }
```

| Fact | Source |
| --- | --- |
| `paging.cursors.after` / `.before`, plus `paging.next` | verified live |
| **`next` absent means this is the last page** | official Graph pagination doc |
| **`after` works as a query parameter on this edge** | verified live: two pages requested, disjoint ids returned |
| `next` is absolute, and **carries the access token** in its query string | verified live |
| `next` **pins its own API version** — observed `…/v26.0/…` while we request `v25.0` | verified live |
| The edge returns **at most 10K** most-recent media | official Instagram reference |
| Cursors must **not** be stored between runs — they invalidate when the data changes | official Graph pagination doc |

**We paginate with `after`, and deliberately do not follow `next`.** Three
reasons, in order of weight: following it would silently move the walk onto
whatever API version Meta pinned into the URL, rather than the one
`META_API_VERSION` selects; `_request` builds its URL from a *path*, so an
absolute URL would produce `…/v25.0/https://…`; and it means re-sending a token
inside a URL this codebase did not construct.

Because cursors expire, a full walk happens in **one pass** — there is no
resumable "carry on where we left off". `MEDIA_MAX_PAGES` is 200, which at
`MEDIA_PAGE_SIZE` 50 is exactly the documented 10K ceiling, so nothing
legitimate is truncated and a cursor that never terminates cannot spin forever.

Routine syncs — the 15-minute cron and every Refresh button — request **one
page**. Only the manual *Resync all posts* action in Settings walks the edge.

## 2d. Media URLs expire — verified live 2026-09-25

`media_url` and `thumbnail_url` are **signed, expiring CDN links**, not stable
addresses. Nothing in this file said so, and the dashboard stored them and
rendered them directly for the whole life of the Posts page. The symptom is an
image that silently fails to load; the body behind it is the giveaway.

| Fact | Source |
| --- | --- |
| The URLs are `*.fbcdn.net` / `*.cdninstagram.com` and carry `&oe=<hex unix ts>` — the expiry | verified live |
| An expired one returns **HTTP 403** with the body `URL signature expired` | verified live, 4 story URLs |
| `media_url` had **≈36 hours** of life; `thumbnail_url` **≈4.5 days** | verified live, measured across 30 posts |
| Expiry varies between objects by several hours and does **not** correlate with post age | verified live |
| Re-fetching `/me/media` returns a **different URL string** — `_nc_gid` and `ccb` rotate | verified live |
| …but with an **identical `oe`**. The expiry did not move. | verified live |

The last two rows are the ones that matter:

```
17885757951580817 VIDEO  media_url  stored_oe=2026-09-26 18:45  fresh_oe=2026-09-26 18:45  identical=False
```

**Expiry is pinned to the media object, not to the request.** So re-syncing does
not extend it, and neither a more frequent cron nor *Resync all posts* can
rescue an image whose window has closed. The only durable fix is to stop
depending on the lifetime at all: copy the bytes while the URL is alive and
serve them ourselves. That is what `services/media_cache.py` and the
`/api/posts/{id}/image` route do, and why the fetch happens at **sync** time —
for a story that is the only moment its URL is ever alive.

### Meta does not re-mint after expiry — verified live 2026-09-25

This was written down first as untested, with the guess that Meta probably
re-mints once a window passes, since Instagram itself keeps rendering the post.
**The guess was wrong**, and it was worth measuring rather than assuming.

Measured on a live account with 96 posts, on a server whose older rows had not
been refreshed since 9 September. A full *Resync all posts* was run at
**13:21**, rewriting every row with whatever `/me/media` returned. The dashboard
was then reloaded and the image requests inspected:

| Requests | `oe` on the URL Meta had just returned | Result |
| --- | --- | --- |
| 61 | 2026-09-29 — in the future | 200 |
| 39 | 2026-09-14 … 2026-09-25 — **already in the past** | **403** |

So a resync performed at 13:21 on the 25th was handed URLs that had expired
eleven days earlier. **Meta serves an already-dead URL rather than signing a
fresh one.** The expiry is frozen to the media object permanently, not merely
for the life of one window.

That makes copying the bytes the *only* available fix, and it puts a deadline on
it: every image is on a clock from the moment it is first seen, and once that
clock runs out no request of any kind brings it back.

Two consequences worth stating out loud:

- **Anything already past its `oe` is gone for good.** Confirmed above, not
  assumed. A post or story whose window closed before its bytes were cached
  shows a placeholder permanently. That is correct behaviour, not a bug.
- **A host that sleeps makes this worse.** The observed cron gaps on the live
  install were 21 and 37 hours — longer than a `media_url` lives.

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
account must reconnect. Social Flow runs a scheduled refresh well before that boundary.

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

### Where the message actually lands — verified 2026-09-05

**A successful send is not a visible send.** Meta's Private Replies page:

> The reply appears in the recipient's Inbox, if the person follows the Instagram professional
> account, or to the **Request folder**, if they do not.

Source: <https://developers.facebook.com/docs/instagram-platform/private-replies/>

This was mistaken for a broken pipeline. A comment from a non-follower produced
`webhook_received` → `private_reply_success` with a `meta_message_id` returned by Meta, and the
commenter still reported receiving nothing. Nothing was wrong: the opener was in **Message
Requests**, which Instagram does not badge like an inbox message.

Two things that look like faults and are not:

- Viewing the business profile from the commenting account shows **"Not everyone can message
  this profile."** That is Instagram describing the *business account's* inbound message
  controls. It does not block an outbound private reply, and the reply had already been
  delivered when it was seen.
- The payoff never arrives on its own. That is the two-step flow working as designed (§4b): a
  private reply is one message, so the opener carries a postback button and the payoff is only
  sent once that button is tapped. An untapped opener means no second message, correctly.

**How to check a send actually landed:** on the commenting account, Instagram → Direct →
**Requests**, not the main inbox. `meta_message_id` in the `executions` row is proof Meta
accepted it; it is not proof anybody saw it.

**For a follow-gated rule, Requests is not the edge case — it is every case.** The gate exists to
reach people who do *not* follow yet, and Meta routes to Requests exactly when the recipient does
not follow. So the first send of a follow-gated automation always lands there. That is the
designed path, not a fault.

### Which recovery the prompt may promise — verified 2026-09-05

The follow prompt has two correct forms, and offering the wrong one strands people. Both are
constants in `backend/app/models/rules.py`, and `tests/test_follow_gate.py` pins the split:

| Rule shape | What actually happens when the gate blocks | Prompt may say |
| --- | --- | --- |
| **With an opening DM** | The postback tap opened a 24h window. `recheck_follow` polls and delivers the link on its own. | *"no need to comment again"* |
| **No opening DM** | Blocked at the comment. The one private reply a comment allows has just been spent on this prompt, and no window is open — there is no channel left. | *must* ask them to comment again |

The shipped default said "comment again" for both, on a product whose default flow is the first
row. The one correct string existed only as a fallback that the editor made unreachable by
pre-filling the wrong one.

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

### "Require follow before sending" — supported, with one hole

The Messaging **User Profile API** exposes `is_user_follow_business` for an IGSID, alongside
`name`, `username`, `profile_pic`, `follower_count`, and `is_business_follow_user`. That is
the officially supported way to gate on following — no scraping, no guessing.

Docs: <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/user-profile>

**Probed live against four real commenters, 2026-09-02:**

| Commenter | Prior DM thread | Result |
| --- | --- | --- |
| A | yes | 200, `is_user_follow_business: true` |
| B | yes | 200, `is_user_follow_business: false` |
| C | **none** | 200, `is_user_follow_business: true` |
| D | none | **HTTP 500** — `User consent is required to access user profile` |

Two things this settles:

1. **A prior DM thread is not required.** Case C was answered with no conversation history.
2. **The lookup can be refused outright.** Case D returns HTTP 500 with "User consent is
   required". That is *unknown*, not "not following" — treating it as a negative would block
   real followers.

**Therefore `None` fails open** in `policy.evaluate`: it blocks on `False` only. A gate that
silently dropped every DM whose lookup failed would be worse than a gate with a narrow hole.

**Enforce at the postback, not only at the comment.** The opener carries no link; the payoff
does. Checking again at the postback means the decision is made at the moment the link
actually changes hands, and by then a thread exists — which is when Meta is most willing to
answer. `follower_count` here is the *commenter's* follower count, and is spelled singular,
unlike the account edge (§2b).

### "Collect email before sending" — no API blocker

Purely a conversational flow: ask in the DM, read the reply from the `messages` webhook, store
it. Nothing here needs a capability Meta does not already grant.

## 4c. Attachments — verified from Meta's docs 2026-09-03

**A private reply is text-only.** The documented body for `recipient.comment_id` is
`{"message": {"text": "..."}}`. No attachment field appears on that page and none is
described. So a file can never ride the reply to a comment.

**Attachments go to an IGSID, inside an open window.**

```
POST https://graph.instagram.com/<API_VERSION>/<IG_USER_ID>/messages

{ "recipient": { "id": "<IGSID>" },
  "message": { "attachment": { "type": "file", "payload": { "url": "<PUBLIC_URL>" } } } }
```

`type` is one of `image`, `video`, `audio`, `file`. Up to ten may be sent by using
`attachments` as an array instead.

| Type | Formats | Max size |
| --- | --- | --- |
| image | png, jpeg | 8 MB |
| video | mp4, ogg, avi, mov, webm | 25 MB |
| audio | aac, m4a, wav, mp4 | 25 MB |
| file | **pdf only** | 25 MB |

**Meta fetches the URL itself; bytes are never uploaded to Meta.** Two consequences that
shape the design:

1. The file must be publicly reachable **at send time**, with no authentication. Behind a
   login, or on a laptop that has gone to sleep, the message silently fails to arrive.
2. Because the reply to a comment cannot carry it, **any rule with an attachment must use the
   opening-DM flow** — the button tap is what opens the 24-hour window the attachment needs.
   A one-step rule with an attachment would never deliver it.

### Meta re-hosts the file — observed live 2026-09-03

A 744KB PDF was sent from `https://<our-host>/files/<uuid>/<name>.pdf`. What arrived in the
recipient's inbox was a **`cdn.fbsbx.com`** URL, not ours.

So Meta fetches the URL once, at send time, and serves its own copy from then on. Three things
follow:

- Our server has to be up **only for that one fetch**, not for the life of the message.
- The recipient never touches our host, so an attachment generates no traffic and leaks no
  server details to them.
- Replacing the file afterwards does not change what an already-delivered message shows. Meta
  has its own copy; the delivered message is effectively frozen.

`.mp4` is listed under both video and audio. Social Flow resolves it to **video**, which is
what a sender almost always means.

Docs: <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api>

## 4d. Story replies and inbound DMs — verified 2026-09-03

Both arrive on the **`messages`** field, which this app already subscribes to. They were
being delivered, stored and enqueued long before anything acted on them.

| Event | Shape |
| --- | --- |
| Story reply | `entry[].messaging[].message.reply_to.story = {id, url}` |
| Quoted reply to a message | `message.reply_to.mid` — an ordinary DM, **not** a story reply |
| Plain DM | `message.text`, no `reply_to` |
| Our own send coming back | `message.is_echo: true` |

**The 24-hour window.** Meta allows 24 hours to reply to a message a user sent. Their own
message opens it, so a story reply or keyword DM is answered with an ordinary
`send_direct_message` — no private reply, no opening DM, no postback button. A file therefore
sends on the first message, unlike the comment path where it needs the button step first.

**Echoes are a loop hazard.** 33 have been observed on this account. Filtering on `is_echo`
alone rests on a single field Meta controls, and a message-id reservation does **not** help:
every echo carries a distinct `mid` and would earn its own valid reservation. Sender identity
is therefore checked independently, at the endpoint and again in the worker.

**Story reactions are undocumented.** Meta's webhook reference does not say how a bare emoji
tap on a story is delivered. The one reaction received here was a reaction to a *message*:

```json
{"reaction": {"mid": "...", "action": "react", "reaction": "other", "emoji": "❤"}}
```

It carries no `sender`, so there would be nobody to reply to. Not implemented; the endpoint
logs the key names of any unhandled messaging event so a real story reaction can be inspected
rather than guessed at.

**Automated-experience disclosure.** Meta requires informing users in California and Germany
that they are interacting with automation. Social Flow appends a configurable line, on by
default, editable in Settings.

**`GET /{ig-user-id}/stories` works under Instagram Login — verified 2026-09-04.**
Meta documents this edge only for Facebook Login (`instagram_basic` +
`pages_read_engagement`), so it was assumed unavailable here. It is not. Called with an
Instagram Login token against a live account it returned four active stories on both
`me/stories` and `{ig-user-id}/stories`.

The earlier `200 {"data": []}` was **genuinely "no active stories"**, not the §7 silent-filter
trap. The two are indistinguishable without a story to test against, which is why this was
held open rather than concluded either way.

Readable fields, confirmed on live data (4 stories, 1 image + 3 video):

| Field | Notes |
| --- | --- |
| `id` | matches `messaging[].message.reply_to.story.id` — this is what makes per-story rules possible |
| `media_type` | `IMAGE` / `VIDEO` |
| `media_product_type` | always `STORY` |
| `permalink` | present on **both** image and video |
| `timestamp` | present on **both** image and video |
| `thumbnail_url` | video only; images carry `media_url` instead |
| `caption` | present only when the story has text — absent on all three videos here |

An earlier note in this file claimed `timestamp` and `permalink` were missing on video
stories. That was wrong, and worth recording as a method failure rather than quietly
deleting: the check printed each story as JSON truncated to 300 characters, and
`thumbnail_url` is long enough to push both fields past the cut. The absence was in the
output, not in Meta's response. Re-checked by asking for field *presence* directly.

Stories expire after 24 hours, so a rule pinned to a story id is dead a day later. Account-wide
is the right default; per-story is an opt-in narrowing, and the UI has to say what happens when
the story expires.

## 4e. The standby channel — verified 2026-09-04

Under the **handover protocol** an app that does not hold thread control still receives
messaging events, but at `entry[].standby[]` rather than `entry[].messaging[]`. Meta's
reference: a standby event fires when *"a message has been sent to your page, but your
application is not the current thread owner."*

**This is invisible unless you subscribe to `standby` and read that key.** Both layers matter:

- `standby` is a separate app-level webhook field. Not subscribing means Meta sends nothing.
- Even subscribed, a parser that reads only `entry[].messaging[]` drops them silently and
  answers `200`.

So "another app has thread control" and "Meta never sent anything" look **identical** from the
server: no request, or a request with nothing in it. Social Flow logs `webhook_standby_events`
with the count and key names for exactly this reason.

**Standby events are never acted on.** An app without thread control cannot send, so a reply
would be rejected at Meta. Taking control (`POST /me/take_thread_control`) would seize the
conversation from whatever holds it — including the Instagram inbox — and is not done
implicitly.

`messaging_postbacks` delivered on standby **omit the postback payload**, so a rule keyed on
`instaflow:<rule>:<comment>` could not be resolved from one even in principle.

Unrelated fields, checked so they are not tried as fixes: `messaging_handover` carries
thread-control transfers between apps (`pass_thread_control`, `take_thread_control`,
`request_thread_control`); `messaging_optins` carries opt-in events. Neither carries story
replies — the `messages` field does.

**The verification handshake sends the token twice.** Meta's `GET` carries *both* spellings
in the same request:

```
?hub.mode=subscribe&hub.verify_token=<TOKEN>&hub_mode=subscribe&hub_verify_token=<TOKEN>
```

Only the dotted form is documented. Anything that redacts secrets from logs has to cover the
underscored one too, or the value survives in the log regardless — observed live 2026-09-04.

**A Tailscale Funnel can serve traffic while Meta cannot validate it.** With the funnel up and
answering external requests correctly (a wrong-token `GET` from outside the tailnet returned
`403`), Meta's own validation still failed and no request reached the server at all. Stopping
the funnel and restarting it — same hostname, same callback URL, nothing else changed — fixed
it immediately. Webhook delivery had been dead for roughly 70 minutes.

Test the funnel from **outside the tailnet**. A `curl` on the host may resolve the `.ts.net`
name locally and never leave the machine, so it proves nothing about public reachability.

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
account. Social Flow subscribes to `comments` only; `messages` is not needed for the MVP.

### 5.4 Fields available with Instagram Login

| Field | Required scopes |
| --- | --- |
| `comments`, `live_comments`, `message_echoes` | `instagram_business_basic` + `instagram_business_manage_comments` |
| `messages`, `message_reactions`, `messaging_handover`, `messaging_optins`, `messaging_postbacks`, `messaging_referral`, `messaging_seen`, `standby` | `instagram_business_basic` + `instagram_business_manage_messages` |

**There is no follow event.** The table above is the complete list, and nothing in it fires when
somebody follows the account. So a "wait until they follow, then send" flow cannot be driven by a
webhook — the only way to notice is to ask, which is what `recheck_follow` does: it re-runs the
`is_user_follow_business` lookup (§4b) on a backoff schedule inside the 24-hour messaging window
the postback opened. Verified 2026-09-05 against this field list.

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
   webhook. Drop any event whose `value.from.id` is the connected account, or Social Flow will
   trigger on its own replies.

## 6. Rate limits (platform-wide)

| Scope | Limit |
| --- | --- |
| Private replies to post/reel comments | 750 calls/hour per IG professional account |
| Standard Graph endpoints | `4800 × impressions` per 24-hour rolling window |
| Send API — text and links | 100 calls/second |
| Send API — media | 10 calls/second |
| Conversations API | 2 calls/second per account |

Social Flow's per-campaign limits are **application safety controls**, unrelated to these.

## 7. Access levels

> **RESOLVED 2026-09-02 — the earlier conclusion in this section was wrong.**
> Comment content from an account with **no role on the app** is delivered normally once the
> app is in **Live** mode. No App Review, no Business Verification. The blocker was
> Development Mode, not the access level. The investigation below is kept as a record of what
> was tested; read "Live mode is the unlock" at the end for the current truth.


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

### Live mode is the unlock — 2026-09-02

**Everything above was measured with the app in Development Mode.** That was the confound. The
app was rebuilt and switched to **Live**, and the same test now passes.

A comment from `kamranrafi` — an account holding **no role on the app**: not an admin, not a
developer, not an Instagram Tester — was delivered in full and answered automatically.

Webhook, from the stored `raw_payload`:

```json
{ "from": { "id": "27962242703427275", "username": "kamranrafi" },
  "media": { "id": "18077914607704323", "media_product_type": "REELS" },
  "id": "18100884581570393",
  "text": "link" }
```

Read API, the same media, the path that previously returned an empty array:

```
GET /<MEDIA_ID>?fields=comments_count   →  {"comments_count": 1}
GET /<MEDIA_ID>/comments                →  {"data": [{"id": "18100884581570393",
                                                      "text": "link", ...}], "paging": {...}}
```

Both Meta sends returned HTTP 200: `opening_dm` at 15:56:44, `private_reply` at 15:57:17 after
the postback. Full pipeline, comment to payoff.

| Path | Development Mode (2026-08-31 / 09-01) | Live Mode (2026-09-02) |
| --- | --- | --- |
| `comments` webhook, role-less commenter | no event | ✅ delivered with text |
| `comments` webhook, accepted Tester | no event | — |
| `GET /<media>/comments` | `{"data": []}` with cursors | ✅ comment returned |
| `POST /<ig-user-id>/messages` private reply | never reached | ✅ 200, message id returned |

### What changed — observation, and the probable cause

Four things changed at once, so the cause is **not isolated**:

1. A **new app**, created as *Other → Business*.
2. The app was switched from Development Mode to **Live**. ← most probable cause
3. It was linked to a **Meta Business Portfolio** (free; no business verification performed).
4. *App Settings → Basic* was completed: Privacy Policy URL, Terms of Service URL, App Icon,
   Data Deletion Instructions URL, Category. Live mode requires these, which is why they
   travel together.

**Live mode is the probable cause**, and it fits Meta's documented model: Standard Access
serves accounts you own or manage, and **Development Mode is what restricts data to people
holding a role on the app**. The dashboard string quoted earlier — "data owned or managed by
people who do not have a role on your app... must have been approved in App Review" — was read
here as governing this use case. It governs **Advanced Access**, i.e. serving *third-party*
accounts. Serving your own account needs neither.

This is stated as the probable cause, not a demonstrated one. Isolating it would mean toggling
the app back to Development Mode and re-commenting. Not worth doing on a working deployment.

### Corrected requirements

| Goal | What you need |
| --- | --- |
| Connect your own Instagram account | Standard Access |
| React to comments from real followers on your own account | Standard Access + app in **Live** mode |
| Serve *other people's* Instagram accounts (SaaS) | Advanced Access → App Review → Business Verification |

**App Review and Business Verification are not required for this deployment.** The earlier
claim that they were — and that "the approval is the moat" — was wrong. The moat was a
dashboard toggle.

### Why the original investigation reached the wrong answer

Every individual observation in it was accurate. The error was in the *explanation*: an
untested variable (Development Mode) was held constant across every experiment, so it could
never show up as a difference. Ruling out seven hypotheses that all shared one confound
produced high confidence in a wrong conclusion.

The lesson is the one already in the ground rules: state observations as observations. "No
webhook arrived" was solid. "Therefore Advanced Access is required" was an inference that was
never tested, and it parked the project for two days.

---

## 7b. More than one Instagram account — verified by construction 2026-09-05

Each person who uses this deployment brings **their own Meta app**, connected to **their own**
Instagram account. Nobody's account is ever reached through somebody else's app.

That is not an implementation detail; it is what keeps the whole deployment on the
Standard Access side of the table in §7. Automating your own account needs only Live mode and
a complete *App Settings → Basic*. Serving *other people's* accounts through your app is the
third-party case, and that one does need Advanced Access → App Review → Business
Verification. Per-user apps keep every user in the first column.

> Do not "simplify" this into one shared app later. The moment a second person's Instagram
> account is reached through the first person's app, this deployment becomes a third-party
> integration and needs App Review.

### Each workspace has its own URLs

A workspace is one user, addressed at `/u/<slug>/`. Its four Meta-facing URLs carry that
prefix, and its Settings page prints them ready to copy:

| Meta dashboard field | Value |
| --- | --- |
| Callback URL (webhooks) | `https://<host>/u/<slug>/webhooks/instagram` |
| Valid OAuth Redirect URIs | `https://<host>/u/<slug>/api/auth/instagram/callback` |
| Privacy Policy URL | `https://<host>/u/<slug>/privacy` |
| Data Deletion Instructions URL | `https://<host>/u/<slug>/data-deletion` |

The same traps from §8 and §7 still apply per app: only the *Instagram → API setup with
Instagram business login* redirect field is read, all three webhook subscription layers are
still required, and the app still has to be switched to **Live**.

### Why the prefix is in the URL and not a header

Three endpoints have to choose an app secret or verify token **before** they can trust
anything else about the request, so the choice cannot come from the payload:

| Endpoint | Why |
| --- | --- |
| `POST /u/<slug>/webhooks/instagram` | The signature is an HMAC of the raw body under the app secret. The body is unverified until that passes, so the account id inside it cannot pick the key that would verify it. |
| `GET /u/<slug>/webhooks/instagram` | The handshake carries only `hub.verify_token`. With N apps there are N valid tokens, and the parameter says only that it is one of them. |
| `deauthorize` / `data-deletion` | Meta's `signed_request` is HMAC'd with the app secret and carries no state parameter. |

Trying each stored secret in turn would work and is an oracle; it also gets slower with every
user. The path segment is the answer.

### Configuring one

Settings → **Instagram app**, per workspace, needs `instagram.manage`:

* **App ID** and **App Secret** — the *Instagram* pair from §5, not the Facebook pair.
* **Webhook verify token** — generated here on first save. It has to be readable, because it
  is typed into Meta's dashboard and re-checked whenever a handshake fails. The App Secret is
  encrypted at rest and never returned.

Settings → **Public address** sets the origin every workspace URL is derived from, for the
whole instance. Changing it updates nothing at Meta: each app keeps calling the old address
until its URLs are re-pasted by hand.

### What stays instance-wide

`ENCRYPTION_KEY`, `SESSION_SECRET`, `DRY_RUN`, MongoDB, Redis, the role catalogue, the Graph
API version, `/r/` click tracking and `/files/` attachments. **`DRY_RUN` in particular is
global** — one workspace live while another is in dry run is not supported.

### `META_APP_ID` and friends are now defaults

The three `META_*` variables in `.env` are the fallback for any workspace that has not
configured its own app, and they keep the pre-workspace URL layout: a workspace on the
instance credentials gets the **unprefixed** `/webhooks/instagram` and
`/api/auth/instagram/callback`, because those are what the already-live Meta app has
registered. `/ready` no longer reports them as missing configuration.

# Meta App Setup — steps for the account owner

> **This walkthrough is also served in-app**, at *Get started*, generated from
> `backend/app/services/onboarding/steps.py`. That version is written for an invited user who
> has no shell: it drops the `.env`, `make reload` and `make funnel` steps, replaces the
> credential step with a paste into Settings, and resolves every copy-out URL from the app so
> it cannot go stale. **The two need to be kept in step** — if you correct something here,
> check whether the step module says the same thing, and vice versa.


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

*My Apps → Create App*, then four screens:

1. Name it — `Social Flow` is fine.
2. Asked what you want your app to do: select **Others**.
3. Under **Looking for something else?**: select **Other**.
4. App type: **Business**.

**Verified 2026-09-07.** This is the route to take. It puts the app in what Meta calls the
**old experience**, where you choose from all available permissions, features and products
yourself rather than being handed a preset — and Instagram is then simply one product you add
(step 4).

> **The use-case route also still exists** — *Create App* will offer
> **"Manage messaging and content on Instagram"** — and it works. It is documented in step 4
> for anyone who took it, but **Other → Business** is the better path: it cannot bind the app
> to the wrong Instagram setup, which cannot be recovered from without recreating the app.

- A Business portfolio is **optional**. You can defer it.
- `Required actions` in the sidebar usually asks for business verification. **Standard Access
  does not need it** — ignore it. See §7.

### 4. Add the Instagram product

**On the old experience (step 3's route), verified 2026-09-07.** On the app dashboard, scroll
to **Available products** and click **Set up** next to **Instagram** — the row reading *"Allow
creators and businesses to manage messages and comments, publish content, track insights,
hashtags and mentions"*. Within its setup, take **Instagram business login**.

> **⚠ `Facebook Login for Business` is in that same products list.** Setting it up instead is
> the most expensive wrong turn available here: it is a separate flow with its own redirect
> URI list, its settings page looks correct, and its redirect-URI validator will report our
> callback as *valid* while our flow never reads it. See §7a and step 7b. Add **Instagram**
> and nothing else from that list.

**On the use-case experience**, if you took that route instead:

> `API setup with Instagram business login` does **not** exist in the sidebar until you
> customize the use case. The customize step is what *creates* that menu entry — so if you
> have not done it, there is no Instagram entry to find, and older guides that say
> "left sidebar → Instagram" simply do not apply.

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
│         │              └─▶ bound to the wrong setup, NOT convertible:
│         │                  recreate via step 3's Other → Business
│         │
│         └─ the Instagram use case is NOT listed
│              └─▶ "Add use case" → add it → Customize as above
│
└─ NO, and there is no "Use cases" either
     └─▶ you are in the old experience — use the products list above
```

Required permissions on that page: `instagram_business_basic`,
`instagram_business_manage_comments`, `instagram_business_manage_messages`. See §4 for why all
three are requested.

**Seeing `Facebook login for business` in the sidebar is normal.** Meta adds it alongside many
use cases. It is not the problem, and it does not need to be removed.

> **If the use-case route offered you only `API setup with Facebook login`**, that app is
> bound to the wrong setup and **cannot be converted** — Meta's rule is *"You can only add one
> setup per app. If you want to implement both setups, create an app for each setup."* Do not
> fight it: create a new app following step 3 (**Other → Business**), which lands in the old
> experience where Instagram is simply one of the products you add, with no use case to bind
> the app to anything. Apps are free and take about a minute. Leave the old one alone.
>
> This was step 3b until 2026-09-07; it is folded in here because the route it recovered from
> is no longer the one this document recommends.

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

### 5b. Switch the app to Live mode — the step that actually unlocks comments

**Do not skip this, and do not spend a day debugging webhooks before doing it.**

In Development Mode, Meta withholds comment content authored by anyone without a role on the
app. Not "delays" — withholds. The webhook never fires and `GET /<media>/comments` returns an
empty array *with paging cursors* beside a non-zero `comments_count`. Every symptom points at a
broken tunnel or a missing subscription, and none of those is the cause. This cost two days
here; §7 records the whole investigation.

Live mode requires **App Settings → Basic** to be complete first:

| Field | Notes |
| --- | --- |
| Privacy Policy URL | `https://<your-host>/privacy` serves a page, or host your own |
| Terms of Service URL | Any reachable page you control |
| App Icon | 1024×1024 PNG — `frontend/public/icon-1024.png` is generated at the right size |
| Data Deletion Instructions URL | `https://<your-host>/api/auth/instagram/data-deletion` |
| Category | Pick anything reasonable, e.g. Business and Pages |

Then flip the toggle at the top of the App Dashboard from **Development** to **Live**.

**What Live mode does not require:** App Review, Business Verification, a registered company,
or a paid plan. Those are for **Advanced Access**, which you only need to serve *other people's*
Instagram accounts. Automating an account you own needs Standard Access in Live mode, and
nothing else.

A Meta Business Portfolio may be linked. Creating one is free and involves no verification.

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
| Data deletion request URL | `https://your-machine.your-tailnet.ts.net/data-deletion` |

> All three are produced by `services/meta/credentials.urls_for` and shown on the Settings
> page. **The deauthorize callback was missing from that set until the guided setup was
> built**, so an app configured by copying from the Settings page before then has it blank.
> Take the values from the app, not from this table — the app knows the workspace prefix.

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

Social Flow does not use it. Our flow calls `https://www.instagram.com/oauth/authorize` and reads
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
> | Account | `POST /me/subscribed_apps` | Social Flow, via *Subscribe webhooks* |
>
> The Instagram page in Social Flow reads the **account** level back from Meta, so it can show
> `subscribed (comments)` while the app level is still missing it. If comments are not arriving
> and that page looks correct, the app level is what to check.

### 9. Grant your Instagram account access to the app

Two halves. **Both are required**, and missing the second is a common cause of OAuth failing
for an account that looks correctly configured.

> **Do this before clicking `Add account`** in the Instagram API setup page's *Generate access
> tokens* section. That page says so itself, in one line of grey text — *"before proceeding,
> make sure to assign the Instagram Tester role to the account in the Roles tab"* — which is
> very easy to read past. The in-app walkthrough orders it that way; this document keeps Meta's
> own numbering, so here it appears later than you will actually need it. Verified 2026-09-07.

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

In Social Flow: *Instagram → Connect Instagram*, and consent to the three scopes.

**Neither Business Verification nor App Review is required** for automating your own account.
The app must be in **Live** mode (step 5b) — in Development Mode, comment content from anyone
without a role on the app is withheld, which is what parked this project for two days. Verified
2026-09-02; see §7.

If a real follower comments and nothing arrives, check the app mode before anything else. Every
other symptom looks identical.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| No `Instagram` / `API setup…` entry in the sidebar | On the use-case route: not customized yet — step 4. On the old experience there is no sidebar entry at all; Instagram is under *Available products* |
| Only *API setup with Facebook login* is offered | Use-case route; one setup per app and not convertible. Recreate via step 3's Other → Business |
| Set up `Facebook Login for Business` by mistake | It sits directly below Instagram in the products list. Add Instagram instead; nothing saved under Facebook Login is read — §7a |
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
