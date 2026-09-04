.PHONY: help up down reload logs build test lint format shell-api shell-worker \
        bootstrap-admin indexes funnel funnel-preflight funnel-off tunnel-status secrets

COMPOSE := docker compose
FUNNEL_PORT := $(shell grep -E '^HOST_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || echo 8090)

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

up: ## Start all services
	$(COMPOSE) up -d

reload: ## Recreate containers so .env changes take effect (restart does NOT reload env_file)
	$(COMPOSE) up -d

down: ## Stop all services
	$(COMPOSE) down

logs: ## Follow logs from all services
	$(COMPOSE) logs -f

build: ## Rebuild all images
	$(COMPOSE) build

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
