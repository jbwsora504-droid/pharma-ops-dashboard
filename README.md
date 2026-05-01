# Pharma Ops Dashboard

**Healthcare Automation Monitoring with AI-Powered Incident Triage**

A Python-based operations tool that ingests system logs from pharmacy automation equipment, applies rule-based triage logic, and uses an LLM to generate plain-English incident summaries and recommended corrective actions — displayed on a real-time web dashboard.

Built from 11+ years of hands-on experience supporting robotic pharmacy dispensing systems across 100+ hospital deployments.

---

## The Problem This Solves

Pharmacy automation environments — robotic dispensers, conveyor systems, PLC-controlled medication workflows — generate constant telemetry. In production healthcare settings, a single undetected fault can halt medication delivery to patients. Operations teams need to:

- Detect failures fast across dozens of simultaneous system feeds
- Prioritize which incidents require immediate escalation vs. routine attention
- Communicate findings clearly to non-technical clinical and administrative stakeholders

Traditionally this is done manually, relying on engineers who know the systems deeply. This project automates the detection, triage, and communication layer.

---

## Architecture

```
Log Sources (simulated)
        │
        ▼
┌─────────────────┐
│  Log Ingestor   │  Parses structured/unstructured log entries
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Triage Engine  │  Rule-based severity scoring + pattern matching
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   AI Summarizer │  LLM prompt → plain-English summary + recommendation
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  SQL Log Store  │  SQLite incident history with audit trail
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Web Dashboard  │  FastAPI + HTML dashboard, live incident feed
└─────────────────┘
```

---

## Features

- Parses pharmacy automation log formats (dispensing errors, PLC faults, API timeouts, sensor alerts)
- Severity classification: CRITICAL / HIGH / MEDIUM / LOW
- AI-generated incident summaries with recommended corrective actions
- SQLite-backed incident history with full audit trail
- Real-time web dashboard with incident feed and status indicators
- Configurable alert thresholds per system type
- Sample synthetic dataset included for demo purposes

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Web Framework | FastAPI |
| Database | SQLite (via sqlite3) |
| AI Integration | Anthropic Claude API |
| Frontend | HTML / Vanilla JS (no build step) |
| Testing | pytest |
| Config | python-dotenv |

---

## Quickstart

```bash
# Clone the repo
git clone https://github.com/sora504/pharma-ops-dashboard.git
cd pharma-ops-dashboard

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Add your Anthropic API key to .env

# Run the dashboard
python main.py

# Visit http://localhost:8000
```

---

## Project Structure

```
pharma-ops-dashboard/
├── main.py                  # Entry point
├── requirements.txt
├── .env.example
├── data/
│   └── samples/             # Synthetic log files for demo
├── src/
│   ├── ingestor/            # Log parsing and normalization
│   ├── triage/              # Severity scoring and rule engine
│   ├── api/                 # AI summarization via Claude API
│   └── dashboard/           # FastAPI routes and HTML templates
├── tests/                   # pytest test suite
└── docs/                    # Architecture notes
```

---

## Sample Output

```
[CRITICAL] Dispenser Unit 4 — Carousel jam detected on slot 12
Severity Score: 95/100
AI Summary: Slot 12 on Dispenser Unit 4 has reported a mechanical jam
affecting carousel rotation. This will block all dispenses from the
affected carousel until cleared. Recommend immediate physical inspection
of slot 12 and surrounding slots for foreign object obstruction or
misaligned canister seating. Escalate to on-site technician if not
resolved within 10 minutes given active medication queue.

[HIGH] PLC Fault — Allen-Bradley Unit 2, Fault Code E-47
Severity Score: 78/100
AI Summary: Fault Code E-47 indicates a motor overload condition on
Unit 2. System has entered safe-stop state. Check drive temperature and
verify conveyor load. Reset fault via Studio 5000 after inspection.
```

---

## Background

This project is based on real operational patterns from pharmacy automation deployments in hospital environments. All log data included in `/data/samples/` is fully synthetic and contains no PHI or proprietary system information.

---

## License

MIT
