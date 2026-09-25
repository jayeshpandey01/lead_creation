import json
import logging
import re
from pathlib import Path

import yaml

from .db import get_session
from .llm_client import chat_completion
from .models import Lead, LeadStatus
from .settings import settings
from .spamcheck import spam_score_issues

logger = logging.getLogger(__name__)

_POSITIONING_PATH = Path(__file__).resolve().parents[2] / "config" / "positioning.yaml"

_SYSTEM_PROMPT_TEMPLATE = """You are {sender_name}, {sender_title} at {sender_company}, writing a short, specific cold outreach email on behalf of Copys.

Rules:
- Use 90 words or fewer. Direct, conversational, respectful tone. Do not add filler praise or generic sales language.
- Only mention prospect details stated in the research brief. Do not invent unverified claims.
- Select an EXACT short phrase (3 to 8 words) from the research brief. That exact phrase must appear VERBATIM inside your email body (in quotes or naturally in the sentence). Set `evidence_quote` to that EXACT phrase.
- Copys builds AI agents, full-stack web systems, and mobile apps. Connect what they do to how Copys can collaborate (e.g. as an engineering partner for custom AI agents/automations, full-stack web systems, or mobile apps).
- You may use at most one personal `email_claim` below, exactly as written and with its role/project attribution intact. Set `proof_point` to its exact name. If none fits, use `proof_point`: "none" and emphasize Copys' core studio builds (web platforms, mobile apps, or AI automation).
- Keep the distinction between the sender's own work at PGAGI, EaseMeMed, PRL/ISRO, BTechNotes, or a research project and work delivered by Copys. Never imply those organizations are Copys clients.
- Use a low-pressure call to action ({cta}). No exclamation marks, fabricated personalization, invented contact names, guarantees, or unsupported metrics.
- Return ONLY valid JSON with keys `subject`, `body`, `evidence_quote`, and `proof_point`. No Markdown fences or commentary.

Copys portfolio:
{portfolio_url}
Portfolio-described offer: {company_offer}

Approved personal claims (use at most one, verbatim):
{proof_points}
"""


def _load_positioning() -> dict:
    with open(_POSITIONING_PATH) as f:
        return yaml.safe_load(f)


def _format_proof_points(items: list[dict]) -> str:
    return "\n".join(
        f"- name: {p['name']}\n  exact email_claim: {p['email_claim']}\n  context: {p['pitch'].strip()}\n  use only when: {p['best_for']}"
        for p in items
    )


def compose_email(lead: Lead, positioning: dict) -> tuple[str, str] | None:
    if not lead.research_brief or "NO_VERIFIED_EVIDENCE" in lead.research_brief:
        return None

    sender = positioning["sender"]
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        sender_name=sender["name"],
        sender_title=sender["title"],
        sender_company=sender["company"],
        cta=sender["calendly_or_reply_cta"],
        portfolio_url=sender["portfolio_url"],
        company_offer=sender["company_offer"],
        proof_points=_format_proof_points(positioning["proof_points"]),
    )
    if lead.first_name:
        who = f"{lead.first_name} {lead.last_name or ''}".strip()
        if lead.title:
            who += f", {lead.title}"
        who += f" at {lead.company or 'their company'}"
    else:
        who = f"No named contact — company: {lead.company or 'unknown'} (address the team generally)"

    user_prompt = (
        f"Prospect: {who}.\n\n"
        f"Research brief:\n{lead.research_brief}\n\n"
        f"Why they were qualified: {lead.qualification_reason or 'n/a'}"
    )

    content = chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
    )
    content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(content)
    if data.get("skip") is True:
        return None

    subject = data.get("subject", "").strip()
    body = data.get("body", "").strip()
    evidence_quote = data.get("evidence_quote", "").strip()
    proof_name = data.get("proof_point", "none")
    allowed = {point["name"]: point["email_claim"] for point in positioning["proof_points"]}

    if not subject or not body:
        raise ValueError("Draft must include a subject and body")
    if len(evidence_quote) < 8 or evidence_quote.lower() not in (lead.research_brief or "").lower():
        raise ValueError("Draft evidence quote is not present in the verified research brief")
    if evidence_quote.lower() not in body.lower():
        raise ValueError("Draft body must include its verified evidence quote verbatim")
    if proof_name != "none" and proof_name not in allowed:
        proof_name = "none"
    if len(body.split()) > 90:
        raise ValueError("Draft body exceeds 90 words")
    if len(re.findall(r"https?://", body, flags=re.IGNORECASE)) > 1:
        raise ValueError("Draft body contains more than one link")

    approved_text = (
        evidence_quote
        + " "
        + (allowed.get(proof_name, "") if proof_name != "none" else "")
        + " "
        + positioning.get("sender", {}).get("company_offer", "")
    )
    numbers_in_body = set(re.findall(r"\d+(?:\.\d+)?%?", subject + " " + body))
    numbers_approved = set(re.findall(r"\d+(?:\.\d+)?%?", approved_text))
    if numbers_in_body - numbers_approved:
        raise ValueError("Draft contains a numeric claim not present in approved source text")
    return subject, body


def run_compose(limit: int = 50, max_attempts: int = 2) -> int:
    positioning = _load_positioning()
    session = get_session()
    composed = 0
    try:
        leads = (
            session.query(Lead)
            .filter(Lead.status == LeadStatus.researched)
            .limit(limit)
            .all()
        )
        for lead in leads:
            for attempt in range(max_attempts):
                try:
                    draft = compose_email(lead, positioning)
                except Exception:
                    logger.exception("Compose failed for %s (attempt %d)", lead.email, attempt)
                    continue

                if draft is None:
                    lead.status = LeadStatus.not_qualified
                    session.add(lead)
                    session.commit()
                    logger.info("Skipped draft for lead without a relevant verified fact")
                    break

                subject, body = draft

                issues = spam_score_issues(subject, body)
                if not issues:
                    lead.email_subject = subject
                    lead.email_body = body
                    lead.status = LeadStatus.ready_to_send
                    session.add(lead)
                    session.commit()
                    composed += 1
                    logger.info("Composed email for %s", lead.email)
                    break
                logger.warning("Rejected draft for %s: %s", lead.email, issues)
            else:
                logger.warning("Gave up composing for %s after %d attempts", lead.email, max_attempts)
    finally:
        session.close()
    return composed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_compose()
