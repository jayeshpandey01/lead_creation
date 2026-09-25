import asyncio
import logging
import random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .db import get_session, init_db
from .export_csv import export_leads_csv
from .mail_client import send_email
from .models import Lead, LeadStatus
from .settings import settings

logger = logging.getLogger(__name__)


def _within_sending_window(now: datetime) -> bool:
    if settings.sending_weekdays_only and now.weekday() >= 5:
        return False
    return settings.sending_hour_start <= now.hour < settings.sending_hour_end


def _sent_today_count(session) -> int:
    tz = ZoneInfo(settings.timezone)
    start_of_day = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        session.query(Lead)
        .filter(Lead.sent_at >= start_of_day)
        .count()
    )


def _sender_config_missing() -> list[str]:
    if settings.mail_provider == "resend":
        required = {
            "RESEND_API_KEY": settings.resend_api_key,
            "RESEND_FROM_EMAIL": settings.resend_from_email,
        }
    elif settings.mail_provider == "smtp":
        required = {
            "SMTP_FROM_EMAIL": settings.smtp_from_email,
            "SMTP_PASSWORD": settings.smtp_password,
        }
    else:
        return [f"MAIL_PROVIDER must be smtp or resend (got {settings.mail_provider!r})"]
    required.update({
        "SENDER_NAME": settings.sender_name,
        "SENDER_COMPANY": settings.sender_company,
        "SENDER_ADDRESS": settings.sender_address,
    })
    return [name for name, value in required.items() if not value.strip()]


def _pick_batch(session, size: int) -> list[Lead]:
    # Shuffle within a larger pool so send order isn't always oldest-first —
    # part of not looking like a mechanical queue drain.
    candidates = (
        session.query(Lead)
        .filter(Lead.status == LeadStatus.ready_to_send)
        .order_by(Lead.discovered_at.asc())
        .limit(max(size * 3, 1))
        .all()
    )
    random.shuffle(candidates)
    return candidates[:size]


async def _send_one(session, lead: Lead) -> None:
    try:
        message_id = await asyncio.to_thread(
            send_email,
            lead.email,
            lead.email_subject,
            lead.email_body,
            f"leadgen-outreach/{lead.id}" if settings.mail_provider == "resend" else None,
        )
    except Exception:
        logger.exception("Failed to send to %s, leaving as ready_to_send for retry", lead.email)
        return

    lead.status = LeadStatus.sent
    lead.sent_at = datetime.now(timezone.utc)
    lead.message_id = message_id
    session.add(lead)
    session.commit()
    export_leads_csv()
    logger.info("Sent to %s (%s)", lead.email, lead.company)


async def sender_loop() -> None:
    init_db()
    tz = ZoneInfo(settings.timezone)

    while True:
        if not settings.enable_email_sending:
            logger.info("Sender disabled; set ENABLE_EMAIL_SENDING=true to opt in")
            await asyncio.sleep(settings.sender_idle_poll_seconds)
            continue
        missing = _sender_config_missing()
        if missing:
            logger.error("Sender disabled; configure required settings: %s", ", ".join(missing))
            await asyncio.sleep(settings.sender_idle_poll_seconds)
            continue

        now = datetime.now(tz)
        if not _within_sending_window(now):
            logger.info("Outside sending window at %s, sleeping 15 min", now.isoformat())
            await asyncio.sleep(15 * 60)
            continue

        session = get_session()
        try:
            sent_today = _sent_today_count(session)
            if sent_today >= settings.daily_cap:
                logger.info("Daily cap (%d) reached, sleeping 30 min", settings.daily_cap)
                await asyncio.sleep(30 * 60)
                continue

            batch_size = min(
                random.randint(settings.batch_min, settings.batch_max),
                settings.daily_cap - sent_today,
            )
            batch = _pick_batch(session, batch_size)
            if not batch:
                logger.info("No leads ready to send, checking again in %ds", settings.sender_idle_poll_seconds)
                await asyncio.sleep(settings.sender_idle_poll_seconds)
                continue

            for i, lead in enumerate(batch):
                await _send_one(session, lead)
                if i < len(batch) - 1:
                    gap = random.uniform(settings.inter_send_min_seconds, settings.inter_send_max_seconds)
                    await asyncio.sleep(gap)
        finally:
            session.close()

        interval = random.uniform(settings.interval_min_seconds, settings.interval_max_seconds)
        logger.info("Batch done, sleeping %.0fs until next batch", interval)
        await asyncio.sleep(interval)


async def send_ready_once(limit: int = 1) -> int:
    """Send a bounded number of ready drafts in a scheduled one-shot job."""
    if not settings.enable_email_sending:
        logger.info("Email sending disabled (ENABLE_EMAIL_SENDING=false)")
        return 0
    missing = _sender_config_missing()
    if missing:
        raise RuntimeError("Sender is missing required settings: " + ", ".join(missing))
    now = datetime.now(ZoneInfo(settings.timezone))
    if not _within_sending_window(now):
        logger.info("Outside sending window; skipping sends")
        return 0

    session = get_session()
    sent = 0
    try:
        remaining = max(0, settings.daily_cap - _sent_today_count(session))
        batch = _pick_batch(session, min(max(limit, 0), remaining))
        for lead in batch:
            await _send_one(session, lead)
            if lead.status == LeadStatus.sent:
                sent += 1
    finally:
        session.close()
    return sent
