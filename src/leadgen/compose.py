import json
import logging
from pathlib import Path

import yaml

from .db import get_session
from .models import Lead, LeadStatus
from .settings import settings
from .spamcheck import spam_score_issues

logger = logging.getLogger(__name__)

_POSITIONING_PATH = Path(__file__).resolve().parents[2] / "config" / "positioning.yaml"

_SYSTEM_PROMPT_TEMPLATE = """You are {sender_name}, a {sender_title} at {sender_company}, writing a SHORT, casual cold email to a prospect.

Rules:
- Under 120 words.
- One specific observation about their company, drawn only from the research brief given below — never invent facts.
- Reference exactly ONE proof point from the list below, whichever is most relevant to this prospect. Do not list several.
- Soft, low-friction call to action ({cta}).
- Plain, human tone. No marketing jargon, no exclamation points, no bold claims, at most one link total.
- Vary your opening line — do not default to "I noticed that" or "I came across".
- If no contact name is given, address the company/team generally (e.g. "Hi there," or "Hi {{company}} team,") — never invent a person's name.
- Output ONLY valid JSON of the form {{"subject": "...", "body": "..."}}. No markdown fences, no commentary.

Available proof points:
{proof_points}
"""


def _get_llm_client():
    from trainiq import cmddllm

    return cmddllm(api_key=settings.trainiq_api_key)


def _load_positioning() -> dict:
    with open(_POSITIONING_PATH) as f:
        return yaml.safe_load(f)


def _format_proof_points(items: list[dict]) -> str:
    return "\n".join(f"- {p['name']}: {p['pitch'].strip()} (best for: {p['best_for']})" for p in items)


def compose_email(lead: Lead, positioning: dict) -> tuple[str, str]:
    sender = positioning["sender"]
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        sender_name=sender["name"],
        sender_title=sender["title"],
        sender_company=sender["company"],
        cta=sender["calendly_or_reply_cta"],
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

    client = _get_llm_client()
    response = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.9,
    )
    content = response.choices[0].message.content.strip()
    content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(content)
    return data["subject"], data["body"]


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
                    subject, body = compose_email(lead, positioning)
                except Exception:
                    logger.exception("Compose failed for %s (attempt %d)", lead.email, attempt)
                    continue

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
