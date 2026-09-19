"""Free, self-hosted email existence check: resolve the domain's MX record,
connect to its mail server, and probe with RCPT TO — without ever sending
DATA, so no email actually goes out. Same principle open-source verifiers
like check-if-email-exists use.

Important caveat: this requires an outbound connection on port 25, which
many cloud hosts (Render included) block by default to fight spam. When
that happens every check comes back "unknown" (never "invalid"), so
run_discovery treats "unknown" as good enough to proceed rather than
rejecting everything — an unreachable verifier degrades gracefully, it
doesn't wrongly reject every candidate.
"""
import logging
import smtplib
import socket

import dns.resolver

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 8
_PROBE_FROM = "verify@example.com"


def _mx_host(domain: str) -> str | None:
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=_TIMEOUT_SECONDS)
        best = min(answers, key=lambda r: r.preference)
        return str(best.exchange).rstrip(".")
    except Exception:
        return None


def check_email(email: str) -> str:
    """Returns "valid", "invalid", or "unknown" (inconclusive — treat as
    good enough to proceed, see module docstring)."""
    if "@" not in email:
        return "invalid"
    domain = email.rsplit("@", 1)[-1]

    mx_host = _mx_host(domain)
    if not mx_host:
        return "invalid"  # domain has no mail server at all — safe to reject

    try:
        with smtplib.SMTP(timeout=_TIMEOUT_SECONDS) as smtp:
            smtp.connect(mx_host, 25)
            smtp.helo(_PROBE_FROM.split("@")[1])
            smtp.mail(_PROBE_FROM)
            code, _ = smtp.rcpt(email)
            if code == 250:
                return "valid"
            if code in (550, 551, 553):
                return "invalid"
            return "unknown"
    except (socket.timeout, socket.error, smtplib.SMTPException, OSError):
        logger.info("SMTP probe to %s unreachable (port 25 likely blocked here) — treating as unknown", mx_host)
        return "unknown"


def find_best_generic_email(domain: str, prefixes: list[str]) -> str | None:
    """Tries each prefix@domain in order, returns the first one that's
    "valid" or "unknown" (i.e. not confirmed invalid). Returns None only if
    every prefix is confirmed invalid or the domain has no mail server."""
    if not prefixes:
        prefixes = ["info", "hello", "contact"]

    fallback = None
    for prefix in prefixes:
        candidate = f"{prefix}@{domain}"
        result = check_email(candidate)
        if result == "valid":
            return candidate
        if result == "unknown" and fallback is None:
            fallback = candidate
    return fallback
