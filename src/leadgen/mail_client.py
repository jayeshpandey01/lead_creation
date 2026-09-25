import email
import imaplib
import logging
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

import requests

from .settings import settings

logger = logging.getLogger(__name__)


def build_footer() -> str:
    company_part = f" | {settings.sender_company}" if settings.sender_company else ""
    lines = [f"{settings.sender_name}{company_part}"]
    if settings.sender_address:
        lines.append(settings.sender_address)
    return "\n\n--\n" + "\n".join(lines)


def send_email(to_email: str, subject: str, body: str, idempotency_key: str | None = None) -> str:
    """Send through the configured provider and return its delivery id."""
    if settings.mail_provider == "resend":
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
                **({"Idempotency-Key": idempotency_key} if idempotency_key else {}),
            },
            json={
                "from": formataddr((settings.sender_name, settings.resend_from_email)),
                "to": [to_email],
                "subject": subject,
                "text": body.rstrip() + build_footer(),
                **({"reply_to": [settings.resend_reply_to]} if settings.resend_reply_to else {}),
            },
            timeout=30,
        )
        response.raise_for_status()
        try:
            return response.json()["id"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError("Resend returned no email id") from exc

    if settings.mail_provider != "smtp":
        raise RuntimeError(f"Unsupported MAIL_PROVIDER: {settings.mail_provider}")

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
    imap.login(settings.imap_username, settings.imap_password)
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
