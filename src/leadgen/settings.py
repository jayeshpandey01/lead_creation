import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() == "true"


def _list(name: str, sep: str = ",") -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(sep) if item.strip()]


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///./leadgen.db"))
    trainiq_api_key: str = field(default_factory=lambda: os.environ.get("TRAINIQ_API_KEY", ""))

    # Discovery, source 1 (free, always-on): gosom/google-maps-scraper +
    # local SMTP verifier. See discover_maps.py.
    maps_scraper_url: str = field(default_factory=lambda: os.environ.get("MAPS_SCRAPER_URL", "http://localhost:8080"))
    queries_file: str = field(default_factory=lambda: os.environ.get("QUERIES_FILE", "queries.txt"))
    generic_email_prefixes: list[str] = field(
        default_factory=lambda: _list("GENERIC_EMAIL_PREFIXES") or ["info", "hello", "contact", "sales", "support"]
    )

    # Discovery, source 2 (optional, paid-plan-gated): Apollo.io. No-op
    # until APOLLO_API_KEY is on a plan with API access. See discover_apollo.py.
    apollo_api_key: str = field(default_factory=lambda: os.environ.get("APOLLO_API_KEY", ""))
    apollo_person_titles: list[str] = field(default_factory=lambda: _list("APOLLO_PERSON_TITLES"))
    apollo_employee_ranges: list[str] = field(default_factory=lambda: _list("APOLLO_EMPLOYEE_RANGES", sep=";"))
    apollo_person_locations: list[str] = field(default_factory=lambda: _list("APOLLO_PERSON_LOCATIONS"))

    smtp_from_email: str = field(default_factory=lambda: os.environ.get("SMTP_FROM_EMAIL", ""))
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_use_tls: bool = field(default_factory=lambda: _bool("SMTP_USE_TLS", True))
    smtp_password: str = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD", ""))
    imap_host: str = field(default_factory=lambda: os.environ.get("IMAP_HOST", "imap.gmail.com"))

    sender_name: str = field(default_factory=lambda: os.environ.get("SENDER_NAME", ""))
    sender_company: str = field(default_factory=lambda: os.environ.get("SENDER_COMPANY", ""))
    sender_address: str = field(default_factory=lambda: os.environ.get("SENDER_ADDRESS", ""))

    batch_min: int = field(default_factory=lambda: _int("SEND_BATCH_MIN", 3))
    batch_max: int = field(default_factory=lambda: _int("SEND_BATCH_MAX", 4))
    interval_min_seconds: int = field(default_factory=lambda: _int("SEND_INTERVAL_MIN_SECONDS", 600))
    interval_max_seconds: int = field(default_factory=lambda: _int("SEND_INTERVAL_MAX_SECONDS", 900))
    inter_send_min_seconds: int = field(default_factory=lambda: _int("SEND_GAP_MIN_SECONDS", 20))
    inter_send_max_seconds: int = field(default_factory=lambda: _int("SEND_GAP_MAX_SECONDS", 90))
    daily_cap: int = field(default_factory=lambda: _int("SEND_DAILY_CAP", 15))
    sending_hour_start: int = field(default_factory=lambda: _int("SENDING_HOUR_START", 9))
    sending_hour_end: int = field(default_factory=lambda: _int("SENDING_HOUR_END", 18))
    sending_weekdays_only: bool = field(default_factory=lambda: _bool("SENDING_WEEKDAYS_ONLY", True))
    timezone: str = field(default_factory=lambda: os.environ.get("SENDER_TIMEZONE", "Asia/Kolkata"))

    poll_interval_seconds: int = field(default_factory=lambda: _int("POLL_INTERVAL_SECONDS", 300))

    discovery_batch_size: int = field(default_factory=lambda: _int("DISCOVERY_BATCH_SIZE", 10))
    pipeline_run_hour: int = field(default_factory=lambda: _int("PIPELINE_RUN_HOUR", 4))

    # Shared secret for POST /trigger-pipeline (manual test trigger on the
    # dashboard). Empty by default = endpoint refuses all requests.
    trigger_token: str = field(default_factory=lambda: os.environ.get("TRIGGER_TOKEN", ""))


settings = Settings()
