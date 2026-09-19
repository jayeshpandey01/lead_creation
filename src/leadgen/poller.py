import asyncio
import logging

from .db import get_session
from .mail_client import check_bounce, check_reply
from .models import Lead, LeadStatus
from .settings import settings

logger = logging.getLogger(__name__)


async def poll_once() -> None:
    session = get_session()
    try:
        sent_leads = (
            session.query(Lead)
            .filter(Lead.status == LeadStatus.sent, Lead.message_id.isnot(None))
            .all()
        )
        for lead in sent_leads:
            try:
                has_reply, is_stop = await asyncio.to_thread(check_reply, lead.message_id)
            except Exception:
                logger.exception("Reply check failed for lead %s", lead.email)
                continue

            if has_reply:
                lead.status = LeadStatus.unsubscribed if is_stop else LeadStatus.replied
                session.add(lead)
                session.commit()
                logger.info("Lead %s -> %s", lead.email, lead.status)
                continue

            try:
                if await asyncio.to_thread(check_bounce, lead.email):
                    lead.status = LeadStatus.bounced
                    session.add(lead)
                    session.commit()
                    logger.info("Lead %s -> bounced", lead.email)
            except Exception:
                logger.exception("Bounce check failed for lead %s", lead.email)
    finally:
        session.close()


async def poller_loop() -> None:
    while True:
        await poll_once()
        await asyncio.sleep(settings.poll_interval_seconds)
