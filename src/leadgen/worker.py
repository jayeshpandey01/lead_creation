import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .compose import run_compose
from .discover import run_discovery
from .poller import poller_loop
from .research import run_research
from .sender import sender_loop
from .settings import settings

logger = logging.getLogger(__name__)


async def run_pipeline_once() -> dict:
    """Runs discover -> research -> compose one time. Shared by the daily
    scheduled loop and the manual /trigger-pipeline dashboard endpoint."""
    logger.info("Running discover/research/compose pipeline")
    inserted = await asyncio.to_thread(run_discovery, settings.discovery_batch_size)
    researched = await asyncio.to_thread(run_research)
    composed = await asyncio.to_thread(run_compose)
    result = {"discovered": inserted, "researched": researched, "composed": composed}
    logger.info("Pipeline run complete: %s", result)
    return result


async def pipeline_loop() -> None:
    """Runs run_pipeline_once() once a day, at settings.pipeline_run_hour
    (sender's local time), to refill the ready_to_send queue."""
    tz = ZoneInfo(settings.timezone)
    while True:
        now = datetime.now(tz)
        next_run = now.replace(hour=settings.pipeline_run_hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())

        try:
            await run_pipeline_once()
        except Exception:
            logger.exception("Scheduled pipeline run failed, will retry tomorrow")


_background_tasks: list[asyncio.Task] = []


async def start_background_tasks() -> None:
    """Starts the sender/poller/pipeline loops as fire-and-forget tasks on
    the current event loop, used by dashboard.py so one process serves the
    dashboard AND runs outreach. Kept separate from main() so `python -m
    leadgen.worker` still works standalone (no dashboard) if ever needed."""
    _background_tasks.append(asyncio.create_task(sender_loop()))
    _background_tasks.append(asyncio.create_task(poller_loop()))
    _background_tasks.append(asyncio.create_task(pipeline_loop()))


async def main() -> None:
    await asyncio.gather(sender_loop(), poller_loop(), pipeline_loop())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
