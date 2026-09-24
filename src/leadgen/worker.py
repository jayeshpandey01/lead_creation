import asyncio
import logging

from .compose import run_compose
from .discover import run_discovery
from .export_csv import export_leads_csv
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
    await asyncio.to_thread(export_leads_csv)
    result = {"discovered": inserted, "researched": researched, "composed": composed}
    logger.info("Pipeline run complete: %s", result)
    return result


async def pipeline_loop() -> None:
    """Runs immediately and then targets the configured start-to-start cadence.

    A scrape can take up to the scraper's max_time, so the next run starts as
    soon as the current run finishes if it exceeded the configured interval.
    """
    while True:
        started = asyncio.get_running_loop().time()
        try:
            await run_pipeline_once()
        except Exception:
            logger.exception("Pipeline run failed; retrying on the next interval")
        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(0, settings.pipeline_interval_seconds - elapsed))


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
