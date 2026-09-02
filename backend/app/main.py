"""FastAPI application entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import queue
from app.api import analytics, auth, instagram, legal, links, rules, webhooks
from app.config import get_settings
from app.db import mongodb
from app.db.indexes import create_indexes
from app.errors import SocialFlowError
from app.logging import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, service="api")

    database = await mongodb.connect(settings.mongo_url, settings.mongo_database)
    await create_indexes(database)
    await queue.connect(settings.redis_url)

    missing = settings.missing_required()
    logger.info(
        "startup_complete",
        extra={
            "dry_run": settings.dry_run,
            "ai_enabled": settings.ai_enabled,
            "public_base_url": settings.public_base_url,
            "missing_config": missing,
        },
    )
    if settings.dry_run:
        logger.warning("dry_run_enabled_no_meta_mutations_will_be_sent")

    try:
        yield
    finally:
        # Graceful shutdown (§68): release both connections even if startup partly failed.
        await queue.disconnect()
        await mongodb.disconnect()


app = FastAPI(
    title="Social Flow",
    description="Self-hosted Instagram comment-to-private-reply automation",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(SocialFlowError)
async def social_flow_error_handler(request: Request, exc: SocialFlowError) -> JSONResponse:
    """Map application errors to HTTP (§53).

    Only `message` and `code` cross the boundary — never a stack trace or the
    internal context, which may name entities the caller should not learn about.
    """
    if exc.status_code >= 500:
        logger.exception("unhandled_application_error", extra={"code": exc.code})
    else:
        logger.info("application_error", extra={"code": exc.code, "path": request.url.path})
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.message}},
        status_code=exc.status_code,
    )


app.include_router(auth.router)
app.include_router(analytics.router)
app.include_router(rules.router)
app.include_router(rules.posts_router)
app.include_router(instagram.auth_router)
app.include_router(instagram.router)
app.include_router(webhooks.router)
app.include_router(links.router)
app.include_router(legal.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Process liveness. Never touches Mongo, Redis, or Meta (§67).

    `service` lets `make funnel` confirm it is about to expose Social Flow and not
    some other process that happens to hold the port.
    """
    return {"status": "ok", "service": "social-flow"}


@app.get("/ready", tags=["health"])
async def ready() -> JSONResponse:
    """Dependency readiness: MongoDB, Redis, and required configuration (§67)."""
    settings = get_settings()
    missing = settings.missing_required()

    checks: dict[str, Any] = {
        "mongodb": await mongodb.ping(),
        "redis": await queue.ping(),
        "config": not missing,
    }
    ready_now = all(bool(v) for v in checks.values())

    body: dict[str, Any] = {
        "status": "ready" if ready_now else "not_ready",
        "checks": checks,
        "dry_run": settings.dry_run,
    }
    if missing:
        body["missing_config"] = missing

    return JSONResponse(body, status_code=200 if ready_now else 503)
