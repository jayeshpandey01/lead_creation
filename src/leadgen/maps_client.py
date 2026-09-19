"""Thin client for gosom/google-maps-scraper's REST API (run as its own
service — see the `maps-scraper` entry in render.yaml). No API key: it
scrapes Google Maps directly. Used to turn a plain-text search query like
"AI startup in Bangalore" into a list of candidate companies (name,
website, category, phone, and sometimes an email if Maps lists one).
"""
import logging
import time

import requests

from .settings import settings

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 5
_POLL_TIMEOUT_SECONDS = 600
_DONE_STATUSES = {"ok", "done", "completed", "finished"}
_FAILED_STATUSES = {"failed", "error"}


def _base_url() -> str:
    return settings.maps_scraper_url.rstrip("/")


def create_job(query: str, depth: int = 1, lang: str = "en") -> str:
    resp = requests.post(
        f"{_base_url()}/api/v1/jobs",
        json={"input": query, "depth": depth, "lang": lang},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    job_id = data.get("id") or data.get("job_id")
    if not job_id:
        raise RuntimeError(f"maps-scraper did not return a job id: {data}")
    return job_id


def _job_status(job_id: str) -> dict:
    resp = requests.get(f"{_base_url()}/api/v1/jobs/{job_id}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def wait_for_job(job_id: str) -> bool:
    """Polls until the job finishes. Returns True if it completed
    successfully, False if it failed or timed out."""
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = str(_job_status(job_id).get("status", "")).lower()
        if status in _DONE_STATUSES:
            return True
        if status in _FAILED_STATUSES:
            logger.warning("maps-scraper job %s failed (status=%s)", job_id, status)
            return False
        time.sleep(_POLL_INTERVAL_SECONDS)
    logger.warning("maps-scraper job %s timed out after %ds", job_id, _POLL_TIMEOUT_SECONDS)
    return False


def download_results(job_id: str) -> list[dict]:
    """Downloads the job's CSV results and returns them as a list of dicts.
    Column names are matched case-insensitively since the exact header
    casing isn't pinned down in the tool's docs."""
    resp = requests.get(f"{_base_url()}/api/v1/jobs/{job_id}/download", timeout=60)
    resp.raise_for_status()

    import csv
    import io

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = []
    for row in reader:
        normalized = {k.strip().lower(): v for k, v in row.items() if k}
        rows.append(normalized)
    return rows


def run_query(query: str) -> list[dict]:
    """End-to-end: create a job for one search query, wait for it, return
    its result rows. Returns an empty list on any failure (logged, not
    raised) so one bad query doesn't stop the rest of the pipeline."""
    try:
        job_id = create_job(query)
    except Exception:
        logger.exception("Failed to create maps-scraper job for query %r", query)
        return []

    if not wait_for_job(job_id):
        return []

    try:
        return download_results(job_id)
    except Exception:
        logger.exception("Failed to download maps-scraper results for job %s", job_id)
        return []
