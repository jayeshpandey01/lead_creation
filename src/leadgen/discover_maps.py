"""Free discovery path: a list of candidate companies (from either a local
CSV file or a live gosom/google-maps-scraper service, see _load_rows below)
+ a local SMTP verifier for resolving an email per company. No paid account
needed. Always-on baseline discovery source — see discover.py for how this
combines with discover_apollo.py."""
import csv
import logging
import os
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


def _load_rows_from_csv() -> list[dict] | None:
    """If MAPS_CSV_PATH exists AND has at least one data row, read candidate
    companies from it directly — no live service to host at all. Same
    column names as the scraper's own CSV output (name, website, category,
    phone, email), matched case-insensitively. Returns None when the file
    doesn't exist or is empty/header-only, so callers fall back to the live
    API instead of silently no-op'ing forever because of a leftover/starter
    CSV file."""
    if not os.path.exists(settings.maps_csv_path):
        return None
    with open(settings.maps_csv_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = [{k.strip().lower(): v for k, v in row.items() if k} for row in reader]
    return rows or None


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


def _iter_sources():
    """Yields (source_label, rows) pairs lazily, so the caller can stop
    early once it has enough leads without running unnecessary live-scraper
    jobs. Prefers a local CSV (settings.maps_csv_path) if one exists — no
    service to host at all. Falls back to querying the live maps-scraper
    API per line in settings.queries_file if no CSV is present."""
    csv_rows = _load_rows_from_csv()
    if csv_rows is not None:
        yield f"manual CSV ({settings.maps_csv_path})", csv_rows
        return

    for query in _load_queries():
        yield query, maps_client.run_query(query)


def run_discovery_maps(count: int) -> int:
    """Finds candidate companies (CSV or live scraper, see _iter_sources),
    resolves an email per result (source's own listing if present,
    otherwise a verified generic address on the company's domain), and
    inserts up to `count` new leads. Dedupes by email and by company name."""
    if count <= 0:
        return 0

    session = get_session()
    inserted = 0
    try:
        for source_label, rows in _iter_sources():
            if inserted >= count:
                break

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
                            f"Found via: \"{source_label}\" "
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
