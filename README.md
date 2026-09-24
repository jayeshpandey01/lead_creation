# leadgen

Automated ICP-based lead discovery, per-lead research, LLM-drafted outreach,
paced sending, and a live status
dashboard — all in one Render web service, backed by a SQLite file (no
external database to manage).

Pipeline: `discover` (Google Maps search + local SMTP email verify, both
free/self-hosted — no paid API) → `research` (site/LinkedIn → brief) →
`compose` (OpenRouter → subject/body) → `sender` (paced Resend or SMTP sends) →
`poller` (IMAP bounce checks and SMTP reply/unsubscribe checks). All four run as background
loops inside `dashboard.py`, a small FastAPI app that also serves a status
page. See `.claude/plans/precious-purring-token.md` for the full design
rationale.

The active discovery path uses `gosom/google-maps-scraper` and does not call
Apollo. Apollo support code remains outside the active pipeline.

## 1. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## 2. Configure `.env`

Copy `.env.example` if needed (a starter `.env` is already in this repo) and fill in:

- **`DATABASE_URL`** — defaults to `sqlite:///./leadgen.db`, a local file. No
  setup needed; swap it for a Postgres URL later if you ever outgrow SQLite.
- **`OPENROUTER_API_KEY` / `OPENROUTER_MODEL`** — used for research and drafting.
- **`MAPS_CSV_PATH`** — defaults to `leads_input.csv` in the repo root.
  **This is the easiest way to feed in companies: no hosting needed at all.**
  Fill it in with rows of `name,website,category,phone,email` (email column
  can be left blank — a generic address gets resolved+verified from the
  domain automatically) and it's picked up on the next discovery run.
- **`LEADS_EXPORT_CSV_PATH`** — defaults to `leads_output.csv`. This is an
  output snapshot of leads stored in the database, including research, draft,
  and send status. It is refreshed after pipeline runs and sender/poller status
  changes. The input file and output file are separate.
- **`DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`** — required to view the
  dashboard or download its CSV at `/leads.csv`. Keep these credentials private.
- **`MAPS_SCRAPER_URL`** / **`QUERIES_FILE`** — only matter if
  `leads_input.csv` is empty/missing. This is the fully-automated path: a
  `gosom/google-maps-scraper` service searched with one
  `"<category> in <location>"` line per line of `QUERIES_FILE`. Locally, run
  `./scripts/run_scraper.sh` (Docker Desktop must be running) to start it at
  `http://localhost:8080`, matching the default `MAPS_SCRAPER_URL`.
- **`GENERIC_EMAIL_PREFIXES`** — tried in order against a company's domain
  when a row doesn't list an email directly (e.g. `info`, `hello`, `contact`).
- **`SMTP_*`** / **`IMAP_*`** / **`SENDER_*`** — see step 3.
- Pacing/window/cap values already have sane defaults in `.env.example`.

## 3. Mailbox setup (one-time)

Resend is the default sender in the Render Blueprint. Verify a sending domain
in Resend, then set `RESEND_API_KEY` and `RESEND_FROM_EMAIL`. Set
`RESEND_REPLY_TO` to an inbox that receives replies. SMTP remains available
locally with `MAIL_PROVIDER=smtp`. To enable bounce polling, configure
`IMAP_USERNAME` and `IMAP_PASSWORD` for the mailbox receiving delivery notices
and enable IMAP on that mailbox.

Set `SENDER_NAME`, `SENDER_COMPANY`, `SENDER_ADDRESS` — a real postal
   address is required in the footer (CAN-SPAM applies to B2B cold email too).

## 4. Edit positioning

`config/positioning.yaml` holds the sender persona and approved proof points
the compose step can choose from per lead.
Edit the `pitch`/`best_for` text to match how you'd actually describe each one.

## 5. Test locally, in order, before touching sending

```bash
./scripts/run_scraper.sh  # omit this when importing leads from CSV
python -m leadgen.pipeline
```

The one-shot command discovers, researches, and drafts; it does not send.
It writes the database snapshot to `leads_output.csv`. The dashboard starts
the background sender, which automatically sends every `ready_to_send` lead
during the configured window, provided all required mail and sender settings
are present. The sender now pauses if provider credentials, sender identity, or
postal address are missing. Test sending with a controlled recipient first.

To test sending itself, use an isolated test lead addressed to your own inbox,
then run the full app:

```bash
uvicorn leadgen.dashboard:app --reload
```

Open http://localhost:8000 for the live status dashboard (lead counts by
status, a table of recent leads/sends); the browser prompts for dashboard
credentials. Download the current export at `/leads.csv` with the same login.
Render settings in `render.yaml` run
one discovery job at a time, target a three-minute start-to-start pipeline
interval, check for newly drafted messages once a minute, and send at most one
message every 2–3 minutes, with a daily cap of 15. A scrape can take the full
three-minute job limit, so the next run starts when the current one completes.

## 6. Deploy to Render

```bash
git init && git add -A && git commit -m "Initial leadgen scaffold"
```

**If you're using `leads_input.csv`** (the simple path), you only need the
`leadgen` web service — deploy it however you like (manually in the Render
UI, or via Blueprint using just that one service from `render.yaml`). No
second service, no Docker, nothing else to host.

**If you want the fully-automated live-scraper path instead**, `render.yaml`
also defines a `maps-scraper` service (Google Maps scraper, deployed from
its public Docker image, internal-only). Push to a GitHub repo and use
**New → Blueprint** to deploy both at once — `leadgen` then reaches
`maps-scraper` automatically via Render's private networking. If you set
this up manually service-by-service instead, note that Render's internal
hostname is whatever you actually name the private service (not
necessarily `maps-scraper`) — set `MAPS_SCRAPER_URL=http://<that exact
name>:10000` on `leadgen` to match. The Blueprint passes host and port; the
client adds the HTTP scheme automatically.

Either way, fill in `OPENROUTER_API_KEY`, `RESEND_API_KEY`,
`RESEND_FROM_EMAIL`, dashboard credentials, and sender identity in the Render
dashboard (they're marked secret, not stored in `render.yaml`). Once
deployed, the dashboard is at the `leadgen` service's Render URL.
Set `DASHBOARD_USERNAME` and a strong `DASHBOARD_PASSWORD` before opening the
dashboard. The generated CSV is available at `<service-url>/leads.csv` after
the first pipeline run.

The web service and scraper both use persistent disks, which Render requires
a paid service plan for. The worker is part of the web service so it shares
the same SQLite file; do not scale this service to multiple instances while
using SQLite.

## 7. Warm-up and rollout

Don't point this at real prospects at full volume on day one — a cold mailbox
sending 15–20/day immediately gets flagged. Start `SEND_DAILY_CAP` low (5–10),
watch Gmail Postmaster Tools / bounce and reply rates for a week or two, then
raise the cap gradually toward steady state.

## Known limitations (by design, for v1)

- **No named contact, by design.** This pipeline finds companies (via Maps),
  not a specific decision-maker (that was CrossLinked's job — dropped, see
  above). Emails address "the team at {company}" generically. `compose.py`
  and its prompt already handle this; `Lead.first_name`/`last_name`/`title`
  are simply left blank.
- **Email resolution is best-effort**: Maps' own listed email is used when
  present; otherwise a handful of generic prefixes (`info@`, `hello@`, etc.)
  are tried against the company's domain and checked via a local SMTP probe
  in `email_verify.py`. That probe needs outbound port 25, which many cloud
  hosts block — when blocked, every check comes back "unknown" rather than
  failing, and an "unknown" address is still used (fails open) rather than
  discarding every candidate. Worth checking Render's logs after the first
  live discovery run to see whether verification is actually confirming
  anything or just falling through every time.
- **`qualification_reason`** is just a plain-text note of which search query
  and category matched — no LLM reasoning per lead. `research_brief` (built
  from the site scrape) carries the real personalization signal instead.
- **LinkedIn research** only reads publicly-rendered content (no login) —
  often just a meta description. Website content is the primary research
  source; treat LinkedIn as a bonus when it works.
- **Bounce detection** is best-effort: it scans recent `mailer-daemon`
  messages in the inbox for the recipient's address, which is a heuristic,
  not a proper bounce/DSN parser. Tighten this in `mail_client.check_bounce`
  if bounce volume matters to you.
- **Resend reply matching is not implemented yet.** Resend returns an API email
  id, while the current IMAP reply matcher expects an SMTP message id. Replies
  can go to `RESEND_REPLY_TO`, but the dashboard will not classify them as
  replied or process STOP until inbound reply handling is added. SMTP mode uses
  standard `In-Reply-To`/`References` matching.
- **Follow-ups** are not implemented — `leads.next_action_at` exists for this
  but nothing schedules it yet. Add a follow-up pass in `compose.py`/`sender.py`
  once the base loop is proven out.
- **SQLite** is fine at this volume (one writer process, tens of emails/day)
  but isn't built for concurrent writers — if you ever split sending across
  multiple processes/services, move `DATABASE_URL` to Postgres first.
# lead_creation
