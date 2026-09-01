# Publishing the Meta app

Meta will not deliver webhooks covering your real audience while the app sits in
Development Mode. Publishing is what changes that, and it has its own set of
prerequisites — separate from the webhook field subscription and from App Review.

Everything here is free.

## What publishing needs

| Requirement | Status | Where |
| --- | --- | --- |
| Privacy Policy URL | ✅ served by this app | `<base>/privacy` |
| Data Deletion URL | ✅ served by this app | `<base>/data-deletion` |
| Deauthorize callback | ✅ served by this app | `<base>/api/auth/instagram/deauthorize` |
| App icon, 1024×1024 | ✅ generated | `brand/app-icon-1024.png` |
| App category | you choose | *App settings → Basic* |
| Display name, contact email | you choose | *App settings → Basic* |

Get your URLs with:

```bash
make tunnel-status
```

## Steps

**1. Fill in App settings → Basic**

| Field | Value |
| --- | --- |
| Display name | `InstaFlow` (or anything not implying Meta endorsement) |
| App contact email | your email |
| Privacy Policy URL | `<base>/privacy` |
| User Data Deletion | *Data Deletion Instructions URL* → `<base>/data-deletion` |
| App icon | upload `brand/app-icon-1024.png` |
| Category | Business, or Messaging |

Save.

**2. Confirm the URLs are publicly reachable**

Meta fetches them, so `localhost` will not do. From a device off your tailnet:

```
curl -sI <base>/privacy        # expect 200
curl -sI <base>/data-deletion  # expect 200
```

If these fail, the funnel is down — `make funnel`.

**3. Switch the app to Live**

Toggle at the top of the App Dashboard. If it refuses, it lists what is missing;
that list is authoritative, and this page is only a prediction of it.

**4. Re-check the webhook subscription**

Publishing can reset webhook state. Confirm `comments` is still subscribed at the
**app** level (*Instagram → Configure webhooks*), and re-run *Subscribe webhooks*
on the Instagram page to refresh the account level.

**5. Test with a comment from an account holding no role**

This is the experiment that settles whether App Review is also needed. See
`docs/meta-api.md` §7.

## If App Review turns out to be required

Meta's App Review page says you may skip it if you are "a direct developer who
only builds for your own Instagram businesses". If a non-tester comment still
produces no webhook after publishing, that exemption does not cover this, and
you will need Advanced Access for `instagram_business_manage_comments` and
`instagram_business_manage_messages`.

A submission needs:

- **A screencast** showing the whole flow: signing in, connecting the Instagram
  account, creating an automation, someone commenting, and the DM arriving.
  Record it with the app working against a tester account — which is why getting
  that working first matters.
- **A written use case** for each permission. Be concrete:
  - `instagram_business_manage_comments` — read comments on the app user's own
    media in order to detect a keyword, and reply publicly when configured.
  - `instagram_business_manage_messages` — send the private reply the comment
    asked for, and the follow-up after the person taps the button.
- **Test credentials** — a dashboard login. Create a throwaway admin with
  `make bootstrap-admin`; do not submit your own.
- **Screenshots** of the dashboard.

Review takes days, not minutes. Nothing about it costs money.

## Verification: you may not need a company

Publishing prompts for verification — *"Meta requires verification processes to help us
understand who is creating apps on this platform."* That reads like it demands a registered
business. It may not.

Meta documents two routes:

| Route | What it needs | Speed |
| --- | --- | --- |
| **Admin / individual verification** | A government-issued ID | Minutes |
| **Business verification** | Incorporation certificate, business licence | Days |

The individual route exists for solo developers with no company paperwork, which is this case.

> **Confidence note.** The clearest documentation for the two-route split is Meta's *Horizon OS*
> developer material. It could not be confirmed from Instagram Platform documentation that the
> same two options appear in this flow. The pattern is probably identical, but the dashboard is
> authoritative — start the verification and see which options it actually offers. If it shows
> "verify your identity" next to "verify your business", take the identity one.

A **business portfolio** is a separate, lighter thing: an admin container in Meta Business
Manager that an individual can create under their own name. It is not proof of incorporation
and does not by itself require verification.

## Honest note on scope

This deployment serves one Instagram account: yours. That is the case Meta's
exemption language describes, and it is why Business Verification is not needed.
If you later run it for someone else's account, that is a different product with
different obligations — App Review becomes unavoidable, and the deauthorize and
data-deletion callbacks stop being formalities. See `docs/security.md` § Known
gaps.
