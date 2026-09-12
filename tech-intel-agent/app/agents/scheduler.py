from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from app.agents.github_agent import run_github_agent
from app.agents.hackernews_agent import run_hackernews_agent
from app.agents.arxiv_agent import run_arxiv_agent
from app.agents.blogs_agent import run_blogs_agent
from app.agents.rss_agent import run_rss_agent
from app.processor.batching_agent import run_batching_agent
from app.sender.twilio_sender import run_sender_worker
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

    # Cadences below are set by free-tier LLM rate limits, not by how
    # often each source could theoretically be polled.
    scheduler.add_job(run_github_agent, IntervalTrigger(hours=4), id="github_agent", max_instances=1)
    # Hourly rather than every 15 minutes. The HackerNews front page
    # does not turn over on a 15-minute cycle, so the shorter interval
    # was re-examining a largely unchanged list - hourly loses almost
    # nothing in freshness for a quarter of the LLM calls.
    scheduler.add_job(run_hackernews_agent, IntervalTrigger(hours=1), id="hackernews_agent", max_instances=1)
    scheduler.add_job(run_arxiv_agent, CronTrigger(hour=8, minute=0, timezone=tz), id="arxiv_agent", max_instances=1)
    scheduler.add_job(run_blogs_agent, IntervalTrigger(hours=2), id="blogs_agent", max_instances=1)
    scheduler.add_job(run_rss_agent, CronTrigger(hour=9, minute=0, timezone=tz), id="rss_agent", max_instances=1)
    # Unchanged at 30 minutes: this one returns immediately at zero LLM
    # cost when nothing is unsent, so its frequency does not affect the
    # rate limits the other cadences are being cut for.
    scheduler.add_job(run_batching_agent, IntervalTrigger(minutes=30), id="batching_agent", max_instances=1)
    # Was every 20 seconds, which could not work: draining one batch to
    # 20 users with a 2-second gap between messages takes ~80 seconds -
    # four times the interval - so APScheduler spent its time emitting
    # "execution skipped, maximum instances reached" instead of
    # sending. 2 minutes clears a full batch with room to spare, and
    # still drains promptly after the batching agent pushes.
    scheduler.add_job(run_sender_worker, IntervalTrigger(minutes=2), id="sender_worker", max_instances=1)
    scheduler.add_job(
        run_cleanup_job,
        CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=tz),
        id="cleanup_job",
        max_instances=1,
    )

    logger.info(
        "Scheduler built with 8 jobs registered",
        extra={"extra_fields": {"cron_timezone": tz}},
    )
    return scheduler
