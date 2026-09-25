import json
import logging
from dataclasses import asdict

import requests
import trafilatura

from .audit import audit_website
from .db import get_session
from .llm_client import chat_completion
from .models import Lead, LeadStatus

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LeadResearchBot/1.0)"}

_SUMMARY_SYSTEM_PROMPT = (
    "Extract a concise research brief using only explicit facts in the supplied "
    "public page text. Never infer a company's pain, plans, customers, tech stack, "
    "or need for services. Return 1-3 short facts as bullets; each bullet must "
    "include an exact short quote from the page and its source URL. Do not "
    "paraphrase the quoted evidence into a stronger claim. If no clear, relevant "
    "fact is present, return exactly NO_VERIFIED_EVIDENCE."
)


def fetch_website_text(url: str | None, max_chars: int = 6000) -> str:
    if not url:
        return ""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        return (trafilatura.extract(downloaded) or "")[:max_chars]
    except Exception:
        logger.exception("Failed to fetch website %s", url)
        return ""


def fetch_linkedin_about(url: str | None, max_chars: int = 3000) -> str:
    """Best-effort only. LinkedIn requires login for most company page
    content, and scraping it while authenticated risks the account. This
    only reads whatever is publicly rendered without logging in (often just
    a meta description) and fails closed to nothing rather than escalate
    to authenticated scraping."""
    if not url:
        return ""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        if resp.status_code != 200:
            return ""
        return (trafilatura.extract(resp.text) or "")[:max_chars]
    except Exception:
        logger.exception("Failed to fetch LinkedIn page %s", url)
        return ""


def summarize(
    company: str,
    website_url: str | None,
    website_text: str,
    linkedin_url: str | None,
    linkedin_text: str,
) -> str:
    if not website_text and not linkedin_text:
        return "NO_VERIFIED_EVIDENCE"

    raw = (
        f"Company name (identifier only): {company}\n\n"
        f"Website source URL: {website_url or 'not available'}\n"
        f"Website page text:\n{website_text}\n\n"
        f"LinkedIn source URL: {linkedin_url or 'not available'}\n"
        f"Public LinkedIn page text:\n{linkedin_text}"
    )
    return chat_completion(
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": raw[:12000]},
        ],
        temperature=0.3,
    )


def run_research(limit: int = 50) -> int:
    session = get_session()
    processed = 0
    try:
        leads = (
            session.query(Lead)
            .filter(Lead.status == LeadStatus.discovered)
            .limit(limit)
            .all()
        )
        for lead in leads:
            if lead.website:
                try:
                    res = audit_website(
                        lead.website,
                        category=getattr(lead, "category", None),
                        rating=getattr(lead, "rating", None),
                        reviews_count=getattr(lead, "reviews_count", None),
                    )
                    lead.opportunity_score = res.opportunity_score
                    lead.recommended_service = res.recommended_service
                    lead.audit_data = json.dumps({
                        "top_gaps": res.top_gaps,
                        "details": asdict(res.details),
                    })
                except Exception:
                    logger.warning("Audit failed for %s (%s)", lead.email, lead.website)

            website_text = fetch_website_text(lead.website)
            linkedin_text = fetch_linkedin_about(lead.linkedin_url)
            try:
                lead.research_brief = summarize(
                    lead.company or "",
                    lead.website,
                    website_text,
                    lead.linkedin_url,
                    linkedin_text,
                )
            except Exception:
                logger.exception("Summarization failed for %s, skipping this cycle", lead.email)
                continue
            lead.status = LeadStatus.researched
            session.add(lead)
            session.commit()
            processed += 1
            logger.info("Researched lead %s (%s) - Score: %s", lead.email, lead.company, lead.opportunity_score)
    finally:
        session.close()
    return processed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_research()
