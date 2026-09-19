"""Free discovery path: gosom/google-maps-scraper (company source) + a local
SMTP verifier for resolving an email per company. No paid account needed.
Always-on baseline discovery source — see discover.py for how this combines
with discover_apollo.py."""
import logging
import re
from urllib.parse import urlparse

from . import maps_client
from .db import get_session
from .email_verify import find_best_generic_email
from .models import Lead, LeadStatus
from .settings import settings

logger = logging.getLogger(__name__)


def _load_queries() -> list[str]:
    try:
        with open(settings.queries_file) as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except FileNotFoundError:
        logger.warning("Queries file %s not found, skipping Maps discovery this run", settings.queries_file)
        return []


def _domain_from_website(website: str) -> str | None:
    if not website:
        return None
    if not website.startswith(("http://", "https://")):
        website = "http://" + website
    host = urlparse(website).netloc
    return re.sub(r"^www\.", "", host).lower() or None


def _resolve_email(row: dict, domain: str | None) -> str | None:
    existing = (row.get("email") or "").strip()
    if existing and "@" in existing:
        return existing
    if not domain:
        return None
    return find_best_generic_email(domain, settings.generic_email_prefixes)


def run_discovery_maps(count: int) -> int:
    """Runs each query in settings.queries_file against the maps-scraper
    service, resolves an email per result (Maps' own listing if present,
    otherwise a verified generic address on the company's domain), and
    inserts up to `count` new leads. Dedupes by email and by company name."""
    if count <= 0:
        return 0

    queries = _load_queries()
    if not queries:
        return 0

    session = get_session()
    inserted = 0
    try:
        for query in queries:
            if inserted >= count:
                break

            rows = maps_client.run_query(query)
            for row in rows:
                if inserted >= count:
                    break

                company = (row.get("name") or row.get("title") or "").strip()
                website = (row.get("website") or "").strip()
                if not company:
                    continue

                domain = _domain_from_website(website)
                email = _resolve_email(row, domain)
                if not email:
                    continue

                exists = (
                    session.query(Lead)
                    .filter((Lead.email == email) | (Lead.company == company))
                    .first()
                )
                if exists:
                    continue

                session.add(
                    Lead(
                        email=email,
                        company=company,
                        website=website or None,
                        qualification_reason=(
                            f"Found via Google Maps search: \"{query}\" "
                            f"(category: {row.get('category') or 'n/a'})"
                        ),
                        status=LeadStatus.discovered,
                    )
                )
                inserted += 1
        session.commit()
    finally:
        session.close()

    logger.info("Maps discovery inserted %d new leads", inserted)
    return inserted
