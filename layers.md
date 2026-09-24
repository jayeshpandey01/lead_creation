# LeadGen implementation plan

## Goal

Build the existing project into a dependable, small lead acquisition workflow: ingest a known company, research it, draft an evidence-based email, let a human review it, and only then enable sending. The first milestone is a repeatable local run that moves at least one valid CSV row through `discovered → researched → ready_to_send`.

This is an implementation plan for this repository, not a plan to combine several unrelated scrapers or audit engines. The current code already has discovery, website research, LLM composition, SMTP/IMAP utilities, a SQLite model, and a dashboard. Stabilize those paths before adding new product layers.

## What the latest run tells us

The output is consistent with the current source:

1. `discover_maps` attempted the live scraper at `MAPS_SCRAPER_URL=http://localhost:8080`, but it was unreachable.
2. `leads_input.csv` currently contains only a header row. `_load_rows_from_csv()` returns `None` for a header-only file, so discovery falls back to live Maps queries.
3. The configured Apollo key allowed a request to reach Apollo, but People Search returned HTTP 403. Current Apollo docs define 403 as plan or endpoint access not available to those credentials; the log is correctly treating that optional source as unavailable.
4. Discovery inserted zero leads. Research and compose query only existing leads in `discovered` and `researched` states, respectively, so both correctly processed zero.

The immediate blocker is data availability, not research or composition. To get the first end-to-end pass without deploying anything, add one or more company rows to `leads_input.csv` with a name and website and run the pipeline again. Rows without an email use the current generic-address resolver and SMTP verifier; that verifier may return unknown when outbound port 25 is blocked. Configure a working `TRAINIQ_API_KEY` as well, or research/composition will not complete for leads that need an LLM summary/draft.

## Current architecture and boundaries

```text
CSV or Maps service ──> lead dedupe + email candidate ──> SQLite
                                                    │
                                                    v
                              website/public research + LLM summary
                                                    │
                                                    v
                                  LLM email draft + spam checks
                                                    │
                                                    v
                                      human review / dashboard
                                                    │
                                                    v
                                paced SMTP sender + IMAP poller
```

Apollo is an optional second discovery source. It should remain optional and must not prevent the CSV path from working. `audit.py` is present but is not integrated into the pipeline; treat website auditing as a later, separate milestone after this core workflow is reliable.

## Phased implementation

### Phase 0 — Make the first pipeline run deterministic

**Purpose:** Establish that a supplied lead reaches each existing stage, without relying on Docker, Google Maps, Apollo, SMTP, or IMAP.

- Put a small, authorized test company row into `leads_input.csv` (`name,website,category,phone,email`). Prefer a test inbox or a company/domain you control. Keep the recipient out of real sending.
- Confirm CSV precedence and field normalization, including blank email, malformed website, duplicate email, duplicate company, and a row with no website.
- Separate pipeline success from provider availability in logs: report source counts and stage skips/reasons, and log a concise actionable message when `TRAINIQ_API_KEY` is absent.
- Make local provider failures non-fatal to already-ingested CSV rows. Keep per-lead failure state/reason observable so a failed LLM call is retryable and not silently indistinguishable from untouched work.
- Keep Apollo disabled when no key is present; when it returns 403, surface the endpoint access issue as configuration/status, not as evidence that all Apollo free accounts universally lack API access. Apollo's current docs say certain search endpoints are available to eligible free accounts using a work email, while access depends on account/key scope.

**Done when:** with one eligible row and valid research/drafting credentials, a single CLI run creates a database record, fills `research_brief`, creates subject/body, and ends in `ready_to_send`; no email is sent by the one-shot pipeline.

### Phase 1 — Harden CSV ingestion and lead quality

- Define and document the minimum accepted columns, required fields, normalization, and how blank emails are handled. Keep the current flexible extra-column behavior.
- Validate website URLs and email syntax before network calls. Record rejected rows with line number and reason rather than silently dropping them.
- Make dedupe stable across case/whitespace and domain variants. Add a database uniqueness strategy only after deciding the intended company identity rule; email is already unique.
- Add an explicit source field and discovery timestamp/source metadata instead of encoding all source details only in `qualification_reason`.
- Do not infer named contacts or fabricate direct addresses. Mark generic/resolved addresses and their verification confidence distinctly.

**Done when:** importing the same CSV twice is idempotent, invalid rows are explainable, and a user can identify where each stored field came from.

### Phase 2 — Make research and composition observable and safe

- Check required configuration before processing and fail/skip with actionable per-stage messages. Do not let one lead's network or LLM error stop the batch.
- Store stage attempt timestamps and concise failure reasons; retry only eligible failures with bounded attempts/backoff.
- Bound website fetch size/time, normalize URLs, and block private, loopback, link-local, and other non-public destinations to reduce SSRF risk from CSV-provided URLs. Recheck redirects before fetching their targets.
- Clearly label research as public-source observations and preserve source URLs/evidence snippets so generated claims can be reviewed.
- Parse/validate model output with a small schema, enforce email length/content constraints, and keep the existing spam checks as a separate gate.
- Keep the existing human-in-the-loop: composition creates drafts, it never sends.

**Done when:** each lead has an auditable research source, a valid draft or a visible failure reason, and retries do not duplicate or overwrite already-approved work.

### Phase 3 — Verify the optional live Maps adapter

Use the CSV path as the dependable baseline. Only operate the Maps adapter when it is deliberately configured and the upstream tool's current API matches `maps_client.py`.

- Start the documented `gosom/google-maps-scraper` web server locally with its web mode, data folder, and port 8080; verify `/api/docs` and a small test job before connecting the pipeline.
- Check request/response payloads, status names, CSV fields, and service health against the installed image/version. Pin an image version or digest after validation instead of relying indefinitely on `latest`.
- Add a fast connectivity/health check before job creation, sensible connect/read timeouts, bounded polling, and a clear distinction between unavailable service, empty successful search, and failed job.
- Do not run broad or high-volume scraping by default. Keep small query sets and low depth for a controlled trial.
- Review the scraper project's license and applicable source-site terms before operational use. A tool's open-source license does not grant rights to upstream site data or make collection compliant by itself.

**Done when:** a deliberately enabled local service can produce one small result set, and an offline service is reported as unavailable without making the whole pipeline appear successful.

### Phase 4 — Dashboard review and controlled sending

- Make the dashboard show counts by state, recent errors, source, and draft preview. Add explicit approve/reject actions before a lead enters the send queue.
- Require complete sender identity, mailbox credentials, footer address, suppression checks, and a test-recipient mode before enabling SMTP.
- Enforce unsubscribe/suppression before every send, honor replies and bounces, and prevent repeated sends after restarts. Keep the configured rate/window caps, but do not describe pacing as a guarantee of inbox placement.
- Test sends only to a mailbox controlled by the operator first. Keep real prospects out of the test run.
- Review applicable email/privacy requirements for the sender and recipient locations before real outreach; implement the required identity, opt-out, and suppression handling.

**Done when:** a draft cannot send without explicit approval, test mode is verified, and sent/replied/bounced/unsubscribed records remain consistent after restart.

### Phase 5 — Add website opportunity audits (optional product expansion)

Only start this after Phases 0–4 work. Integrate the existing `audit.py` behind a separate research substage, inspect its current checks and output, then define a stable structured audit schema. Add evidence, severity, confidence, and source URL for each finding. Calculate any score from documented weighted rules and avoid presenting it as an objective measure of business quality. Generate a report only after findings can be reproduced and reviewed. Do not adopt or combine third-party repos until their current maintenance, license, data handling, deployment cost, and integration surface have been checked against a specific gap.

**Done when:** a human can reproduce and verify each reported finding, and the audit provides useful evidence without blocking ordinary lead research.

## First implementation order

1. Add a controlled sample row and valid LLM credentials, then run the CLI to establish the baseline path.
2. Fix any failures revealed by that run, starting with CSV validation, configuration reporting, and per-stage error visibility.
3. Add focused tests for CSV parsing/deduplication, state transitions, LLM failure handling, and the Maps adapter's mocked API responses. Run them when test verification is requested as part of implementation.
4. Integrate and verify the local Maps service only if automated discovery is needed; otherwise keep using the CSV input.
5. Add dashboard approval and test-only sending controls before enabling real sending.
6. Integrate audits and reports only after the core workflow is stable.

## Acceptance checklist for the immediate milestone

- [ ] `leads_input.csv` has at least one valid, controlled test row.
- [ ] The run reports how many rows were read, accepted, skipped, and deduplicated.
- [ ] At least one lead reaches `ready_to_send` with research and a draft.
- [ ] Missing Maps/Apollo services are visible as skipped/unavailable sources, not mistaken for pipeline success.
- [ ] The one-shot pipeline does not send email.
- [ ] A failure on one lead does not block other leads, and the failure is visible/retryable.

## References checked

- The scraper's current README documents web mode (`-web`, `-addr`, `-data-folder`), REST job routes (`/api/v1/jobs`), and its CSV download route: [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper).
- Apollo's current docs say People API Search is available to eligible free accounts registered with a work email, while access is credential/plan dependent: [People API Search](https://docs.apollo.io/reference/people-api-search), [Authentication](https://docs.apollo.io/reference/authentication), and [Status Codes](https://docs.apollo.io/reference/status-codes).
- Google Maps Platform policies and terms govern use of official Maps Platform content; this project uses a third-party browser scraper, so those API policies are not by themselves a complete legal assessment of that scraper. Review the relevant source terms and jurisdictions before deployment: [Google Maps Platform policies](https://developers.google.com/maps/documentation/javascript/policies), [Terms](https://cloud.google.com/maps-platform/terms).
