import asyncio

from app.core.observability import init_observability
from app.core.logging_config import get_logger
from app.agents.scheduler import build_scheduler

logger = get_logger(__name__)


async def main() -> None:
    """
    Entrypoint for Process One (Intelligence Pipeline). Initializes
    observability, builds and starts the scheduler (5 source agents,
    batching agent, sender worker, weekly cleanup), then keeps the
    process alive forever.
    """
    init_observability(service_name="intelligence-pipeline")

    scheduler = build_scheduler()
    scheduler.start()

    logger.info("Intelligence pipeline started - scheduler running")

    # AsyncIOScheduler runs on the same event loop we're already in -
    # this loop just needs to never exit for the process to keep
    # running the scheduled jobs indefinitely.
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
