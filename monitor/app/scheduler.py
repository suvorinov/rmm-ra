"""Планировщик: инвентарь, снимки, очистка."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .collector import CollectorContext, run_cleanup, run_inventory, run_snapshots

log = logging.getLogger(__name__)


def build_scheduler(ctx: CollectorContext) -> BackgroundScheduler | None:
    settings = ctx.settings
    if not settings.scheduler_enabled:
        log.warning("планировщик отключён (MON_SCHEDULER_ENABLED=false)")
        return None

    tz = ZoneInfo(settings.tz)
    scheduler = BackgroundScheduler(
        timezone=tz,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": max(60, settings.snapshot_interval),
        },
    )
    now = datetime.now(tz)

    scheduler.add_job(
        run_inventory,
        "interval",
        seconds=settings.inventory_interval,
        args=[ctx],
        id="inventory",
        name="inventory",
        next_run_time=now,
    )
    scheduler.add_job(
        run_snapshots,
        "interval",
        seconds=settings.snapshot_interval,
        args=[ctx],
        id="snapshots",
        name="snapshots",
        next_run_time=now + timedelta(seconds=20),
    )
    scheduler.add_job(
        run_cleanup,
        CronTrigger(hour=3, minute=30, timezone=tz),
        args=[ctx],
        id="cleanup",
        name="cleanup",
    )
    log.info(
        "планировщик: инвентарь %s с, снимки %s с, очистка 03:30 %s",
        settings.inventory_interval,
        settings.snapshot_interval,
        settings.tz,
    )
    return scheduler
