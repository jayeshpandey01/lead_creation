"""Export the current lead database to a reviewable CSV snapshot."""
import csv
import logging
import os
import tempfile
from pathlib import Path

from .db import get_session
from .models import Lead
from .settings import settings

logger = logging.getLogger(__name__)

_FIELDS = (
    "id",
    "email",
    "first_name",
    "last_name",
    "company",
    "title",
    "website",
    "linkedin_url",
    "qualification_reason",
    "research_brief",
    "email_subject",
    "email_body",
    "status",
    "discovered_at",
    "sent_at",
)


def export_leads_csv() -> int:
    """Write all leads atomically; return the number of exported rows."""
    destination = Path(settings.leads_export_csv_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    session = get_session()
    temp_path = None
    try:
        leads = session.query(Lead).order_by(Lead.id.asc()).all()
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8-sig",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temp_path = output.name
            writer = csv.DictWriter(output, fieldnames=_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for lead in leads:
                writer.writerow({
                    field: getattr(lead, field).value if field == "status" else getattr(lead, field)
                    for field in _FIELDS
                })
        os.replace(temp_path, destination)
        logger.info("Exported %d leads to %s", len(leads), destination)
        return len(leads)
    finally:
        session.close()
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
