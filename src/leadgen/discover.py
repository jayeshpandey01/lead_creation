"""Orchestrate local CSV / Google Maps scraper discovery.

Apollo is deliberately not part of the active pipeline. The CSV remains a
manual fallback, while the self-hosted Maps scraper is the automated source.
"""
import logging

from .db import init_db
from .discover_maps import run_discovery_maps

logger = logging.getLogger(__name__)


def run_discovery(count: int = 10) -> int:
    init_db()

    inserted = run_discovery_maps(count)

    logger.info("Discovery run inserted %d new leads total", inserted)
    return inserted


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO)
    run_discovery(count=int(os.environ.get("DISCOVERY_BATCH_SIZE", "10")))
