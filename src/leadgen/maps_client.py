"""Thin client for gosom/google-maps-scraper's REST API (run alongside the
app in the Render container, or separately for local development). No API
key: it scrapes Google Maps directly and can extract website emails. Used to
turn a plain-text search query into candidate companies.
"""
import logging
import os
import time

import requests

from .settings import settings

logger = logging.getLogger(__name__)

if settings.maps_scraper_url != os.environ.get("MAPS_SCRAPER_URL", "http://localhost:8080").strip():
    logger.info(
        "Using bundled Maps scraper at %s (overriding retired MAPS_SCRAPER_URL)",
        settings.maps_scraper_url,
    )

_POLL_INTERVAL_SECONDS = 5
_POLL_TIMEOUT_SECONDS = 600
_MAX_CONSECUTIVE_POLL_ERRORS = 5
_JOB_MAX_TIME_SECONDS = 180
_DONE_STATUSES = {"ok", "done", "completed", "finished"}
_FAILED_STATUSES = {"failed", "error"}


def _base_url() -> str:
    url = settings.maps_scraper_url.rstrip("/")
    return url if "://" in url else f"http://{url}"


def is_available() -> bool:
    """Return whether the scraper API responds, without dumping a traceback."""
    try:
        response = requests.get(f"{_base_url()}/api/v1/jobs", timeout=5)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("Maps scraper unavailable at %s (%s)", _base_url(), exc)
        return False


def create_job(query: str, depth: int = 1, lang: str = "en") -> str:
    # The scraper API takes JobData fields at the top level. `input` is a CLI
    # concept, not an API field, and the API validates name/keywords/lang/
    # depth/max_time before accepting a job.
    resp = requests.post(
        f"{_base_url()}/api/v1/jobs",
        json={
            "name": query,
            "keywords": [query],
            "lang": lang,
            "depth": depth,
            "max_time": _JOB_MAX_TIME_SECONDS,
            "email": True,
            "zoom": 15,
            "radius": 10000,
        },
        timeout=30,
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        detail = resp.text.strip().replace("\n", " ")[:500]
        raise requests.HTTPError(f"{exc}; scraper response: {detail}", response=resp) from exc
    data = resp.json()
    job_id = data.get("id") or data.get("job_id") or data.get("ID") or data.get("JobID")
    if not job_id:
        raise RuntimeError(f"maps-scraper did not return a job id: {data}")
    return job_id


def _job_status(job_id: str) -> dict:
    resp = requests.get(f"{_base_url()}/api/v1/jobs/{job_id}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def wait_for_job(job_id: str) -> bool:
    """Polls until the job finishes. Returns True if it completed
    successfully, False if it failed, timed out, or errored repeatedly.

    A single query polls this every 5s for up to 10 minutes (~120 requests),
    so a transient network blip or upstream 5xx during that window is likely,
    not exceptional. Unlike create_job/download_results (whose callers wrap
    them in try/except), this loop used to let such an error propagate
    uncaught all the way out of run_discovery -- crashing whatever entry
    point called it and discarding any leads already found earlier in that
    run. It now treats a request failure the same way a "still running"
    status is treated: log it and keep polling, up to a small consecutive-
    failure cap so a genuinely dead service still gives up instead of
    spinning for the full timeout doing nothing useful."""
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    consecutive_errors = 0
    while time.monotonic() < deadline:
        try:
            status_data = _job_status(job_id)
            # gosom/google-maps-scraper currently serializes job detail keys
            # with uppercase names ("Status"), while some versions/proxies
            # return lowercase JSON fields. Accept either shape.
            status = str(status_data.get("status") or status_data.get("Status") or "").lower()
        except requests.RequestException as exc:
            consecutive_errors += 1
            logger.warning(
                "Transient error polling maps-scraper job %s (%d/%d): %s",
                job_id, consecutive_errors, _MAX_CONSECUTIVE_POLL_ERRORS, exc,
            )
            if consecutive_errors >= _MAX_CONSECUTIVE_POLL_ERRORS:
                logger.warning("Giving up on maps-scraper job %s after repeated poll errors", job_id)
                return False
            time.sleep(_POLL_INTERVAL_SECONDS)
            continue

        consecutive_errors = 0
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
    except requests.RequestException as exc:
        logger.warning("Could not start Maps query %r: %s", query, exc)
        return []
    except Exception as exc:
        logger.warning("Could not start Maps query %r: %s", query, exc)
        return []

    if not wait_for_job(job_id):
        return []

    try:
        return download_results(job_id)
    except Exception as exc:
        logger.warning("Could not download Maps results for job %s: %s", job_id, exc)
        return []
