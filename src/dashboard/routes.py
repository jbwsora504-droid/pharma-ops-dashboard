"""
routes.py
---------
FastAPI application exposing the ops dashboard and REST API.

Endpoints:
  GET  /                    Web dashboard (HTML)
  GET  /api/incidents        Active incidents (JSON)
  GET  /api/incidents/history  Full history with optional filters
  GET  /api/stats            Summary statistics
  POST /api/incidents/{id}/acknowledge  Acknowledge an incident
  POST /api/ingest           Trigger ingestion of sample log directory
"""

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.ingestor.ingestor import ingest_directory
from src.triage.triage import triage_batch
from src.api.summarizer import summarize_batch
from src.dashboard.database import (
    initialize_db, save_incident,
    get_active_incidents, get_incident_history,
    get_summary_stats, acknowledge_incident,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Pharma Ops Dashboard",
    description="Healthcare automation monitoring with AI-powered incident triage",
    version="1.0.0",
)


@app.on_event("startup")
def startup():
    initialize_db()
    logger.info("Pharma Ops Dashboard started")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class AcknowledgeRequest(BaseModel):
    acknowledged_by: str = "operator"


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/incidents", tags=["incidents"])
def list_active_incidents(limit: int = Query(100, ge=1, le=500)):
    """Return all unacknowledged incidents sorted by severity score."""
    return get_active_incidents(limit=limit)


@app.get("/api/incidents/history", tags=["incidents"])
def incident_history(
    system_id: str = Query(None),
    severity: str = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """Return incident history with optional system_id and severity filters."""
    return get_incident_history(system_id=system_id, severity=severity, limit=limit)


@app.get("/api/stats", tags=["dashboard"])
def dashboard_stats():
    """Return aggregate counts for the status bar."""
    return get_summary_stats()


@app.post("/api/incidents/{incident_id}/acknowledge", tags=["incidents"])
def ack_incident(incident_id: str, body: AcknowledgeRequest):
    """Acknowledge an incident, removing it from the active view."""
    updated = acknowledge_incident(incident_id, body.acknowledged_by)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Incident not found: {incident_id}")
    return {"status": "acknowledged", "incident_id": incident_id}


@app.post("/api/ingest", tags=["pipeline"])
def run_ingestion(log_dir: str = Query(None)):
    """
    Trigger ingestion of a log directory through the full pipeline.
    Defaults to the LOG_SAMPLE_DIR environment variable.
    """
    directory = log_dir or os.getenv("LOG_SAMPLE_DIR", "data/samples")
    path = Path(directory)
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"Directory not found: {directory}")

    entries = ingest_directory(path)
    if not entries:
        return {"status": "no_entries", "processed": 0}

    triage_results = triage_batch(entries)
    summaries = summarize_batch(triage_results)

    saved = 0
    for summary, triage_result in zip(summaries, triage_results):
        entry = triage_result.entry
        save_incident(
            summary,
            raw_log=entry.raw,
            source=entry.source.value,
            event_code=entry.event_code,
            log_timestamp=entry.timestamp.isoformat(),
        )
        saved += 1

    return {
        "status": "complete",
        "processed": len(entries),
        "saved": saved,
        "critical": sum(1 for s in summaries if s.severity == "CRITICAL"),
        "high": sum(1 for s in summaries if s.severity == "HIGH"),
    }


# ---------------------------------------------------------------------------
# Web dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, tags=["dashboard"])
def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pharma Ops Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #0f1117; color: #e2e8f0; min-height: 100vh; }
  header { background: #1a1d2e; border-bottom: 2px solid #1b4f8a; padding: 16px 32px; display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 1.3rem; font-weight: 700; color: #fff; letter-spacing: 0.5px; }
  header span { font-size: 0.8rem; color: #64748b; }
  .status-bar { display: flex; gap: 16px; padding: 16px 32px; background: #13151f; }
  .stat-card { background: #1a1d2e; border-radius: 8px; padding: 14px 20px; flex: 1; border-left: 4px solid #334155; }
  .stat-card.critical { border-color: #ef4444; }
  .stat-card.high { border-color: #f97316; }
  .stat-card.medium { border-color: #eab308; }
  .stat-card.low { border-color: #22c55e; }
  .stat-card .count { font-size: 2rem; font-weight: 700; }
  .stat-card .label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }
  .critical .count { color: #ef4444; }
  .high .count { color: #f97316; }
  .medium .count { color: #eab308; }
  .low .count { color: #22c55e; }
  .toolbar { padding: 16px 32px; display: flex; gap: 12px; align-items: center; }
  .toolbar button { background: #1b4f8a; color: #fff; border: none; padding: 8px 18px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; font-weight: 600; }
  .toolbar button:hover { background: #2563ab; }
  .toolbar button.secondary { background: #1e293b; }
  .toolbar button.secondary:hover { background: #334155; }
  #incident-list { padding: 0 32px 32px; display: flex; flex-direction: column; gap: 12px; }
  .incident-card { background: #1a1d2e; border-radius: 10px; padding: 18px 22px; border-left: 5px solid #334155; position: relative; }
  .incident-card.CRITICAL { border-color: #ef4444; }
  .incident-card.HIGH { border-color: #f97316; }
  .incident-card.MEDIUM { border-color: #eab308; }
  .incident-card.LOW { border-color: #22c55e; }
  .card-header { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
  .badge { font-size: 0.72rem; font-weight: 700; padding: 3px 10px; border-radius: 20px; text-transform: uppercase; letter-spacing: 0.5px; }
  .badge.CRITICAL { background: #7f1d1d; color: #fca5a5; }
  .badge.HIGH { background: #7c2d12; color: #fdba74; }
  .badge.MEDIUM { background: #713f12; color: #fde047; }
  .badge.LOW { background: #14532d; color: #86efac; }
  .score-pill { background: #0f172a; color: #94a3b8; font-size: 0.72rem; padding: 3px 9px; border-radius: 20px; }
  .system-id { font-size: 0.8rem; color: #60a5fa; font-family: monospace; }
  .card-title { font-size: 1rem; font-weight: 600; color: #f1f5f9; margin-bottom: 8px; }
  .card-summary { font-size: 0.85rem; color: #94a3b8; line-height: 1.6; margin-bottom: 10px; }
  .card-action { font-size: 0.82rem; color: #cbd5e1; background: #0f172a; border-radius: 6px; padding: 10px 14px; line-height: 1.7; white-space: pre-wrap; }
  .card-footer { display: flex; align-items: center; justify-content: space-between; margin-top: 12px; }
  .card-footer .timestamp { font-size: 0.73rem; color: #475569; }
  .ack-btn { background: #1e293b; color: #94a3b8; border: 1px solid #334155; padding: 5px 14px; border-radius: 6px; cursor: pointer; font-size: 0.78rem; }
  .ack-btn:hover { background: #334155; color: #e2e8f0; }
  .escalate-tag { font-size: 0.7rem; background: #450a0a; color: #fca5a5; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
  .empty-state { text-align: center; padding: 60px; color: #475569; }
  .empty-state h2 { font-size: 1.1rem; margin-bottom: 8px; }
  .loading { text-align: center; padding: 40px; color: #475569; }
</style>
</head>
<body>

<header>
  <h1>&#9679; Pharma Ops Dashboard</h1>
  <span id="last-refresh">Loading...</span>
</header>

<div class="status-bar">
  <div class="stat-card critical"><div class="count" id="stat-critical">—</div><div class="label">Critical Active</div></div>
  <div class="stat-card high"><div class="count" id="stat-high">—</div><div class="label">High Active</div></div>
  <div class="stat-card medium"><div class="count" id="stat-medium">—</div><div class="label">Medium Active</div></div>
  <div class="stat-card low"><div class="count" id="stat-low">—</div><div class="label">Low Active</div></div>
</div>

<div class="toolbar">
  <button onclick="runIngestion()">&#8635; Run Ingestion</button>
  <button class="secondary" onclick="loadIncidents()">Refresh</button>
</div>

<div id="incident-list"><div class="loading">Loading incidents...</div></div>

<script>
async function loadStats() {
  const r = await fetch('/api/stats');
  const s = await r.json();
  document.getElementById('stat-critical').textContent = s.critical_active ?? 0;
  document.getElementById('stat-high').textContent = s.high_active ?? 0;
  document.getElementById('stat-medium').textContent = s.medium_active ?? 0;
  document.getElementById('stat-low').textContent = s.low_active ?? 0;
}

async function loadIncidents() {
  document.getElementById('last-refresh').textContent = 'Refreshed: ' + new Date().toLocaleTimeString();
  const r = await fetch('/api/incidents?limit=50');
  const incidents = await r.json();
  const list = document.getElementById('incident-list');
  if (!incidents.length) {
    list.innerHTML = '<div class="empty-state"><h2>No active incidents</h2><p>All systems nominal or all incidents acknowledged.</p></div>';
    return;
  }
  list.innerHTML = incidents.map(inc => `
    <div class="incident-card ${inc.severity}" id="card-${inc.incident_id}">
      <div class="card-header">
        <span class="badge ${inc.severity}">${inc.severity}</span>
        <span class="score-pill">Score: ${inc.score}/100</span>
        <span class="system-id">${inc.system_id}</span>
        ${inc.escalate_immediately ? '<span class="escalate-tag">&#9888; Escalate Now</span>' : ''}
      </div>
      <div class="card-title">${inc.short_title}</div>
      <div class="card-summary">${inc.summary}</div>
      <div class="card-action">${inc.recommended_action}</div>
      <div class="card-footer">
        <span class="timestamp">${inc.incident_id} &nbsp;|&nbsp; ${new Date(inc.created_at).toLocaleString()}</span>
        <button class="ack-btn" onclick="acknowledge('${inc.incident_id}')">Acknowledge</button>
      </div>
    </div>
  `).join('');
}

async function acknowledge(id) {
  await fetch('/api/incidents/' + id + '/acknowledge', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({acknowledged_by: 'operator'}) });
  document.getElementById('card-' + id).style.opacity = '0.3';
  setTimeout(() => { loadIncidents(); loadStats(); }, 600);
}

async function runIngestion() {
  const btn = event.target;
  btn.textContent = 'Running...';
  btn.disabled = true;
  try {
    const r = await fetch('/api/ingest', { method: 'POST' });
    const result = await r.json();
    alert('Ingestion complete. Processed: ' + result.processed + ' | Critical: ' + result.critical + ' | High: ' + result.high);
    loadIncidents();
    loadStats();
  } catch(e) {
    alert('Ingestion failed: ' + e.message);
  }
  btn.textContent = '↻ Run Ingestion';
  btn.disabled = false;
}

loadStats();
loadIncidents();
setInterval(() => { loadStats(); loadIncidents(); }, 30000);
</script>
</body>
</html>"""
