"""Website Intelligence & Audit Engine (Layers 2 & 3) + Opportunity Scoring (Layer 4)
Inspired by Scavier, Trivium, and EpicSaber as outlined in layers.md.

Performs non-invasive static audits of business websites to identify:
1. Online appointment / booking system gaps
2. WhatsApp CTA & lead automation gaps
3. Mobile UX & SSL security health
4. Technical SEO (OpenGraph, meta description, Schema.org)
5. CMS / technology stack
6. Calculates Copy'S Opportunity Score (0-100) and matches target service
"""
import json
import logging
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 10
_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

_BOOKING_PATTERNS = [
    re.compile(r"calendly\.com", re.I),
    re.compile(r"cal\.com", re.I),
    re.compile(r"acuityscheduling\.com", re.I),
    re.compile(r"booksy\.com", re.I),
    re.compile(r"square\.site", re.I),
    re.compile(r"setmore\.com", re.I),
    re.compile(r"simplybook\.me", re.I),
    re.compile(r"zoho\.com/bookings", re.I),
    re.compile(r"doctolib", re.I),
    re.compile(r"zocdoc", re.I),
    re.compile(r"fresha\.com", re.I),
    re.compile(r"/book(-online)?", re.I),
    re.compile(r"/appointment", re.I),
    re.compile(r"/schedule", re.I),
]

_WHATSAPP_PATTERNS = [
    re.compile(r"wa\.me/", re.I),
    re.compile(r"api\.whatsapp\.com/send", re.I),
    re.compile(r"web\.whatsapp\.com", re.I),
    re.compile(r"whatsapp:", re.I),
]


@dataclass
class AuditDetails:
    has_ssl: bool = False
    has_responsive_viewport: bool = False
    has_meta_title: bool = False
    has_meta_description: bool = False
    has_opengraph: bool = False
    has_schema_ldjson: bool = False
    has_booking: bool = False
    has_whatsapp: bool = False
    has_contact_form: bool = False
    has_tel_link: bool = False
    has_email_link: bool = False
    cms: str | None = None
    analytics: list[str] | None = None


@dataclass
class AuditResult:
    opportunity_score: int
    recommended_service: str
    top_gaps: list[str]
    details: AuditDetails

    def to_json(self) -> str:
        return json.dumps({
            "opportunity_score": self.opportunity_score,
            "recommended_service": self.recommended_service,
            "top_gaps": self.top_gaps,
            "details": asdict(self.details),
        })


def _detect_cms(html_text: str, soup: BeautifulSoup) -> str | None:
    lowered = html_text.lower()
    generator = soup.find("meta", attrs={"name": re.compile(r"^generator$", re.I)})
    if generator and generator.get("content"):
        gen_content = generator["content"].lower()
        if "wordpress" in gen_content:
            return "WordPress"
        if "shopify" in gen_content:
            return "Shopify"
        if "webflow" in gen_content:
            return "Webflow"
        if "wix" in gen_content:
            return "Wix"
        if "squarespace" in gen_content:
            return "Squarespace"
        if "drupal" in gen_content:
            return "Drupal"
        if "joomla" in gen_content:
            return "Joomla"

    if "wp-content" in lowered or "wp-includes" in lowered:
        return "WordPress"
    if "cdn.shopify.com" in lowered:
        return "Shopify"
    if "wix.com" in lowered or "_wix" in lowered:
        return "Wix"
    if "squarespace" in lowered:
        return "Squarespace"
    if "__next" in lowered or "_next/static" in lowered:
        return "Next.js"
    if "react" in lowered and "react-dom" in lowered:
        return "React"
    if "data-wf-page" in lowered or "webflow" in lowered:
        return "Webflow"

    return None


def _detect_analytics(html_text: str) -> list[str]:
    lowered = html_text.lower()
    detected = []
    if "google-analytics.com" in lowered or "gtag(" in lowered:
        detected.append("Google Analytics")
    if "googletagmanager.com" in lowered:
        detected.append("Google Tag Manager")
    if "fbevents.js" in lowered or "fbq(" in lowered:
        detected.append("Meta Pixel")
    if "hotjar.com" in lowered:
        detected.append("Hotjar")
    if "mixpanel.com" in lowered:
        detected.append("Mixpanel")
    return detected


def audit_website(url: str | None, rating: float | None = None, reviews_count: int | None = None, category: str | None = None) -> AuditResult:
    """Performs static intelligence analysis on the given website URL
    and calculates Copy'S Opportunity Score (0-100)."""
    details = AuditDetails()
    top_gaps: list[str] = []

    if not url:
        return AuditResult(
            opportunity_score=85,
            recommended_service="High-Converting Website Build",
            top_gaps=["No website detected", "Missing online presence", "No online lead capture"],
            details=details,
        )

    clean_url = url.strip()
    if not clean_url.startswith(("http://", "https://")):
        clean_url = "https://" + clean_url

    parsed = urlparse(clean_url)
    details.has_ssl = parsed.scheme == "https"

    raw_html = ""
    try:
        resp = requests.get(
            clean_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=True,
            verify=False,
        )
        if resp.status_code == 200:
            raw_html = resp.text
            if resp.url.startswith("https://"):
                details.has_ssl = True
    except Exception as exc:
        logger.debug("Website fetch failed for %s: %s", clean_url, exc)

    if not raw_html:
        top_gaps.append("Website unreachable or slow response (>10s)")
        return AuditResult(
            opportunity_score=75,
            recommended_service="High-Converting Website Redesign",
            top_gaps=top_gaps,
            details=details,
        )

    soup = BeautifulSoup(raw_html[:300000], "html.parser")

    # 1. Mobile & Viewport check
    viewport = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    details.has_responsive_viewport = bool(viewport and "width=device-width" in str(viewport.get("content", "")))
    if not details.has_responsive_viewport:
        top_gaps.append("Weak mobile UX (missing responsive viewport)")

    # 2. SEO Checks
    title = soup.find("title")
    details.has_meta_title = bool(title and title.text.strip())

    meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    details.has_meta_description = bool(meta_desc and meta_desc.get("content", "").strip())
    if not details.has_meta_description:
        top_gaps.append("Missing search meta description")

    og_title = soup.find("meta", attrs={"property": "og:title"})
    details.has_opengraph = bool(og_title and og_title.get("content", "").strip())

    scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    details.has_schema_ldjson = len(scripts) > 0
    if not details.has_schema_ldjson:
        top_gaps.append("Missing Schema.org structured data")

    # 3. Booking Engine Check
    all_links = [a.get("href", "") for a in soup.find_all("a", href=True)]
    for href in all_links:
        for pat in _BOOKING_PATTERNS:
            if pat.search(href):
                details.has_booking = True
                break
        if details.has_booking:
            break

    if not details.has_booking:
        for tag in soup.find_all(["iframe", "script"], src=True):
            src = tag.get("src", "")
            for pat in _BOOKING_PATTERNS:
                if pat.search(src):
                    details.has_booking = True
                    break
            if details.has_booking:
                break

    if not details.has_booking:
        top_gaps.append("No online appointment / booking flow")

    # 4. WhatsApp CTA Check
    for href in all_links:
        for pat in _WHATSAPP_PATTERNS:
            if pat.search(href):
                details.has_whatsapp = True
                break
        if details.has_whatsapp:
            break

    if not details.has_whatsapp:
        top_gaps.append("No automated WhatsApp chat or CTA")

    # 5. Contact & Phone Links
    for href in all_links:
        if href.startswith("tel:"):
            details.has_tel_link = True
        elif href.startswith("mailto:"):
            details.has_email_link = True

    details.has_contact_form = bool(soup.find("form"))

    # 6. CMS & Analytics
    details.cms = _detect_cms(raw_html, soup)
    details.analytics = _detect_analytics(raw_html)

    # ----------------------------------------------------
    # Calculate Opportunity Score (0 - 100) per layers.md
    # ----------------------------------------------------
    score = 0

    if not details.has_ssl:
        score += 10
    if not details.has_responsive_viewport:
        score += 15
    elif not details.has_contact_form and not details.has_tel_link:
        score += 5

    rev_count = reviews_count or 0
    if rev_count >= 100:
        score += 12
    elif rev_count >= 20:
        score += 8
    elif rev_count >= 5:
        score += 4

    cur_rating = rating or 0.0
    if cur_rating >= 4.0:
        score += 8
    elif cur_rating >= 3.0:
        score += 4

    if not details.has_booking:
        score += 14
    if not details.has_contact_form and not details.has_tel_link:
        score += 6

    if details.cms in ("WordPress", "Joomla", "Drupal") or details.cms is None:
        score += 10
    if not details.analytics:
        score += 5

    if not details.has_meta_description:
        score += 5
    if not details.has_schema_ldjson:
        score += 5

    if not details.has_whatsapp:
        score += 10

    opportunity_score = min(max(score, 10), 100)

    cat_lower = (category or "").lower()
    is_tech = any(k in cat_lower for k in ["software", "tech", "ai", "startup", "developer", "agency", "saas"])
    is_service_or_clinic = any(k in cat_lower for k in ["dental", "clinic", "doctor", "salon", "spa", "real estate", "consult", "law", "gym"])

    if not details.has_booking and (is_service_or_clinic or rev_count >= 15):
        recommended_service = "Online Appointment / Booking Engine"
    elif not details.has_whatsapp and is_service_or_clinic:
        recommended_service = "WhatsApp Lead Automation Bot"
    elif not details.has_responsive_viewport or not details.has_ssl:
        recommended_service = "High-Converting Website Redesign"
    elif is_tech:
        recommended_service = "Production AI / ML Microservices"
    elif not details.has_booking:
        recommended_service = "Online Appointment / Booking Engine"
    else:
        recommended_service = "Website Conversion & Automation Redesign"

    return AuditResult(
        opportunity_score=opportunity_score,
        recommended_service=recommended_service,
        top_gaps=top_gaps[:3],
        details=details,
    )
