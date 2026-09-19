"""Orchestrates all discovery sources. Currently two:
  - discover_maps: gosom/google-maps-scraper + local SMTP verify (free, always-on)
  - discover_apollo: Apollo.io API (no-op until APOLLO_API_KEY is on a paid
    plan with API access — see discover_apollo.py)
Both dedupe against the same `leads` table independently, so running both is
safe even if they occasionally find the same company/email.
"""
import logging

from .db import init_db
from .discover_apollo import run_discovery_apollo
from .discover_maps import run_discovery_maps

logger = logging.getLogger(__name__)


def run_discovery(count: int = 10) -> int:
    init_db()

    inserted = run_discovery_maps(count)
    remaining = count - inserted
    if remaining > 0:
        inserted += run_discovery_apollo(remaining)

    logger.info("Discovery run inserted %d new leads total", inserted)
    return inserted


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO)
    run_discovery(count=int(os.environ.get("DISCOVERY_BATCH_SIZE", "10")))
