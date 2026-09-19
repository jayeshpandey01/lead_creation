import logging

import requests
import trafilatura

from .db import get_session
from .models import Lead, LeadStatus
from .settings import settings

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LeadResearchBot/1.0)"}

_SUMMARY_SYSTEM_PROMPT = (
    "You are a B2B sales research assistant. Given raw scraped text about a "
    "company, produce a concise research brief for a salesperson: 4-6 short "
    "bullet points covering (1) what the company does, (2) their likely "
    "pain point relevant to AI/ML, data, or engineering services, and (3) "
    "one concrete, specific detail from the text that could open a "
    "personalized email. No preamble, just the bullets."
)


def _get_llm_client():
    from trainiq import cmddllm

    return cmddllm(api_key=settings.trainiq_api_key)


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


def summarize(company: str, website_text: str, linkedin_text: str) -> str:
    if not website_text and not linkedin_text:
        return f"No public research material found for {company}. Personalize using company name and title only."

    raw = f"Company: {company}\n\nWebsite content:\n{website_text}\n\nLinkedIn content:\n{linkedin_text}"
    client = _get_llm_client()
    response = client.chat.completions.create(
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": raw[:12000]},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


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
            website_text = fetch_website_text(lead.website)
            linkedin_text = fetch_linkedin_about(lead.linkedin_url)
            try:
                lead.research_brief = summarize(lead.company or "", website_text, linkedin_text)
            except Exception:
                logger.exception("Summarization failed for %s, skipping this cycle", lead.email)
                continue
            lead.status = LeadStatus.researched
            session.add(lead)
            session.commit()
            processed += 1
            logger.info("Researched lead %s (%s)", lead.email, lead.company)
    finally:
        session.close()
    return processed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_research()
