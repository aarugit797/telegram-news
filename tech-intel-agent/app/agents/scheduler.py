from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from app.agents.github_agent import run_github_agent
from app.agents.hackernews_agent import run_hackernews_agent
from app.agents.arxiv_agent import run_arxiv_agent
from app.agents.blogs_agent import run_blogs_agent
from app.agents.rss_agent import run_rss_agent
from app.processor.batching_agent import run_breaking_check, run_digest
from app.sender.batch_sender import run_sender_worker
from app.db.cleanup_job import run_cleanup_job
from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def build_scheduler() -> AsyncIOScheduler:
    """
    Builds (does not start) the scheduler with all 5 source agents,
    the batching agent, the sender worker, and the weekly cleanup job
    registered on their designed cadence. Called once from
    pipeline_main.py.

    max_instances=1 on every job prevents a new run from starting
    while a previous run of the SAME job is still in progress - e.g.
    if the GitHub agent takes unusually long one run, we don't want
    a second overlapping run hitting the DB and GitHub's API at the
    same time as the first.
    """
    scheduler = AsyncIOScheduler()

    # Every CronTrigger below is anchored to this explicitly. A
    # CronTrigger with no timezone uses the SERVER's local time, so the
    # same code fires at different real-world times on a laptop than on
    # an EC2 box running UTC - a difference that never shows up in
    # testing and only appears once deployed. IntervalTriggers do not
    # need it: their cadence is relative, not wall-clock.
    tz = settings.scheduler_timezone

    # ---- TIER 1: DIGEST AGENTS ------------------------------------------
    # Polling frequency follows DELIVERY URGENCY, not how often a source
    # could theoretically be polled. Nothing from these four is ever worth
    # interrupting someone for, so they only need to have run before the
    # next digest is composed.
    #
    # STAGGERED, NOT SIMULTANEOUS - the timing decision. All agents share
    # ONE rate-limit budget (~5 RPM per Gemini credential), so firing
    # github and hackernews together at 08:00 makes them contend for the
    # same slots and each finishes later than it would alone. Measured: a
    # 30-call GitHub run took 212s, and a 6-item HackerNews run several
    # minutes, entirely because of pacing rather than work.
    #
    # 15 minutes apart means each agent has the budget to itself, and the
    # last one (rss at 08:45) still has 15 minutes of margin before the
    # 09:00 digest. Verifying a simultaneous burst fits would have meant
    # trusting a measurement taken under one particular quota state;
    # staggering removes the contention instead of budgeting for it.
    scheduler.add_job(
        run_github_agent,
        CronTrigger(hour="8,20", minute=0, timezone=tz),
        id="github_agent", max_instances=1,
    )
    scheduler.add_job(
        run_hackernews_agent,
        CronTrigger(hour="8,20", minute=15, timezone=tz),
        id="hackernews_agent", max_instances=1,
    )
    # Once daily - papers publish on a daily cycle, so an evening run
    # would re-examine the same listing.
    scheduler.add_job(
        run_arxiv_agent,
        CronTrigger(hour=8, minute=30, timezone=tz),
        id="arxiv_agent", max_instances=1,
    )
    # Once daily for the same reason - newsletters publish daily.
    scheduler.add_job(
        run_rss_agent,
        CronTrigger(hour=8, minute=45, timezone=tz),
        id="rss_agent", max_instances=1,
    )

    # ---- TIER 2: BREAKING AGENT -----------------------------------------
    # The only source producing genuinely interrupt-worthy news - model
    # releases and capability announcements - which is the entire reason
    # the BREAKING bypass exists.
    #
    # It writes EVERY approved signal to the database, not only urgent
    # ones. Urgency is an ADDITIONAL check applied to what already passed
    # the normal filter, so a non-urgent Anthropic post found at 2pm still
    # appears in the 9pm digest instead of being discarded and re-fetched.
    scheduler.add_job(
        run_blogs_agent,
        IntervalTrigger(minutes=settings.breaking_check_interval_minutes),
        id="blogs_agent", max_instances=1,
    )

    # ---- DELIVERY --------------------------------------------------------
    scheduler.add_job(
        run_digest,
        CronTrigger(
            hour=settings.digest_morning_hour,
            minute=settings.digest_morning_minute,
            timezone=tz,
        ),
        id="digest_morning", max_instances=1,
    )
    scheduler.add_job(
        run_digest,
        CronTrigger(
            hour=settings.digest_evening_hour,
            minute=settings.digest_evening_minute,
            timezone=tz,
        ),
        id="digest_evening", max_instances=1,
    )
    # Cheap by construction: one indexed SELECT and an immediate return
    # when no new blog signal has appeared, which is almost every run.
    scheduler.add_job(
        run_breaking_check,
        IntervalTrigger(minutes=settings.breaking_check_interval_minutes),
        id="breaking_check", max_instances=1,
    )
    # Was every 20 seconds, which could not work: draining one batch to
    # 20 users with a 2-second gap between messages takes ~80 seconds -
    # four times the interval - so APScheduler spent its time emitting
    # "execution skipped, maximum instances reached" instead of sending.
    scheduler.add_job(
        run_sender_worker,
        IntervalTrigger(minutes=2),
        id="sender_worker", max_instances=1,
    )
    scheduler.add_job(
        run_cleanup_job,
        CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=tz),
        id="cleanup_job", max_instances=1,
    )

    logger.info(
        "Scheduler built",
        extra={"extra_fields": {
            "cron_timezone": tz,
            "digest_times": [
                f"{settings.digest_morning_hour:02d}:{settings.digest_morning_minute:02d}",
                f"{settings.digest_evening_hour:02d}:{settings.digest_evening_minute:02d}",
            ],
        }},
    )
    return scheduler
