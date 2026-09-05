.PHONY: help up down reload logs build test lint format shell-api shell-worker backup restore db-preflight verify-backup \
        bootstrap-admin indexes funnel funnel-preflight funnel-off tunnel-status secrets

COMPOSE := docker compose
FUNNEL_PORT := $(shell grep -E '^HOST_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || echo 8090)

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

up: ## Start all services
	$(COMPOSE) up -d

reload: ## Recreate containers so .env and Caddyfile changes take effect
	$(COMPOSE) up -d
	@# Caddy reads its Caddyfile into memory once, at startup. The file is
	@# bind-mounted, so editing it changes nothing Compose can see -- `up -d`
	@# finds the container spec unchanged, leaves it running, and the edge keeps
	@# the old routing. Silently: nothing errors and `ps` says healthy.
	@#
	@# That cost a production incident. The /u/<slug>/ workspace routes were
	@# added to the Caddyfile, Caddy never picked them up, and every API call
	@# under a workspace prefix fell through to the SPA -- so login POSTed to
	@# nginx and came back "405 Not Allowed", pointing nowhere near the cause.
	@#
	@# Recreating it unconditionally costs about a second of edge downtime on a
	@# target that is already recreating the API. Detecting whether the file
	@# changed would be cheaper and is not worth the branch.
	$(COMPOSE) up -d --force-recreate caddy

down: ## Stop all services
	$(COMPOSE) down

logs: ## Follow logs from all services
	$(COMPOSE) logs -f

build: ## Rebuild all images
	$(COMPOSE) build

# Mount the source so the edit/test loop does not need an image rebuild. The
# image deliberately excludes tests/ (see backend/.dockerignore), so without
# this the test target runs a stale copy baked at build time -- or, once the
# image is rebuilt, finds no tests at all.
DEV_MOUNT := -v $(CURDIR)/backend:/app

test: ## Run the backend test suite
	$(COMPOSE) run --rm $(DEV_MOUNT) api pytest -q

lint: ## Lint and type-check the backend
	$(COMPOSE) run --rm $(DEV_MOUNT) api sh -c "ruff check . && mypy app"

format: ## Auto-format the backend
	$(COMPOSE) run --rm $(DEV_MOUNT) api ruff format .

shell-api: ## Shell into the api container
	$(COMPOSE) exec api sh

shell-worker: ## Shell into the worker container
	$(COMPOSE) exec worker sh

bootstrap-admin: ## Create the initial admin user
	$(COMPOSE) run --rm api python scripts/bootstrap_admin.py

indexes: ## Create/verify all MongoDB indexes
	$(COMPOSE) run --rm api python scripts/create_indexes.py

db-preflight: ## Verify MongoDB is actually up, not merely started
	@# `docker compose up -d` returns as soon as the container is created. It
	@# says nothing about whether the process inside survived, so a crash-looping
	@# mongod looks like success until something tries to use it.
	@if ! $(COMPOSE) ps --status running --services 2>/dev/null | grep -qx mongodb; then \
		echo "MongoDB is not running."; \
		echo; \
		$(COMPOSE) ps -a --format '  {{.Service}}  {{.Status}}' 2>/dev/null | grep mongodb || true; \
		echo; \
		echo "If it says 'Restarting', mongod is crash-looping. The reason is in:"; \
		echo "  docker compose logs mongodb --tail 40"; \
		echo; \
		echo "Most common causes on a new server:"; \
		echo "  'Illegal instruction' / signal=ILL  -> CPU has no AVX; MongoDB 5.0+ requires it"; \
		echo "  exit code 137 / OOMKilled           -> not enough memory"; \
		echo "  WiredTiger / unclean shutdown       -> data volume from a different MongoDB"; \
		echo; \
		echo "See docs/server-migration.md, Troubleshooting."; \
		exit 1; \
	fi
	@if ! $(COMPOSE) exec -T mongodb mongosh --quiet --eval 'db.adminCommand("ping").ok' >/dev/null 2>&1; then \
		echo "MongoDB is running but not answering. It may still be starting up."; \
		echo "  docker compose logs mongodb --tail 40"; \
		exit 1; \
	fi

backup: db-preflight ## Dump MongoDB and uploads to ./backups/ (run before moving servers)
	@mkdir -p backups
	@stamp=$$(date +%F-%H%M); \
	$(COMPOSE) exec -T mongodb mongodump --archive --db=$${MONGO_DATABASE:-instaflow} \
		> backups/mongo-$$stamp.archive; \
	tar czf backups/uploads-$$stamp.tar.gz uploads; \
	echo; \
	echo "  backups/mongo-$$stamp.archive   $$(du -h backups/mongo-$$stamp.archive | cut -f1)"; \
	echo "  backups/uploads-$$stamp.tar.gz  $$(du -h backups/uploads-$$stamp.tar.gz | cut -f1)"; \
	echo; \
	echo "A dump you have never restored is not a backup. Verify it:"; \
	echo "  make verify-backup ARCHIVE=backups/mongo-$$stamp.archive"

verify-backup: db-preflight ## Restore ARCHIVE into a scratch database and count it, without touching live data
	@test -n "$(ARCHIVE)" || { echo "Usage: make verify-backup ARCHIVE=backups/mongo-....archive"; exit 1; }
	@test -s "$(ARCHIVE)" || { echo "$(ARCHIVE) does not exist, or is empty."; exit 1; }
	@echo "Restoring into a scratch database. Live data is not touched."
	@$(COMPOSE) exec -T mongodb mongorestore --archive --drop --nsFrom='instaflow.*' --nsTo='backupcheck.*' < $(ARCHIVE) 2>&1 | tail -1
	@$(COMPOSE) exec -T mongodb mongosh backupcheck --quiet --eval 'print("");["users","rules","instagram_accounts","media","executions","comments"].forEach(c=>print("  "+c.padEnd(22)+db.getCollection(c).countDocuments({})))'
	@$(COMPOSE) exec -T mongodb mongosh backupcheck --quiet --eval 'db.dropDatabase()' > /dev/null
	@echo ""
	@echo "  scratch database dropped; live data untouched"

restore: db-preflight ## Restore ARCHIVE into the live database (DESTRUCTIVE — drops matching collections)
	@# Every check runs BEFORE the confirmation prompt. Asking someone to
	@# authorise a destructive action and only then discovering it cannot be
	@# performed teaches them to type past the prompt.
	@test -n "$(ARCHIVE)" || { echo "Usage: make restore ARCHIVE=backups/mongo-....archive"; exit 1; }
	@test -s "$(ARCHIVE)" || { echo "$(ARCHIVE) does not exist, or is empty."; exit 1; }
	@echo "This DROPS the current $${MONGO_DATABASE:-instaflow} collections and replaces them"
	@echo "with $(ARCHIVE) ($$(du -h $(ARCHIVE) | cut -f1))."
	@printf "Type the database name to confirm: "; read ans; \
	test "$$ans" = "$${MONGO_DATABASE:-instaflow}" || { echo "Aborted."; exit 1; }
	$(COMPOSE) exec -T mongodb mongorestore --archive --drop < $(ARCHIVE)
	@echo "Restored. Now: make reload"

secrets: ## Print freshly generated values for the .env secrets
	@echo "META_WEBHOOK_VERIFY_TOKEN=$$(openssl rand -hex 32)"
	@echo "ENCRYPTION_KEY=$$(openssl rand -hex 32)"
	@echo "SESSION_SECRET=$$(openssl rand -hex 32)"

funnel: funnel-preflight ## Expose Caddy publicly over Tailscale Funnel
	tailscale funnel --bg $(FUNNEL_PORT)
	@$(MAKE) --no-print-directory tunnel-status

funnel-preflight: ## Verify Social Flow -- not another local service -- holds the funnel port
	@# Funnel proxies to IPv4 127.0.0.1. Check that exact address, not
	@# localhost, which may resolve to ::1 and reach a different listener.
	@if curl -sf --max-time 5 http://127.0.0.1:$(FUNNEL_PORT)/health | grep -q '"service":"social-flow"'; then \
		echo "preflight ok: Social Flow is on 127.0.0.1:$(FUNNEL_PORT)"; \
	else \
		echo "REFUSING TO EXPOSE PORT $(FUNNEL_PORT)."; \
		echo "127.0.0.1:$(FUNNEL_PORT) is not Social Flow. It answered:"; \
		curl -s --max-time 5 http://127.0.0.1:$(FUNNEL_PORT)/health | head -c 300; echo; \
		echo "Run 'make up', or change HOST_PORT in .env if another service holds this port."; \
		exit 1; \
	fi

funnel-off: ## Stop the Tailscale Funnel
	tailscale funnel --https=443 off

tunnel-status: ## Print the public base URL
	@host=$$(tailscale status --json | python3 -c "import json,sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))"); \
	echo "PUBLIC_BASE_URL=https://$$host"; \
	echo "  redirect URI : https://$$host/api/auth/instagram/callback"; \
	echo "  webhook URL  : https://$$host/webhooks/instagram"
