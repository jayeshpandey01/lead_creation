"""One-shot discover -> research -> compose run, for local testing or manual triggering.

The production worker (worker.py) runs this same sequence on a daily timer
internally instead of via a separate cron job, since Render Cron Jobs can't
have a persistent disk attached and outfind needs one for its local state.
"""
import logging
import os

from .compose import run_compose
from .discover import run_discovery
from .research import run_research


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    batch_size = int(os.environ.get("DISCOVERY_BATCH_SIZE", "10"))

    inserted = run_discovery(count=batch_size)
    logging.info("discover: %d new leads", inserted)

    researched = run_research()
    logging.info("research: %d leads researched", researched)

    composed = run_compose()
    logging.info("compose: %d emails drafted", composed)


if __name__ == "__main__":
    main()
