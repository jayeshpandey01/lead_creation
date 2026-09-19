import email
import imaplib
import logging
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

from .settings import settings

logger = logging.getLogger(__name__)


def build_footer() -> str:
    company_part = f" | {settings.sender_company}" if settings.sender_company else ""
    return (
        f"\n\n--\n{settings.sender_name}{company_part}\n"
        f"{settings.sender_address}\n"
        "If you'd rather not hear from me again, just reply STOP and I won't follow up."
    )


def send_email(to_email: str, subject: str, body: str) -> str:
    """Sends via SMTP using the configured mailbox. Returns the Message-ID we
    generated, so replies can be matched to it later via IMAP."""
    message_id = make_msgid()
    msg = MIMEText(body.rstrip() + build_footer())
    msg["To"] = to_email
    msg["From"] = formataddr((settings.sender_name, settings.smtp_from_email))
    msg["Subject"] = subject
    msg["Message-ID"] = message_id

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        if settings.smtp_use_tls:
            server.starttls()
        server.login(settings.smtp_from_email, settings.smtp_password)
        server.send_message(msg)

    return message_id


def _imap_connect() -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL(settings.imap_host)
    imap.login(settings.smtp_from_email, settings.smtp_password)
    imap.select("INBOX")
    return imap


def _fetch_text(imap: imaplib.IMAP4_SSL, uid: bytes) -> bytes:
    typ, msg_data = imap.fetch(uid, "(RFC822)")
    if typ != "OK" or not msg_data or not msg_data[0]:
        return b""
    return msg_data[0][1]


def check_reply(message_id: str) -> tuple[bool, bool]:
    """Returns (has_reply, is_stop) for a sent Message-ID, by searching the
    inbox for a message that references it (standard In-Reply-To/References
    headers — works with any IMAP provider, not just Gmail)."""
    imap = _imap_connect()
    try:
        typ, data = imap.search(None, f'(HEADER In-Reply-To "{message_id}")')
        uids = data[0].split() if data and data[0] else []
        if not uids:
            typ, data = imap.search(None, f'(HEADER References "{message_id}")')
            uids = data[0].split() if data and data[0] else []
        if not uids:
            return False, False

        raw = _fetch_text(imap, uids[-1])
        parsed = email.message_from_bytes(raw)
        text = ""
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        text += payload.decode(errors="ignore")
        else:
            payload = parsed.get_payload(decode=True)
            if payload:
                text = payload.decode(errors="ignore")

        is_stop = "stop" in text.lower().split()
        return True, is_stop
    finally:
        imap.logout()


def check_bounce(to_email: str) -> bool:
    """Best-effort bounce detection: looks for a recent mailer-daemon message
    mentioning this recipient's address anywhere in the raw message."""
    imap = _imap_connect()
    try:
        typ, data = imap.search(None, '(FROM "mailer-daemon")')
        uids = data[0].split() if data and data[0] else []
        needle = to_email.lower().encode()
        for uid in uids[-20:]:
            raw = _fetch_text(imap, uid)
            if needle in raw.lower():
                return True
        return False
    finally:
        imap.logout()
