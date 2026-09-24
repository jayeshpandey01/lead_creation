"""One-shot discover -> research -> compose run; this command does not send.

The dashboard/worker runs sending separately after drafts reach ready_to_send.
"""
import logging
import os

from .compose import run_compose
from .discover import run_discovery
from .export_csv import export_leads_csv
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
    export_leads_csv()


if __name__ == "__main__":
    main()
