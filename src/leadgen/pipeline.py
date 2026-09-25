"""One-shot scheduled discover -> research -> compose -> optional send run.

Sending is disabled unless ENABLE_EMAIL_SENDING=true.
"""
import logging
import os
import asyncio

from .compose import run_compose
from .discover import run_discovery
from .export_csv import export_leads_csv
from .research import run_research
from .sender import send_ready_once
from .settings import settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    batch_size = int(os.environ.get("DISCOVERY_BATCH_SIZE", "10"))

    inserted = run_discovery(count=batch_size)
    logging.info("discover: %d new leads", inserted)

    researched = run_research()
    logging.info("research: %d leads researched", researched)

    composed = run_compose()
    logging.info("compose: %d emails drafted", composed)
    sent = asyncio.run(send_ready_once(limit=settings.job_send_limit))
    logging.info("send: %d emails sent", sent)
    export_leads_csv()


if __name__ == "__main__":
    main()
