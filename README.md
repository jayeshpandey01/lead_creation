# leadgen

Automated ICP-based lead discovery, per-lead research, LLM-drafted outreach,
humanized sending (3–4 emails every 10–15 minutes), and a live status
dashboard — all in one Render web service, backed by a SQLite file (no
external database to manage).

Pipeline: `discover` (Google Maps search + local SMTP email verify, both
free/self-hosted — no paid API) → `research` (site/LinkedIn → brief) →
`compose` (trainiq `cmddllm` → subject/body) → `sender` (paced SMTP sends) →
`poller` (IMAP reply/bounce/unsubscribe checks). All four run as background
loops inside `dashboard.py`, a small FastAPI app that also serves a status
page. See `.claude/plans/precious-purring-token.md` for the full design
rationale.

**Why not Apollo/OpenOutFind/CrossLinked?** All tried and dropped during
build: Apollo's API turned out to be paid-plan-only (confirmed live — the
free plan returns a hard 403 on every API endpoint, not just a low credit
cap). CrossLinked (Google/Bing search-scraping for LinkedIn profiles) was
live-tested against a real company and came back empty, most likely because
Google blocks that query pattern from datacenter IPs — which would hit
Render's servers the same way. What's left is genuinely free and did work in
testing: `gosom/google-maps-scraper` (a headless-browser Maps scraper, no
API key, no login) for finding companies, plus a local SMTP-probe verifier
for resolving/validating an email per company.

## 1. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## 2. Configure `.env`

Copy `.env.example` if needed (a starter `.env` is already in this repo) and fill in:

- **`DATABASE_URL`** — defaults to `sqlite:///./leadgen.db`, a local file. No
  setup needed; swap it for a Postgres URL later if you ever outgrow SQLite.
- **`TRAINIQ_API_KEY`** — your `trainiq`/`cmddllm` key.
- **`MAPS_CSV_PATH`** — defaults to `leads_input.csv` in the repo root.
  **This is the easiest way to feed in companies: no hosting needed at all.**
  Fill it in with rows of `name,website,category,phone,email` (email column
  can be left blank — a generic address gets resolved+verified from the
  domain automatically) and it's picked up on the next discovery run.
- **`MAPS_SCRAPER_URL`** / **`QUERIES_FILE`** — only matter if
  `leads_input.csv` is empty/missing. This is the fully-automated path: a
  live `gosom/google-maps-scraper` service (see `render.yaml`) searched with
  one `"<category> in <location>"` line per line of `QUERIES_FILE`. Requires
  actually hosting that service somewhere reachable — skip this entirely if
  the CSV is enough for you.
- **`GENERIC_EMAIL_PREFIXES`** — tried in order against a company's domain
  when a row doesn't list an email directly (e.g. `info`, `hello`, `contact`).
- **`SMTP_*`** / **`IMAP_*`** / **`SENDER_*`** — see step 3.
- Pacing/window/cap values already have sane defaults in `.env.example`.

## 3. Mailbox setup (one-time)

This uses a Gmail app password for both sending (SMTP) and reply/bounce
polling (IMAP) — no Google Cloud project needed.

1. Turn on 2-Step Verification on the sending Google account, then create an
   **App Password** (Google Account → Security → 2-Step Verification → App
   passwords).
2. In Gmail settings, make sure **IMAP is enabled** (Settings → Forwarding and
   POP/IMAP → Enable IMAP) — needed for the reply/bounce poller.
3. Set `SMTP_FROM_EMAIL` and `SMTP_PASSWORD` (the app password, not your
   regular Gmail password) in `.env` — `SMTP_HOST`/`PORT`/`IMAP_HOST` already
   default to Gmail's.
4. Set `SENDER_NAME`, `SENDER_COMPANY`, `SENDER_ADDRESS` — a real postal
   address is required in the footer (CAN-SPAM applies to B2B cold email too).

## 4. Edit positioning

`config/positioning.yaml` holds the sender persona and the proof points
(Joblet.ai, the RAG+OKF pipeline, etc.) the compose step chooses from per lead.
Edit the `pitch`/`best_for` text to match how you'd actually describe each one.

## 5. Test locally, in order, before touching sending

```bash
python -m leadgen.pipeline
```

This runs discover → research → compose once and exits. Inspect `leadgen.db`
after each stage (e.g. `sqlite3 leadgen.db "select company, status, email_subject from leads"`)
for a handful of real companies — check the `research_brief`, `email_subject`,
`email_body` read right — before wiring up sending.

To test sending itself, temporarily point `SMTP_FROM_EMAIL`/recipients at a
personal test inbox (not real leads), then run the full app:

```bash
uvicorn leadgen.dashboard:app --reload
```

Open http://localhost:8000 for the live status dashboard (lead counts by
status, a table of recent leads/sends). Watch the logs too: batches should be
3–4 sends, ~10–15 minutes apart, with 20–90s gaps between individual sends in
a batch.

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
necessarily "maps-scraper") — set `MAPS_SCRAPER_URL=http://<that exact
name>:10000` on `leadgen` to match.

Either way, fill in the remaining `sync: false` env vars in the Render
dashboard (they're marked secret, not stored in `render.yaml`). Once
deployed, the dashboard is at the `leadgen` service's Render URL.

**Note on the `maps-scraper` service config**: I wrote it against the tool's
documented CLI flags, but couldn't live-verify it on an actual Render deploy
(no Docker available to test locally). If that service fails to start,
check its logs against the image's actual `ENTRYPOINT`/`CMD` — the
`startCommand` in `render.yaml` may need adjusting. This whole service is
optional — skip it if `leads_input.csv` covers your needs.

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
- **Reply matching** relies on standard `In-Reply-To`/`References` headers —
  works with any real mail client's reply, but a reply sent from a client
  that strips these headers won't be matched.
- **Follow-ups** are not implemented — `leads.next_action_at` exists for this
  but nothing schedules it yet. Add a follow-up pass in `compose.py`/`sender.py`
  once the base loop is proven out.
- **SQLite** is fine at this volume (one writer process, tens of emails/day)
  but isn't built for concurrent writers — if you ever split sending across
  multiple processes/services, move `DATABASE_URL` to Postgres first.
# lead_creation
