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

    scheduler.add_job(run_github_agent, IntervalTrigger(hours=2), id="github_agent", max_instances=1)
    scheduler.add_job(run_hackernews_agent, IntervalTrigger(minutes=15), id="hackernews_agent", max_instances=1)
    scheduler.add_job(run_arxiv_agent, CronTrigger(hour=8, minute=0), id="arxiv_agent", max_instances=1)
    scheduler.add_job(run_blogs_agent, IntervalTrigger(minutes=30), id="blogs_agent", max_instances=1)
    scheduler.add_job(run_rss_agent, CronTrigger(hour=9, minute=0), id="rss_agent", max_instances=1)
    scheduler.add_job(run_batching_agent, IntervalTrigger(minutes=30), id="batching_agent", max_instances=1)
    # Short interval - promptly drains anything the batching agent
    # just pushed, without needing its own separate always-on loop.
    # A cheap no-op (single Redis check) when the queue is empty.
    scheduler.add_job(run_sender_worker, IntervalTrigger(seconds=20), id="sender_worker", max_instances=1)
    scheduler.add_job(
        run_cleanup_job, CronTrigger(day_of_week="sun", hour=2, minute=0), id="cleanup_job", max_instances=1
    )

    logger.info("Scheduler built with 8 jobs registered")
    return scheduler
