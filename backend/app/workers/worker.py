"""ARQ worker entrypoint."""

import logging
from typing import Any

from arq import cron, run_worker
from arq.connections import RedisSettings
from arq.worker import func

from app.config import get_settings
from app.db import mongodb
from app.db.indexes import create_indexes
from app.logging import configure_logging
from app.services.ai.client import AiClient
from app.services.meta.factory import build_client
from app.workers.email_tasks import send_email
from app.workers.tasks import (
    MAX_ATTEMPTS,
    deliver_paused_backlog,
    ping,
    process_comment_event,
    process_message_event,
    process_postback_event,
    prune_media_cache,
    recheck_follow,
    refresh_expiring_tokens,
    refresh_geoip_database,
    resync_all_media,
    snapshot_account_metrics,
    sync_posts,
    sync_stories,
)

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, service="worker")

    database = await mongodb.connect(settings.mongo_url, settings.mongo_database)
    await create_indexes(database)

    ctx["settings"] = settings
    ctx["db"] = database
    ctx["client"] = build_client(settings)
    # Built only when enabled, so a misconfigured AI_BASE_URL cannot affect a
    # deployment that never asked for AI.
    ctx["ai_client"] = (
        AiClient(
            settings.ai_base_url, settings.ai_model, timeout_seconds=settings.ai_timeout_seconds
        )
        if settings.ai_enabled
        else None
    )

    logger.info(
        "worker_started",
        extra={"dry_run": settings.dry_run, "ai_enabled": settings.ai_enabled},
    )
    if settings.dry_run:
        logger.warning("dry_run_enabled_no_meta_mutations_will_be_sent")
        # Stated separately rather than by rewording the line above, which is
        # grepped in ops notes. Dry run is scoped to Meta: mail still goes out,
        # because an invitation that silently never arrives sends whoever
        # debugs it into the mail server instead of a flag.
        logger.warning("dry_run_does_not_apply_to_email_mail_will_still_be_sent")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Stop cleanly: ARQ finishes the in-flight job before this runs (§68)."""
    if ctx.get("ai_client") is not None:
        await ctx["ai_client"].aclose()
    await mongodb.disconnect()
    logger.info("worker_stopped")


class WorkerSettings:
    functions = [
        ping,
        process_comment_event,
        process_postback_event,
        process_message_event,
        recheck_follow,
        deliver_paused_backlog,
        refresh_expiring_tokens,
        refresh_geoip_database,
        snapshot_account_metrics,
        # Its own timeout: `job_timeout` below is class-wide at 60s, and this
        # job now downloads thumbnails as well as reading the media edge. A job
        # ARQ cancels raises nothing to classify and leaves no status behind, so
        # the cost of getting this wrong is a sync that fails invisibly.
        func(sync_posts, timeout=180),
        # Same reason: a story's image is fetched inline while it is still live.
        func(sync_stories, timeout=180),
        send_email,
        prune_media_cache,
        # Its own timeout, because `job_timeout` below is class-wide and 60s
        # cannot cover a full walk of the media edge -- 200 requests at worst.
        # `max_tries=1` because the walk is idempotent and operator-triggered:
        # the button is the retry, and five silent attempts would quadruple the
        # API cost while hiding the failure the Settings card exists to show.
        func(resync_all_media, timeout=600, max_tries=1),
    ]
    cron_jobs = [
        # Daily token check. Meta kills a token not refreshed within 60 days.
        cron(refresh_expiring_tokens, hour=3, minute=0),
        # Just after midnight UTC, so a snapshot lands squarely inside the day it
        # is labelled with. Meta keeps no follower history, so a day not written
        # here is a day of growth that can never be recovered (§2b).
        cron(snapshot_account_metrics, hour=0, minute=15),
        # GeoLite2 is rebuilt twice a week; Wednesday and Saturday keeps
        # it fresh without hammering the download limit.
        cron(refresh_geoip_database, weekday={2, 5}, hour=4, minute=30),
        # Hourly. A story lives 24 hours, and expiry is only visible by asking
        # Meta which stories are still active, so the resolution of this cron is
        # also the worst-case lag before a rule pinned to a dead story is
        # disabled. The endpoint runs it on demand too.
        cron(sync_stories, minute=5),
        # Every 15 minutes. This is what makes "apply to my next post" real:
        # without a scheduled sync a template would sit unapplied until someone
        # happened to press Refresh.
        cron(sync_posts, minute={0, 15, 30, 45}),
        # Daily, well away from the other crons. Nothing depends on it running
        # on time -- it only reclaims disk, and a day late costs nothing.
        cron(prune_media_cache, hour=4, minute=15),
    ]
    on_startup = startup
    on_shutdown = shutdown
    # Retry scheduling is explicit in the task (§64); this is the ceiling.
    max_tries = MAX_ATTEMPTS
    job_timeout = 60
    keep_result = 3600
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)


if __name__ == "__main__":
    run_worker(WorkerSettings)  # type: ignore[arg-type]
