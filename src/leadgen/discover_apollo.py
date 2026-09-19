"""Apollo.io discovery path. Kept alongside discover_maps.py so this starts
contributing leads automatically the moment APOLLO_API_KEY is on a plan with
API access — Apollo's free plan returns a hard 403 on every API endpoint
(confirmed live, not just a low credit cap), so this is a no-op until then.
See discover.py for how this combines with the always-on Maps-based path."""
import logging

import requests

from .db import get_session
from .models import Lead, LeadStatus
from .settings import settings

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
_MATCH_URL = "https://api.apollo.io/api/v1/people/match"


def _headers() -> dict:
    return {"Content-Type": "application/json", "x-api-key": settings.apollo_api_key}


def _search_candidates(limit: int) -> list[dict]:
    """Free search — consumes no Apollo credits. Results have obfuscated
    names and no email/LinkedIn URL by design; each candidate must be
    enriched separately to reveal those."""
    payload: dict = {"page": 1, "per_page": min(max(limit, 1), 100)}
    if settings.apollo_person_titles:
        payload["person_titles"] = settings.apollo_person_titles
    if settings.apollo_employee_ranges:
        payload["organization_num_employees_ranges"] = settings.apollo_employee_ranges
    if settings.apollo_person_locations:
        payload["person_locations"] = settings.apollo_person_locations

    resp = requests.post(_SEARCH_URL, json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("people", [])


def _enrich_candidate(candidate: dict) -> dict | None:
    """Reveals the real email/LinkedIn URL for one candidate. Costs 1 Apollo
    credit if a match with email is found, 0 otherwise."""
    payload = {"id": candidate.get("id"), "reveal_personal_emails": True}
    resp = requests.post(_MATCH_URL, json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()
    person = resp.json().get("person")
    if not person or not person.get("email"):
        return None
    return person


def run_discovery_apollo(count: int) -> int:
    """Searches Apollo.io for candidates matching the configured ICP filters
    (APOLLO_PERSON_TITLES / APOLLO_EMPLOYEE_RANGES / APOLLO_PERSON_LOCATIONS),
    then enriches up to `count` of them to reveal a verified email (1 credit
    each — search itself is free). Dedupes against existing leads by email.
    No-op (returns 0) if APOLLO_API_KEY isn't set or the account has no API
    access."""
    if count <= 0:
        return 0

    if not settings.apollo_api_key:
        return 0

    try:
        candidates = _search_candidates(limit=count * 3)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 403:
            logger.info("Apollo API not accessible on current plan, skipping (see discover_apollo.py)")
        else:
            logger.exception("Apollo search failed")
        return 0
    except Exception:
        logger.exception("Apollo search failed")
        return 0

    session = get_session()
    inserted = 0
    try:
        for candidate in candidates:
            if inserted >= count:
                break

            try:
                person = _enrich_candidate(candidate)
            except Exception:
                logger.exception("Apollo enrichment failed for candidate %s", candidate.get("id"))
                continue
            if not person:
                continue

            email = person.get("email")
            if not email:
                continue

            exists = session.query(Lead).filter(Lead.email == email).first()
            if exists:
                continue

            org = person.get("organization") or {}
            session.add(
                Lead(
                    email=email,
                    first_name=person.get("first_name"),
                    last_name=person.get("last_name"),
                    company=org.get("name"),
                    title=person.get("title"),
                    website=org.get("website_url") or org.get("primary_domain"),
                    linkedin_url=person.get("linkedin_url"),
                    qualification_reason=(
                        f"Apollo match: titles={settings.apollo_person_titles or 'any'}, "
                        f"employee ranges={settings.apollo_employee_ranges or 'any'}, "
                        f"locations={settings.apollo_person_locations or 'any'}"
                    ),
                    status=LeadStatus.discovered,
                )
            )
            inserted += 1
        session.commit()
    finally:
        session.close()

    logger.info("Apollo discovery inserted %d new leads", inserted)
    return inserted
