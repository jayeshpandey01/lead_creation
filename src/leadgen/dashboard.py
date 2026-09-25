"""Single-process entrypoint: serves a read-only status dashboard over HTTP
and runs the sender/poller/pipeline loops in the background on the same
event loop. Run with: uvicorn leadgen.dashboard:app
"""
import asyncio
import html
import logging
import secrets
from collections import Counter
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse, HTMLResponse

from . import maps_client
from .db import get_session, init_db
from .models import Lead, LeadStatus
from .settings import settings
from .worker import run_pipeline_once, start_background_tasks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="leadgen")
_basic_auth = HTTPBasic()

_STATUS_ORDER = [s.value for s in LeadStatus]


@app.get("/health")
def health() -> dict[str, str]:
    """Render readiness probe: only report healthy when Maps discovery works."""
    if not maps_client.is_available(log_failures=False, timeout=2):
        raise HTTPException(status_code=503, detail="Maps scraper unavailable")
    return {"status": "ok"}


def _require_dashboard_login(credentials: HTTPBasicCredentials = Depends(_basic_auth)) -> None:
    if not settings.dashboard_username or not settings.dashboard_password:
        raise HTTPException(status_code=503, detail="Dashboard login is not configured")
    valid_username = secrets.compare_digest(credentials.username, settings.dashboard_username)
    valid_password = secrets.compare_digest(credentials.password, settings.dashboard_password)
    if not (valid_username and valid_password):
        raise HTTPException(
            status_code=401,
            detail="Invalid dashboard credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    await start_background_tasks()


@app.post("/trigger-pipeline")
async def trigger_pipeline(x_trigger_token: str = Header(default="")) -> dict:
    """Manual one-off discover -> research -> compose run, for testing
    without waiting for the daily scheduled run. Requires TRIGGER_TOKEN to
    be set and matched via the X-Trigger-Token header:
      curl -X POST https://<your-app>.onrender.com/trigger-pipeline \\
        -H "X-Trigger-Token: <your token>"
    """
    if not settings.trigger_token or not secrets.compare_digest(x_trigger_token, settings.trigger_token):
        raise HTTPException(status_code=403, detail="Missing or invalid X-Trigger-Token")

    asyncio.create_task(run_pipeline_once())
    return {"status": "started"}


def _row(lead: Lead) -> str:
    sent_at = lead.sent_at.strftime("%Y-%m-%d %H:%M") if lead.sent_at else ""
    return (
        "<tr>"
        f"<td>{html.escape(lead.company or '')}</td>"
        f"<td>{html.escape(lead.email)}</td>"
        f"<td><span class='status'>{lead.status.value}</span></td>"
        f"<td>{html.escape(lead.email_subject or '')}</td>"
        f"<td>{sent_at}</td>"
        "</tr>"
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(_: None = Depends(_require_dashboard_login)) -> str:
    session = get_session()
    try:
        leads = session.query(Lead).order_by(Lead.updated_at.desc()).limit(100).all()
        counts = Counter(lead.status.value for lead in session.query(Lead).all())
    finally:
        session.close()

    cards = "".join(
        f"<div class='card'><div class='count'>{counts.get(s, 0)}</div><div class='label'>{s}</div></div>"
        for s in _STATUS_ORDER
    )
    rows = "".join(_row(lead) for lead in leads) or "<tr><td colspan='5'>No leads yet</td></tr>"

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="30">
  <title>leadgen</title>
  <style>
    body {{ font-family: -apple-system, Helvetica, sans-serif; background: #0b0d10; color: #e6e6e6; padding: 24px; }}
    h1 {{ font-size: 18px; font-weight: 600; }}
    .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0 24px; }}
    .card {{ background: #16191d; border-radius: 10px; padding: 14px 20px; min-width: 90px; text-align: center; }}
    .count {{ font-size: 26px; font-weight: 700; }}
    .label {{ font-size: 11px; color: #9aa0a6; text-transform: uppercase; margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #22262b; }}
    .status {{ padding: 2px 8px; border-radius: 6px; background: #22262b; font-size: 11px; }}
  </style>
</head>
<body>
  <h1>leadgen</h1>
  <div class="cards">{cards}</div>
  <table>
    <tr><th>Company</th><th>Email</th><th>Status</th><th>Subject</th><th>Sent at</th></tr>
    {rows}
  </table>
</body>
</html>"""


@app.get("/leads.csv", dependencies=[Depends(_require_dashboard_login)])
def download_leads_csv() -> FileResponse:
    path = Path(settings.leads_export_csv_path).expanduser()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Lead export has not been generated yet")
    return FileResponse(path, media_type="text/csv", filename="leads_output.csv")
