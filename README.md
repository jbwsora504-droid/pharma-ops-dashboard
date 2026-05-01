# Pharma Ops Dashboard

An AI-augmented incident monitoring system for pharmacy automation environments. Raw equipment logs flow through a structured pipeline that ingests multiple formats, scores severity with domain-aware rules, generates plain-English incident narratives via Claude, and surfaces them on a live operations dashboard with acknowledgment workflow.

Built as a focused exploration of how AI summarization can sit alongside deterministic rule engines in a regulated healthcare automation environment, drawing on eleven years of hands-on experience with pharmacy automation hardware and software at Omnicell.

## Why this exists

Pharmacy automation systems generate continuous streams of operational data. Dispenser carousel events, PLC fault codes, API integration errors, and environmental sensor alerts all flow through proprietary tooling that often does not surface the signal well. A pharmacy ops lead is frequently the one piecing together "is something wrong on Dispenser 4 today" from several disconnected systems.

This project explores a different shape:

1. Multi-format log streams are normalized into a single internal representation.
2. A rule-based triage engine assigns each event a severity score grounded in real operational impact.
3. An AI layer generates a plain-English incident summary, recommended action, and impact assessment for each prioritized event.
4. A live dashboard presents the result with acknowledgment tracking, ordered by severity score.

The goal was to keep my Python and applied AI skills sharp during my transition from in-house engineering at Omnicell into independent contracting and AI-focused roles, while building something that reflects the actual operational problems I worked on for a decade.

## Architecture

```
Pharmacy logs  ->  Ingestor  ->  Triage engine  ->  AI summarizer  ->  SQLite store  ->  Dashboard
   (multi-format)    (normalize)    (score 0-100)    (Claude API)        (audit trail)    (HTML/JS)
```

| Component | Path | Role |
|-----------|------|------|
| Ingestor | `src/ingestor/ingestor.py` | Parses dispenser, PLC, API, and sensor logs across JSON, plain text, and CSV formats. Returns a normalized `LogEntry` dataclass with source classification, system ID, event code, timestamp, and metadata. |
| Triage engine | `src/triage/triage.py` | Applies 25+ weighted scoring rules to each entry. Produces a `TriageResult` with a 0-to-100 score, severity tier (CRITICAL, HIGH, MEDIUM, LOW), matched rule names, and an immediate-escalation flag. |
| AI summarizer | `src/api/summarizer.py` | Calls the Anthropic Claude API with a structured prompt to generate a short title, plain-English summary, recommended corrective action, and impact assessment. Falls back to a rule-based summary on API failure so the dashboard never goes dark. |
| Persistence | `src/dashboard/database.py` | SQLite-backed incident store with WAL mode, append-only design, and acknowledgment tracking. Indexed by severity, system ID, creation time, and acknowledgment state. |
| API and UI | `src/dashboard/routes.py` | FastAPI app exposing the dashboard and REST endpoints. Self-contained HTML/CSS/JS UI with severity-coded incident cards, real-time stats, and an acknowledge action. |
| Tests | `tests/test_pipeline.py` | Unit tests covering parser format detection, source-specific pattern matching, triage scoring, severity tiers, and batch ordering. |

## Domain coverage

The triage rules encode operational patterns from real pharmacy automation environments:

- **Dispenser events.** Carousel jams, slot misfills, canister depletion, wrong-drug detection. These weight heavily because they directly block patient medication queues.
- **PLC faults.** Allen-Bradley-style fault codes including motor overload, e-stop activation, communication loss with conveyor controllers, and safe-stop conditions.
- **API integrations.** Timeouts, authentication failures, 500-class errors on healthcare endpoints, and repeated-failure escalation.
- **Environmental.** Temperature threshold breaches, humidity excursions, and access door events on regulated storage areas.
- **Cross-source modifiers.** Patient-impact keywords, data integrity and 21 CFR Part 11 audit terms, offline-system signals, overnight timing (when on-site staffing is lower), and repeated-event escalation.

Severity thresholds are configurable via environment variables (`TRIAGE_CRITICAL_THRESHOLD`, `TRIAGE_HIGH_THRESHOLD`, `TRIAGE_MEDIUM_THRESHOLD`) so the same engine can be tuned for different operational risk postures.

## Tech stack

- Python 3.11+, FastAPI 0.111, Uvicorn 0.30
- Anthropic Python SDK
- SQLite with WAL mode for the persistence layer
- Pytest 8 for the test suite
- Vanilla HTML, CSS, and JavaScript for the dashboard UI (no framework dependency)
- python-dotenv for environment configuration

## Quick start

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Open .env and set ANTHROPIC_API_KEY

# 4. Add log samples to the ingestion directory
mkdir -p data/samples
# Drop .log, .txt, .json, or .csv files into data/samples/

# 5. Run the dashboard
python main.py
```

Open `http://localhost:8000` in a browser. Click **Run Ingestion** to process the sample directory through the full pipeline. Incidents will populate the dashboard ordered by severity score.

## REST API

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/` | Live HTML dashboard. |
| GET | `/api/incidents` | Active (unacknowledged) incidents, ordered by severity score. |
| GET | `/api/incidents/history` | Full incident history with optional `system_id` and `severity` filters. |
| GET | `/api/stats` | Aggregate counts for the status bar. |
| POST | `/api/incidents/{incident_id}/acknowledge` | Mark an incident as acknowledged. |
| POST | `/api/ingest` | Trigger a full pipeline run on the sample log directory. |

Interactive API docs are available at `/docs` (FastAPI auto-generated Swagger UI).

## Running the tests

```bash
pytest tests/ -v
```

Tests cover the ingestor's pattern matching across all four log sources and three formats, the triage engine's severity scoring and rule matching, and batch ordering by score.

## What I explored

- Designing a clean separation between deterministic rules (fast, auditable, narrow) and LLM reasoning (flexible, contextual, harder to validate). The triage engine produces a structured context payload that the AI consumes, so the AI never sees raw logs and the rules layer is independently testable.
- Enforcing JSON schema on LLM output for reliable downstream parsing, including a graceful markdown-fence stripping path for cases where a model wraps its response despite instructions.
- Failure-isolated AI integration. A single API timeout or parse error never halts the dashboard; the system falls back to a rule-based summary with the error captured for diagnostic purposes.
- Append-only persistence patterns that align with 21 CFR Part 11 audit expectations, even in a sandbox project that does not need to claim compliance.
- FastAPI patterns for keeping route handlers thin, with all the meaningful logic pushed into the ingestor, triage, summarizer, and database modules.

## Future improvements

This is an exploration project, not a production system. Things I would change before considering it deployment-ready:

- **Update the Claude model identifier.** `summarizer.py` pins `claude-sonnet-4-20250514`, which is deprecated. The replacement is `claude-sonnet-4-6` or the current Sonnet alias.
- **Move from `@app.on_event` to FastAPI lifespan handlers.** The startup hook in `routes.py` uses the older pattern that is being phased out.
- **Replace `datetime.utcnow()` with `datetime.now(timezone.utc)`.** The former is deprecated as of Python 3.12.
- **Add an evaluation harness** that scores the AI summaries against a curated set of ground-truth incidents, so model changes can be measured rather than trusted.
- **Add authentication and role-based access** before any deployment touching real operational data.
- **Replace SQLite with a time-series store** if log volumes grow beyond what SQLite handles cleanly.
- **Stream ingestion** rather than batch processing on a directory, with proper backpressure and at-least-once delivery semantics.

## Author

**Jeron Williams.** Senior Robotics and Automation Engineer with over eleven years at Omnicell, working on pharmacy automation, robotics, SCADA and PLC integration, REST API troubleshooting, and 21 CFR Part 11 compliant systems. Currently focused on AI engineering and applied machine learning, available for remote and hybrid contract or full-time roles.

LinkedIn: [linkedin.com/in/jeron-williams-54631a86](https://linkedin.com/in/jeron-williams-54631a86)
Email: jbw.sora504@gmail.com
