# Production checklist

Work through this before pointing a real audience at the deployment.

## Before enabling real sending

- [ ] `DRY_RUN=true` has run for at least a day and the Activity page shows the
      messages you expected, addressed to the people you expected
- [ ] The rendered message reads correctly — check `message_text` on a dry-run
      execution, not just the template
- [ ] Link buttons point where you think; click one through `/r/{token}` and
      confirm the destination and that the click was counted
- [ ] Per-rule limits are set to something you would be comfortable with if a
      post went unexpectedly viral
- [ ] "Only send once per person" is on unless you have a specific reason

## Secrets

- [ ] `.env` is not in git (`git check-ignore .env` prints the path)
- [ ] `ENCRYPTION_KEY`, `SESSION_SECRET`, `META_WEBHOOK_VERIFY_TOKEN` are
      freshly generated, not the examples (`make secrets`)
- [ ] `.env` is `chmod 600`
- [ ] The admin password is not one you use elsewhere

## Hardening

- [ ] `SESSION_COOKIE_SECURE=true`
- [ ] `APP_ENV=production`
- [ ] MongoDB authentication enabled (see `docs/deployment.md`)
- [ ] Only Caddy's port is reachable; confirm with `docker compose ps` that
      nothing else lists a host binding
- [ ] `curl -I` the public URL and confirm CSP, HSTS, and `X-Frame-Options`

## Operations

- [ ] A backup has been taken *and restored once* — an untested backup is a
      hypothesis
- [ ] You know where the logs are and have read one webhook end to end
- [ ] `make funnel` survives a reboot, or the tunnel is a system service
- [ ] Token expiry is understood: 60 days, refreshed daily by cron, dead if the
      machine is off long enough

## Roles and people

- [ ] Everyone is on the narrowest role that lets them work — the upgrade puts every
      pre-existing account on Owner, which is the right default but not a resting place
- [ ] **At least two Owners.** The last active Owner cannot be deactivated, deleted or
      moved to another role. That guard stops you stranding the deployment, and you will
      meet it at an inconvenient moment if there is only ever one
- [ ] `make bootstrap-admin` still works — it is the recovery path if you lock yourself out
- [ ] Nobody shares a login. Roles are only meaningful if each person has their own
- [ ] Server-wide settings — the public address and the GeoLite2 credentials — are
      Owner-only (`instance.*`). A custom role that used to reach Click geography through
      `settings.edit` needs `instance.manage` granted explicitly

## Meta

- [ ] Webhook subscription verified from Meta's side, not just ours
      (`/api/instagram/{id}/subscription`)
- [ ] The Instagram account has accepted its tester invite
- [ ] You are within Standard Access — this deployment serves only accounts you
      own. Serving anyone else's account requires App Review and Business
      Verification

## Known gaps you are accepting

See `docs/security.md` § Known gaps. The two that matter most if this ever grows
beyond one operator: MongoDB has no authentication by default, and the
deauthorize / data-deletion callbacks acknowledge without acting.
